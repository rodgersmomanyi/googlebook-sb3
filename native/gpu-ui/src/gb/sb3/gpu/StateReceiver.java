package gb.sb3.gpu;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.Status;

/**
 * Receives gb_power's state broadcast (INTERFACES 10.7, 12.1: {@code am broadcast --user <u> --include-stopped-packages
 * -a gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu}, sent by gb_notify as the shell user; wave 6: once per running
 * full user, never the headless system user). The manifest requires the sender to hold DUMP, so only shell and root
 * can send it. The intent carries no data. On each broadcast (v1.2): query `status`; update the notifications (in use,
 * detach, 12.3) and the Settings summary; ask SystemUI to refresh the active tile; declare `ui-caps notify|none`
 * (27.3); push the app catalog when `apps_sync` is 0 (27.5). (Wave 6) An instance in the headless system user ignores
 * it ({@link gb.sb3.gpu.core.UserPolicy}); {@link UserReceiver} runs the same refresh when a user signs in.
 */
public final class StateReceiver extends BroadcastReceiver {
    static final String ACTION = "gb.sb3.gpu.action.STATE_CHANGED";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION.equals(intent.getAction())) return;
        final Context app = context.getApplicationContext();
        if (!Gb.actsOnState(app)) {
            Log.i(Gb.TAG, "headless system user: state broadcast ignored");
            return;
        }
        final PendingResult pr = goAsync();
        Gb.io(new Runnable() {
            @Override
            public void run() {
                try {
                    handle(app);
                } finally {
                    pr.finish();
                }
            }
        });
    }

    static void handle(Context app) {
        Status s = Gb.CLIENT.status().status();
        Gb.publish(app, s);
        Gb.requestTileUpdate(app);
        if (s == null) return;
        GbPowerClient.Outcome caps = Gb.CLIENT.call("ui-caps", Notifier.uiCaps(app));
        if (!caps.ok()) Log.i(Gb.TAG, "ui-caps: " + caps);
        if (s.appsSync == 0) Gb.pushCatalog(app);
    }
}
