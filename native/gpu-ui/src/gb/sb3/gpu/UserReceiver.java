package gb.sb3.gpu;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * (Wave 6, notify) {@code BOOT_COMPLETED}, which the system sends to each user when that user is unlocked: on lapis
 * (headless system user mode) that is the person's sign-in. gb_notify sends gb_power's state broadcast only to the
 * full users that run at that moment (INTERFACES 12.1), so a user that starts later refreshes itself here exactly as
 * {@link StateReceiver} does: status, notifications (12.3), the tile, {@code ui-caps}, the app catalog when
 * {@code apps_sync} is 0. Not exported: only the system reaches it (the action is a protected broadcast).
 */
public final class UserReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        final Context app = context.getApplicationContext();
        if (!Gb.actsOnState(app)) {
            Log.i(Gb.TAG, "headless system user: no refresh at BOOT_COMPLETED");
            return;
        }
        Log.i(Gb.TAG, "user unlocked: refreshing state");
        final PendingResult pr = goAsync();
        Gb.io(new Runnable() {
            @Override
            public void run() {
                try {
                    StateReceiver.handle(app);
                } finally {
                    pr.finish();
                }
            }
        });
    }
}
