package gb.sb3.gpu;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.os.Bundle;

import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.Holder;
import gb.sb3.gpu.core.HoldersModel;
import gb.sb3.gpu.core.Json;
import gb.sb3.gpu.core.Labels;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * The close-apps dialog (INTERFACES 27.4) in three modes (extra {@link #EXTRA_MODE}): {@code close} (in-use
 * notification, Settings page), {@code intel} (an Intel switch reported {@code pending}) and {@code detach} (the detach
 * notification's <b>Close apps and detach</b>). It lists a fresh holder scan (`list-holders`); the positive button sends
 * `close-holders` (gb_power queues an `am force-stop` for each verified closable holder) or, in detach mode,
 * `close-holders-and-detach`, plus `stop-compute` when the compute service runs. In detach mode the dialog then shows
 * {@code Preparing to detach...} until {@code dtx} is {@code confirmed} (it closes) or {@code detach_ref} grows again
 * ({@code Some apps could not be closed} with the remaining labels). Not exported.
 */
public final class HoldersActivity extends Activity {
    static final String EXTRA_MODE = "gb.sb3.gpu.extra.MODE";
    private static final long DETACH_WATCH_MS = 60000;
    private static final long POLL_MS = 500;

    private HoldersModel.Mode mode = HoldersModel.Mode.CLOSE;
    private AlertDialog dialog;
    private volatile boolean watching;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        mode = HoldersModel.Mode.parse(getIntent() == null ? null : getIntent().getStringExtra(EXTRA_MODE));
        if (mode == HoldersModel.Mode.DETACH) Notifier.cancelDetach(getApplicationContext());
        final Context app = getApplicationContext();
        Gb.io(new Runnable() {
            @Override
            public void run() {
                final Status s = Gb.CLIENT.status().status();
                final List<Holder> hs = new ArrayList<Holder>();
                GbPowerClient.Outcome o = Gb.CLIENT.call("list-holders");
                if (o.ok()) {
                    for (Object e : Json.list(o.reply.obj, "holders")) {
                        Holder h = Holder.from(e);
                        if (h != null) hs.add(h);
                    }
                } else if (s != null) {
                    hs.addAll(s.holders);
                }
                final HoldersModel.Plan p = s == null ? null
                        : HoldersModel.plan(mode, hs, s.computeRunning(), Gb.labeler(app));
                Gb.main(new Runnable() {
                    @Override
                    public void run() {
                        show(s, p);
                    }
                });
            }
        });
    }

    /** singleTask: a second request (another mode, e.g. from the detach notification) restarts the dialog. */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        watching = false;
        recreate();
    }

    @Override
    protected void onDestroy() {
        watching = false;
        if (dialog != null) dialog.dismiss();
        super.onDestroy();
    }

    private void show(final Status s, final HoldersModel.Plan p) {
        if (isFinishing() || isDestroyed()) return;
        if (s == null || p == null) {
            Gb.toast(this, Texts.NOT_AVAILABLE);
            finish();
            return;
        }
        if (!p.canProceed() && p.others.isEmpty()) {
            Gb.toast(this, Texts.NO_HOLDERS);
            finish();
            return;
        }
        AlertDialog.Builder b = new AlertDialog.Builder(this)
                .setTitle(HoldersModel.title(mode))
                .setMessage(p.message())
                .setNegativeButton(Texts.HOLDERS_CANCEL, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int which) {
                        finish();
                    }
                })
                .setOnCancelListener(new DialogInterface.OnCancelListener() {
                    @Override
                    public void onCancel(DialogInterface d) {
                        finish();
                    }
                });
        if (p.canProceed()) {
            b.setPositiveButton(HoldersModel.positive(mode), new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface d, int which) {
                    if (mode == HoldersModel.Mode.DETACH) {
                        detach(p, s.detachRef);
                    } else {
                        closeApps(getApplicationContext(), p);
                        finish();
                    }
                }
            });
        }
        dialog = b.create();
        dialog.setCanceledOnTouchOutside(true);
        dialog.show();
        if (dialog.getButton(AlertDialog.BUTTON_POSITIVE) != null) {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setFilterTouchesWhenObscured(true);
        }
    }

    /** close / intel: runs on a background thread after the user confirmed; uses the application context only. */
    static void closeApps(final Context app, final HoldersModel.Plan p) {
        Gb.io(new Runnable() {
            @Override
            public void run() {
                String problem = send(p);
                if (problem != null) Notifier.alert(app, Texts.ALERT_CLOSE_TITLE, problem);
                Gb.publish(app, Gb.CLIENT.status().status());
                Gb.requestTileUpdate(app);
            }
        });
    }

    /** Sends the plan's commands; returns a problem text for the alert, or null. */
    static String send(HoldersModel.Plan p) {
        String problem = null;
        boolean fallback = false;
        if (p.cmd != null) {
            GbPowerClient.Outcome o = Gb.CLIENT.call(p.cmd, p.arg);
            if (!o.ok()) {
                // R-16: every listed app exited meanwhile: nothing to close, ask for the detach directly
                fallback = o.reply != null && HoldersModel.detachFallback(p.mode, p.cmd, o.reply.err);
                if (!fallback) problem = Texts.error(p.cmd, o);
            } else {
                List<String> refused = new ArrayList<String>();
                for (Object r : Json.list(o.reply.obj, "refused")) {
                    Map<String, Object> m = Json.obj(r);
                    String reason = Json.str(m, "reason", "?");
                    if (!HoldersModel.refusalShown(p.mode, reason)) continue;   // R-16: exited meanwhile
                    int pid = Json.int32(m, "pid", -1);
                    int k = p.pids.indexOf(pid);
                    String n = k >= 0 && k < p.names.size() ? p.names.get(k) : "pid " + pid;
                    refused.add(n + " (" + reason + ")");
                }
                if (!refused.isEmpty()) problem = Labels.join(refused);
            }
        }
        if (p.stopCompute) Gb.CLIENT.call("stop-compute");
        if (p.detachRequest || fallback) {
            GbPowerClient.Outcome d = Gb.CLIENT.call("detach-request");
            if (!d.ok() && problem == null) problem = Texts.error("detach-request", d);
        }
        return problem;
    }

    /** detach mode: send, then show "Preparing to detach..." and watch the status (27.4). */
    private void detach(final HoldersModel.Plan p, final int refAtStart) {
        final AlertDialog progress = new AlertDialog.Builder(this)
                .setMessage(Texts.PREPARING_DETACH_DOTS)
                .setNegativeButton(Texts.HOLDERS_CANCEL, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int which) {
                        finish();
                    }
                })
                .setCancelable(false)
                .create();
        dialog = progress;
        progress.show();
        watching = true;
        final Context app = getApplicationContext();
        Gb.io(new Runnable() {
            @Override
            public void run() {
                final String problem = send(p);
                if (problem != null) {
                    finishWith(problem);
                    return;
                }
                long end = System.currentTimeMillis() + DETACH_WATCH_MS;
                while (watching && System.currentTimeMillis() < end) {
                    Status s = Gb.CLIENT.status().status();
                    HoldersModel.Progress pr = HoldersModel.progress(s, refAtStart);
                    if (pr == HoldersModel.Progress.DONE) {
                        Gb.main(new Runnable() {
                            @Override
                            public void run() {
                                finish();
                            }
                        });
                        return;
                    }
                    if (pr == HoldersModel.Progress.FAILED) {
                        finishWith(HoldersModel.failedText(s, Gb.labeler(app)));
                        return;
                    }
                    try {
                        Thread.sleep(POLL_MS);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                Gb.main(new Runnable() {
                    @Override
                    public void run() {
                        finish();
                    }
                });
            }
        });
    }

    /** Replaces the progress dialog with a failure message (OK closes the activity). */
    private void finishWith(final String text) {
        Gb.main(new Runnable() {
            @Override
            public void run() {
                if (isFinishing() || isDestroyed()) return;
                if (dialog != null) dialog.dismiss();
                dialog = new AlertDialog.Builder(HoldersActivity.this)
                        .setTitle(Texts.DETACH_TITLE)
                        .setMessage(text)
                        .setPositiveButton(android.R.string.ok, new DialogInterface.OnClickListener() {
                            @Override
                            public void onClick(DialogInterface d, int which) {
                                finish();
                            }
                        })
                        .setOnCancelListener(new DialogInterface.OnCancelListener() {
                            @Override
                            public void onCancel(DialogInterface d) {
                                finish();
                            }
                        })
                        .create();
                dialog.show();
            }
        });
    }
}
