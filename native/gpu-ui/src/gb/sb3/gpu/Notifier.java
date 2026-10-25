package gb.sb3.gpu;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.drawable.Icon;
import android.provider.Settings;
import android.util.Log;

import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.Holder;
import gb.sb3.gpu.core.HoldersModel;
import gb.sb3.gpu.core.Json;
import gb.sb3.gpu.core.NotifModel;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * GbGpu's own notifications (INTERFACES 12.3 v1.2, 27.3): the ongoing "in use" notification (channel gb_gpu_status,
 * id 1, action Close apps), alerts about the results of UI actions (gb_gpu_alerts, id 2) and the actionable detach
 * notification (gb_gpu_detach, id 3: Close apps and detach / Cancel) that replaces gb_power's N08 once the app has sent
 * `ui-caps notify`. The other daemon notifications (N01-N20) are never duplicated here.
 */
final class Notifier {
    private Notifier() {
    }

    static final String PREFS = "gb_gpu";
    static final String KEY_SEEN = "detach_ref_seen";
    static final String KEY_BOOT = "detach_ref_boot";

    /** Text currently shown in the in-use notification (null = cancelled or unknown after process start). */
    private static String shownInUse;
    private static boolean inUseKnown;

    static void ensureChannels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        NotificationChannel status = new NotificationChannel(Texts.CH_STATUS_ID, Texts.CH_STATUS_NAME,
                NotificationManager.IMPORTANCE_LOW);
        status.setShowBadge(false);
        NotificationChannel alerts = new NotificationChannel(Texts.CH_ALERTS_ID, Texts.CH_ALERTS_NAME,
                NotificationManager.IMPORTANCE_HIGH);
        NotificationChannel detach = new NotificationChannel(Texts.CH_DETACH_ID, Texts.CH_DETACH_NAME,
                NotificationManager.IMPORTANCE_HIGH);
        nm.createNotificationChannels(Arrays.asList(status, alerts, detach));
    }

    static PendingIntent holders(Context c, HoldersModel.Mode m, int requestCode) {
        Intent i = new Intent(c, HoldersActivity.class)
                .putExtra(HoldersActivity.EXTRA_MODE, m.extra())
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return PendingIntent.getActivity(c, requestCode, i, PendingIntent.FLAG_IMMUTABLE
                | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Posts, updates or cancels the in-use notification for a status (null = unavailable). */
    static synchronized void update(Context c, Status s) {
        String[] m = NotifModel.inUse(s, Gb.labeler(c));
        String text = m == null ? null : m[1];
        if (inUseKnown && (text == null ? shownInUse == null : text.equals(shownInUse))) return;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        if (m == null) {
            nm.cancel(Texts.NOTIF_IN_USE);
            shownInUse = null;
            inUseKnown = true;
            return;
        }
        if (!nm.areNotificationsEnabled()) return; // POST_NOTIFICATIONS not granted (yet): try again next time
        ensureChannels(c);
        Notification note = new Notification.Builder(c, Texts.CH_STATUS_ID)
                .setSmallIcon(R.drawable.ic_gpu)
                .setContentTitle(m[0])
                .setContentText(m[1])
                .setStyle(new Notification.BigTextStyle().bigText(m[1]))
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_STATUS)
                .setContentIntent(holders(c, HoldersModel.Mode.CLOSE, 0))
                .addAction(new Notification.Action.Builder(Icon.createWithResource(c, R.drawable.ic_gpu),
                        Texts.ACTION_CLOSE_APPS, holders(c, HoldersModel.Mode.CLOSE, 0)).build())
                .build();
        nm.notify(Texts.NOTIF_IN_USE, note);
        shownInUse = text;
        inUseKnown = true;
    }

    /** {@code Settings.Global.BOOT_COUNT}: {@code detach_ref} is a per-boot counter, so the stored value is per boot. */
    private static int bootCount(Context c) {
        try {
            return Settings.Global.getInt(c.getContentResolver(), Settings.Global.BOOT_COUNT, -1);
        } catch (RuntimeException e) {
            return -1;
        }
    }

    /**
     * 12.3 / 27.3: post notification id 3 when {@code detach_ref} grew beyond the value seen last (fetching a fresh
     * `list-holders`), cancel it when the base is gone or nouveau is no longer busy. Background thread only.
     */
    static synchronized void detach(Context c, Status s) {
        if (s == null) return;
        SharedPreferences p = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        int boot = bootCount(c);
        boolean sameBoot = boot >= 0 && p.getInt(KEY_BOOT, -2) == boot;
        int seen = p.getInt(KEY_SEEN, 0);
        NotifModel.Detach d = NotifModel.detach(s, seen, sameBoot);
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm != null && d.cancel) nm.cancel(Texts.NOTIF_DETACH);
        if (nm != null && d.post) postDetach(c, nm, s);
        if (d.seen != seen || !sameBoot) p.edit().putInt(KEY_SEEN, d.seen).putInt(KEY_BOOT, boot).apply();
    }

    private static void postDetach(Context c, NotificationManager nm, Status s) {
        if (!nm.areNotificationsEnabled()) return;
        List<Holder> hs = new ArrayList<Holder>();
        GbPowerClient.Outcome o = Gb.CLIENT.call("list-holders");
        if (o.ok()) {
            for (Object e : Json.list(o.reply.obj, "holders")) {
                Holder h = Holder.from(e);
                if (h != null) hs.add(h);
            }
        } else {
            hs.addAll(s.holders);
        }
        String[] m = NotifModel.detachText(hs, Gb.labeler(c), StateMapper.busyCount(s.state));
        ensureChannels(c);
        Intent cancel = new Intent(c, ActionReceiver.class).setAction(ActionReceiver.ACTION_CANCEL_DETACH);
        PendingIntent cancelPi = PendingIntent.getBroadcast(c, 3, cancel, PendingIntent.FLAG_IMMUTABLE
                | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification note = new Notification.Builder(c, Texts.CH_DETACH_ID)
                .setSmallIcon(R.drawable.ic_detach)
                .setContentTitle(m[0])
                .setContentText(m[1])
                .setStyle(new Notification.BigTextStyle().bigText(m[1]))
                .setCategory(Notification.CATEGORY_STATUS)
                .setAutoCancel(true)
                .setContentIntent(holders(c, HoldersModel.Mode.DETACH, 3))
                .addAction(new Notification.Action.Builder(Icon.createWithResource(c, R.drawable.ic_detach),
                        Texts.ACTION_CLOSE_AND_DETACH,
                        holders(c, HoldersModel.Mode.DETACH, 3)).build())
                .addAction(new Notification.Action.Builder(Icon.createWithResource(c, R.drawable.ic_detach),
                        Texts.ACTION_CANCEL, cancelPi).build())
                .build();
        nm.notify(Texts.NOTIF_DETACH, note);
        Log.i(Gb.TAG, "detach refused (detach_ref " + s.detachRef + "): " + m[1]);
    }

    static void cancelDetach(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm != null) nm.cancel(Texts.NOTIF_DETACH);
    }

    /** 27.3: what to declare with `ui-caps`. */
    static String uiCaps(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return "none";
        ensureChannels(c);
        NotificationChannel ch = nm.getNotificationChannel(Texts.CH_DETACH_ID);
        boolean blocked = ch == null || ch.getImportance() == NotificationManager.IMPORTANCE_NONE;
        return NotifModel.uiCaps(nm.areNotificationsEnabled(), blocked);
    }

    /** An alert about a UI action's result (e.g. `close-holders` refused). */
    static void alert(Context c, String title, String text) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null || !nm.areNotificationsEnabled()) {
            Gb.toast(c, text);
            return;
        }
        ensureChannels(c);
        Notification note = new Notification.Builder(c, Texts.CH_ALERTS_ID)
                .setSmallIcon(R.drawable.ic_gpu)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_ERROR)
                .build();
        nm.notify(Texts.NOTIF_ALERT, note);
    }
}
