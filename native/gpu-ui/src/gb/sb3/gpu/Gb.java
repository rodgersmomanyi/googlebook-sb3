package gb.sb3.gpu;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Handler;
import android.os.Looper;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;
import android.util.Log;
import android.widget.Toast;

import gb.sb3.gpu.core.CatalogBuilder;
import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.Labels;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;
import gb.sb3.gpu.core.Transport;
import gb.sb3.gpu.core.WatchSession;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicBoolean;

/** Process-wide helpers: the gb_power client, background execution, toasts, labels, the catalog and state fan-out. */
final class Gb {
    private Gb() {
    }

    static final String TAG = "GbGpu";

    static final Transport.Factory FACTORY = new Transport.Factory() {
        @Override
        public Transport create() {
            return new LocalSocketTransport(GbPowerClient.SOCKET_PATH);
        }
    };

    static final GbPowerClient CLIENT = new GbPowerClient(FACTORY, GbPowerClient.DEFAULT_TIMEOUT_MS);

    private static final ExecutorService IO = Executors.newCachedThreadPool(new ThreadFactory() {
        @Override
        public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "gb-io");
            t.setDaemon(true);
            return t;
        }
    });

    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    /** The newest status any component saw (null = unknown or unavailable). */
    private static volatile Status lastStatus;
    private static String lastSummary;
    private static final AtomicBoolean PUSHING = new AtomicBoolean();

    interface Done {
        void done(GbPowerClient.Outcome o);
    }

    static Status lastStatus() {
        return lastStatus;
    }

    /**
     * Runs one gb_power command off the main thread. {@code done} runs on the main thread exactly once, with a
     * TIMEOUT outcome if the call has not returned shortly after the client deadline.
     */
    static void async(final String cmd, final String arg, final Context ctx, final Done done) {
        final AtomicBoolean fired = new AtomicBoolean();
        final Runnable timeout = new Runnable() {
            @Override
            public void run() {
                if (fired.compareAndSet(false, true)) {
                    done.done(GbPowerClient.Outcome.fail(GbPowerClient.Failure.TIMEOUT, "ui deadline"));
                }
            }
        };
        MAIN.postDelayed(timeout, CLIENT.timeoutMs() + 500L);
        final Context app = ctx == null ? null : ctx.getApplicationContext();
        IO.execute(new Runnable() {
            @Override
            public void run() {
                final GbPowerClient.Outcome o = CLIENT.call(cmd, arg);
                if ("status".equals(cmd) && app != null) publish(app, o.status());
                MAIN.post(new Runnable() {
                    @Override
                    public void run() {
                        MAIN.removeCallbacks(timeout);
                        if (fired.compareAndSet(false, true)) done.done(o);
                    }
                });
            }
        });
    }

    static void io(Runnable r) {
        IO.execute(r);
    }

    static void main(Runnable r) {
        MAIN.post(r);
    }

    static void toast(final Context c, final String text) {
        final Context app = c.getApplicationContext();
        MAIN.post(new Runnable() {
            @Override
            public void run() {
                Toast.makeText(app, text, Toast.LENGTH_SHORT).show();
            }
        });
    }

    /** INTERFACES 27.1: the toast for a `set-mode` effect (none for "now"); a failure gives a short reason. */
    static void reportMode(Context c, String cmd, GbPowerClient.Outcome o) {
        if (o.ok()) {
            String t = StateMapper.effectToast(o.reply.str("effect"));
            if (t != null) toast(c, t);
        } else {
            Log.i(TAG, cmd + ": " + o);
            toast(c, Texts.error(cmd, o));
        }
    }

    static WatchSession.Options watchOptions(long maxLifeMs) {
        WatchSession.Options o = new WatchSession.Options();
        o.maxLifeMs = maxLifeMs;
        return o;
    }

    static int tileState(StateMapper.TileState s) {
        switch (s) {
            case ACTIVE:
                return Tile.STATE_ACTIVE;
            case INACTIVE:
                return Tile.STATE_INACTIVE;
            default:
                return Tile.STATE_UNAVAILABLE;
        }
    }

    /**
     * Records a new status (null = unavailable) and fans it out: the in-use notification, the detach notification
     * (27.3, on a background thread: it may fetch `list-holders`) and, when the injected summary changed, Settings.
     * Safe on any thread.
     */
    static void publish(final Context c, final Status s) {
        boolean summaryChanged;
        synchronized (Gb.class) {
            lastStatus = s;
            String sum = StateMapper.summary(s);
            summaryChanged = !sum.equals(lastSummary);
            lastSummary = sum;
        }
        Notifier.update(c, s);
        final Context app = c.getApplicationContext();
        IO.execute(new Runnable() {
            @Override
            public void run() {
                Notifier.detach(app, s);
            }
        });
        if (summaryChanged) SwitchProvider.notifyChanged(c);
    }

    /** Asks SystemUI to bind the (active) Graphics tile so it can refresh itself. */
    static void requestTileUpdate(Context c) {
        try {
            TileService.requestListeningState(c, new ComponentName(c, GpuTileService.class));
        } catch (RuntimeException e) {
            Log.i(TAG, "requestListeningState: " + e);
        }
    }

    /** App labels through PackageManager (QUERY_ALL_PACKAGES makes every package visible); cached per labeler. */
    static Labels.Labeler labeler(Context c) {
        final PackageManager pm = c.getPackageManager();
        final Map<String, String> cache = new HashMap<String, String>();
        return new Labels.Labeler() {
            @Override
            public String label(String pkg) {
                if (cache.containsKey(pkg)) return cache.get(pkg);
                String l = null;
                try {
                    ApplicationInfo ai = pm.getApplicationInfo(pkg, PackageManager.ApplicationInfoFlags.of(0));
                    CharSequence cs = pm.getApplicationLabel(ai);
                    if (cs != null && cs.length() > 0) l = cs.toString();
                } catch (PackageManager.NameNotFoundException e) {
                    // not installed (any more): show the package name
                }
                cache.put(pkg, l);
                return l;
            }
        };
    }

    /** Every launchable activity's application of this user (ACTION_MAIN + CATEGORY_LAUNCHER, INTERFACES 24.3). */
    static List<ResolveInfo> launchable(Context c) {
        Intent main = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        return c.getPackageManager().queryIntentActivities(main, PackageManager.ResolveInfoFlags.of(0));
    }

    /** The app catalog (INTERFACES 24.3) from {@link #launchable}. */
    static List<CatalogBuilder.Entry> catalog(Context c, List<ResolveInfo> ris) {
        PackageManager pm = c.getPackageManager();
        List<CatalogBuilder.App> apps = new ArrayList<CatalogBuilder.App>();
        for (ResolveInfo ri : ris) {
            if (ri.activityInfo == null || ri.activityInfo.applicationInfo == null) continue;
            ApplicationInfo ai = ri.activityInfo.applicationInfo;
            CharSequence l = ai.loadLabel(pm);
            apps.add(new CatalogBuilder.App(ai.packageName, l == null ? null : l.toString(), ai.flags, ai.category));
        }
        return CatalogBuilder.build(apps);
    }

    /**
     * INTERFACES 27.5: pushes the catalog on one connection (`apps-begin`, `apps-add` lines, `apps-commit`). Runs on
     * the calling (background) thread; a second push while one runs is skipped. Returns true when gb_power committed
     * it; a failure is retried at the next broadcast.
     */
    static boolean pushCatalog(Context c) {
        if (!PUSHING.compareAndSet(false, true)) return false;
        try {
            List<String> lines = CatalogBuilder.lines(catalog(c, launchable(c)));
            GbPowerClient.Batch b = CLIENT.exchangeAll(lines, GbPowerClient.BATCH_TIMEOUT_MS);
            boolean ok = b.ok(lines.size());
            Log.i(TAG, "app catalog push (" + lines.size() + " lines): " + (ok ? "committed " + b.last() : b));
            return ok;
        } catch (RuntimeException e) {
            Log.w(TAG, "app catalog push: " + e);
            return false;
        } finally {
            PUSHING.set(false);
        }
    }
}
