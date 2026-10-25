package gb.sb3.gpu;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * The detach notification's <b>Cancel</b> action (INTERFACES 12.3, 13.2 v1.2): it only dismisses notification id 3 (the
 * EC's detach request already ended at gb_power's refusal, 23.9). Not exported: only our own PendingIntent reaches it.
 */
public final class ActionReceiver extends BroadcastReceiver {
    static final String ACTION_CANCEL_DETACH = "gb.sb3.gpu.action.CANCEL_DETACH";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION_CANCEL_DETACH.equals(intent.getAction())) return;
        Notifier.cancelDetach(context.getApplicationContext());
    }
}
