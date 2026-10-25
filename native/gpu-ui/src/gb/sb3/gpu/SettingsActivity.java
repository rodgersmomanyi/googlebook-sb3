package gb.sb3.gpu;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Insets;
import android.graphics.drawable.Drawable;
import android.os.Bundle;
import android.text.InputType;
import android.text.format.DateFormat;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import gb.sb3.gpu.core.AppRows;
import gb.sb3.gpu.core.CatalogBuilder;
import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.GraphicsPage;
import gb.sb3.gpu.core.HoldersModel;
import gb.sb3.gpu.core.Json;
import gb.sb3.gpu.core.Labels;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;
import gb.sb3.gpu.core.WatchSession;

import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The Graphics page (INTERFACES 27.2): Settings > System > Graphics (injected, 13.2) and the tile's long-press. Top to
 * bottom: the mode radios, the pending line with Close apps, the status line, the NVIDIA GPU line, the self-test line
 * with Run self-test again, Use NVIDIA for all apps, the per-app list (Use NVIDIA / Always Intel), the NVIDIA compute
 * service with the wave-1 allowlist, the help text and the diagnostic report. All texts and enable rules come from the
 * host-tested models ({@link GraphicsPage}, {@link AppRows}). No socket or {@code gfx_cap} 0: Unavailable with the
 * graphics controls disabled. Built in code: no AndroidX, no layouts.
 */
public final class SettingsActivity extends Activity {
    private WatchSession watch;
    private boolean started;
    private boolean binding;
    private boolean pushedOnOpen;
    private Status status;
    private Labels.Labeler labeler;
    private GraphicsPage.DateFmt dateFmt;

    private TextView unavailableHelp;
    private RadioGroup modes;
    private final Map<String, RadioButton> radios = new HashMap<String, RadioButton>();
    private final Map<Integer, String> radioMode = new HashMap<Integer, String>();
    private TextView pendingLine;
    private Button closeApps;
    private TextView statusLine;
    private TextView gpuLine;
    private TextView selfTestLine;
    private Button rerun;
    private Switch allApps;
    private TextView allAppsSub;
    private TextView appsNote;
    private LinearLayout appList;
    private TextView computeLine;
    private LinearLayout allowList;
    private EditText pkgEdit;
    private Button allowButton;
    private Button report;

    private final List<AppRows.Row> rows = new ArrayList<AppRows.Row>();
    private final Map<String, RowViews> rowViews = new HashMap<String, RowViews>();
    private boolean choicesOk;

    /** Views of one app row. */
    private static final class RowViews {
        TextView effective;
        TextView hint;
        Switch useNvidia;
        Switch alwaysIntel;
    }

    private final WatchSession.Listener listener = new WatchSession.Listener() {
        @Override
        public void onStatus(final Status s) {
            Gb.publish(SettingsActivity.this, s);
            Gb.main(new Runnable() {
                @Override
                public void run() {
                    render(s);
                }
            });
        }

        @Override
        public void onUnavailable(String why) {
            Gb.publish(SettingsActivity.this, null);
            Gb.main(new Runnable() {
                @Override
                public void run() {
                    render(null);
                }
            });
        }
    };

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private TextView text(ViewGroup parent, int appearance, String s) {
        TextView t = new TextView(this);
        t.setTextAppearance(appearance);
        t.setText(s);
        t.setPadding(0, dp(6), 0, dp(6));
        parent.addView(t, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return t;
    }

    private TextView heading(ViewGroup parent, String s) {
        TextView t = text(parent, android.R.style.TextAppearance_DeviceDefault_Medium, s);
        t.setPadding(0, dp(18), 0, dp(4));
        return t;
    }

    private Button button(ViewGroup parent, String label, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setFilterTouchesWhenObscured(true);
        b.setOnClickListener(l);
        parent.addView(b, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return b;
    }

    private void radio(String mode, String label, String secondary) {
        RadioButton r = new RadioButton(this);
        r.setId(View.generateViewId());
        r.setText(label);
        r.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Medium);
        r.setFilterTouchesWhenObscured(true);
        modes.addView(r, new RadioGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView sub = new TextView(this);
        sub.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Small);
        sub.setText(secondary);
        sub.setPadding(dp(32), 0, 0, dp(8));
        modes.addView(sub, new RadioGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        radios.put(mode, r);
        radioMode.put(r.getId(), mode);
    }

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setTitle(Texts.SETTINGS_TITLE);
        labeler = Gb.labeler(this);
        final java.text.DateFormat df = DateFormat.getMediumDateFormat(this);
        dateFmt = new GraphicsPage.DateFmt() {
            @Override
            public String date(long epochSeconds) {
                return df.format(new Date(epochSeconds * 1000L));
            }
        };
        final int pad = dp(16);
        ScrollView scroll = new ScrollView(this);
        final LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(pad, pad, pad, pad);
        scroll.addView(col);
        // Edge-to-edge (targetSdk 36): keep content clear of the system bars, cutouts and the keyboard.
        scroll.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override
            public WindowInsets onApplyWindowInsets(View v, WindowInsets in) {
                Insets i = in.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout()
                        | WindowInsets.Type.ime());
                v.setPadding(i.left, i.top, i.right, i.bottom);
                return WindowInsets.CONSUMED;
            }
        });

        text(col, android.R.style.TextAppearance_DeviceDefault_Large, Texts.SETTINGS_TITLE);
        unavailableHelp = text(col, android.R.style.TextAppearance_DeviceDefault_Small,
                getString(R.string.unavailable_help));

        // 1. mode radios
        modes = new RadioGroup(this);
        modes.setOrientation(LinearLayout.VERTICAL);
        radio("auto", Texts.RADIO_AUTO, Texts.RADIO_AUTO_SUB);
        radio("intel", Texts.RADIO_INTEL, Texts.RADIO_INTEL_SUB);
        radio("nvidia", Texts.RADIO_NVIDIA, Texts.RADIO_NVIDIA_SUB);
        modes.setOnCheckedChangeListener(new RadioGroup.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(RadioGroup g, int id) {
                String m = radioMode.get(id);
                if (!binding && m != null) setMode(m);
            }
        });
        col.addView(modes, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        pendingLine = text(col, android.R.style.TextAppearance_DeviceDefault_Small, "");
        closeApps = button(col, Texts.CLOSE_APPS, new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(SettingsActivity.this, HoldersActivity.class)
                        .putExtra(HoldersActivity.EXTRA_MODE, HoldersModel.Mode.INTEL.extra()));
            }
        });

        // 2.-4. status, GPU, self-test
        statusLine = text(col, android.R.style.TextAppearance_DeviceDefault_Medium, "");
        gpuLine = text(col, android.R.style.TextAppearance_DeviceDefault_Small, "");
        selfTestLine = text(col, android.R.style.TextAppearance_DeviceDefault_Small, "");
        rerun = button(col, Texts.SELFTEST_RERUN, new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                rerun.setEnabled(false);
                Gb.async("run-selftest", null, SettingsActivity.this, new Gb.Done() {
                    @Override
                    public void done(GbPowerClient.Outcome o) {
                        Gb.toast(SettingsActivity.this, o.ok() ? Texts.SELFTEST_STARTED
                                : Texts.error("run-selftest", o));
                        render(status);
                    }
                });
            }
        });

        // 5. Use NVIDIA for all apps
        allApps = new Switch(this);
        allApps.setText(Texts.ALL_APPS);
        allApps.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Medium);
        allApps.setFilterTouchesWhenObscured(true);
        allApps.setPadding(0, dp(18), 0, dp(4));
        allApps.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton b, boolean on) {
                if (!binding) setPolicy(on);
            }
        });
        col.addView(allApps, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        allAppsSub = text(col, android.R.style.TextAppearance_DeviceDefault_Small, Texts.ALL_APPS_OFF);

        // 6. per-app list
        heading(col, Texts.APPS_HEADING);
        text(col, android.R.style.TextAppearance_DeviceDefault_Small, Texts.APPS_HINT);
        appsNote = text(col, android.R.style.TextAppearance_DeviceDefault_Small, getString(R.string.apps_loading));
        appList = new LinearLayout(this);
        appList.setOrientation(LinearLayout.VERTICAL);
        col.addView(appList, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        // 7. compute service (wave-1 allowlist)
        heading(col, Texts.COMPUTE_HEADING);
        computeLine = text(col, android.R.style.TextAppearance_DeviceDefault_Small, "");
        text(col, android.R.style.TextAppearance_DeviceDefault_Small, getString(R.string.compute_help));
        allowList = new LinearLayout(this);
        allowList.setOrientation(LinearLayout.VERTICAL);
        col.addView(allowList);
        LinearLayout addRow = new LinearLayout(this);
        addRow.setOrientation(LinearLayout.HORIZONTAL);
        addRow.setGravity(Gravity.CENTER_VERTICAL);
        pkgEdit = new EditText(this);
        pkgEdit.setHint(R.string.package_hint);
        pkgEdit.setSingleLine(true);
        pkgEdit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        addRow.addView(pkgEdit, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        allowButton = button(addRow, getString(R.string.allow_button), new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                allow(pkgEdit.getText().toString().trim());
            }
        });
        col.addView(addRow);

        // 8. help, 9. diagnostic report
        heading(col, Texts.HELP_HEADING);
        text(col, android.R.style.TextAppearance_DeviceDefault_Small, Texts.HELP_TEXT);
        heading(col, getString(R.string.diagnostics_heading));
        report = button(col, getString(R.string.report_button), new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Gb.async("report", null, SettingsActivity.this, new Gb.Done() {
                    @Override
                    public void done(GbPowerClient.Outcome o) {
                        Gb.toast(SettingsActivity.this, o.ok() ? Texts.REPORT_REQUESTED : Texts.error("report", o));
                    }
                });
            }
        });
        text(col, android.R.style.TextAppearance_DeviceDefault_Small, getString(R.string.recovery_help));

        setContentView(scroll);
        watch = new WatchSession(Gb.CLIENT, listener, Gb.watchOptions(0));
        render(Gb.lastStatus());
    }

    @Override
    protected void onStart() {
        super.onStart();
        started = true;
        watch.start();
        loadAllowList();
        loadApps();
        IntentFilter f = new IntentFilter();
        f.addAction(Intent.ACTION_PACKAGE_ADDED);
        f.addAction(Intent.ACTION_PACKAGE_REMOVED);
        f.addAction(Intent.ACTION_PACKAGE_REPLACED);
        f.addDataScheme("package");
        registerReceiver(packages, f, Context.RECEIVER_NOT_EXPORTED);
    }

    @Override
    protected void onStop() {
        started = false;
        unregisterReceiver(packages);
        watch.stop();
        super.onStop();
    }

    /**
     * D3 "on package add/remove/replace" while the page is visible: refresh the list and push the catalog at once. The
     * background path is INTERFACES 24.3's (gb_power sees packages.list change, sets apps_sync 0 and broadcasts;
     * manifest receivers do not get these implicit broadcasts). A push that races PackageManager's packages.list write
     * only drops the new package; the following apps_sync = 0 broadcast pushes again.
     */
    private final BroadcastReceiver packages = new BroadcastReceiver() {
        @Override
        public void onReceive(Context c, Intent i) {
            if (!started) return;
            loadApps();
            final Context app = getApplicationContext();
            Gb.io(new Runnable() {
                @Override
                public void run() {
                    Gb.pushCatalog(app);
                }
            });
        }
    };

    // ------------------------------------------------------------------ rendering

    private void render(Status s) {
        status = s;
        GraphicsPage p = GraphicsPage.build(s, labeler, dateFmt);
        binding = true;
        try {
            RadioButton r = radios.get(p.mode);
            if (s != null && r != null) modes.check(r.getId());
            else modes.clearCheck();
            for (RadioButton b : radios.values()) b.setEnabled(p.graphics);
            unavailableHelp.setVisibility(p.graphics ? View.GONE : View.VISIBLE);
            pendingLine.setVisibility(p.pendingLine == null ? View.GONE : View.VISIBLE);
            pendingLine.setText(p.pendingLine == null ? "" : p.pendingLine);
            closeApps.setVisibility(p.pendingLine == null ? View.GONE : View.VISIBLE);
            statusLine.setText(p.statusLine);
            gpuLine.setText(p.gpuLine);
            gpuLine.setVisibility(p.connected ? View.VISIBLE : View.GONE);
            selfTestLine.setText(p.selfTestLine);
            selfTestLine.setVisibility(p.graphics ? View.VISIBLE : View.GONE);
            rerun.setEnabled(p.rerunEnabled);
            allApps.setChecked(p.allApps);
            allApps.setEnabled(p.graphics);
            allAppsSub.setText(p.allAppsSecondary);
            computeLine.setText(p.computeLine);
            pkgEdit.setEnabled(p.connected);
            allowButton.setEnabled(p.connected);
            report.setEnabled(p.connected);
            for (int i = 0; i < allowList.getChildCount(); i++) setEnabledDeep(allowList.getChildAt(i), p.connected);
            AppRows.setHolders(rows, s == null ? null : s.holders);
            for (AppRows.Row row : rows) bindRow(row);
        } finally {
            binding = false;
        }
        if (s != null && s.appsSync == 0 && !pushedOnOpen) {
            pushedOnOpen = true; // 27.5: the page pushes the catalog on open when gb_power has none for this stamp
            final Context app = getApplicationContext();
            Gb.io(new Runnable() {
                @Override
                public void run() {
                    Gb.pushCatalog(app);
                }
            });
        }
    }

    private static void setEnabledDeep(View v, boolean on) {
        v.setEnabled(on);
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) setEnabledDeep(g.getChildAt(i), on);
        }
    }

    private String policy() {
        return status == null ? "games" : status.gfxPolicy;
    }

    private boolean rowsEnabled() {
        return status != null && status.gfxCap == 1 && choicesOk;
    }

    private void bindRow(AppRows.Row row) {
        RowViews v = rowViews.get(row.pkg);
        if (v == null) return;
        boolean was = binding;
        binding = true;
        try {
            v.effective.setText(row.effectiveText(policy()));
            String h = row.hint(policy());
            v.hint.setText(h == null ? "" : h);
            v.hint.setVisibility(h == null ? View.GONE : View.VISIBLE);
            v.useNvidia.setChecked(row.useNvidia());
            v.alwaysIntel.setChecked(row.alwaysIntel());
            v.useNvidia.setEnabled(rowsEnabled());
            v.alwaysIntel.setEnabled(rowsEnabled());
        } finally {
            binding = was;
        }
    }

    // ------------------------------------------------------------------ actions

    private void setMode(final String m) {
        if (status != null && m.equals(status.effectiveMode())) return;
        for (RadioButton b : radios.values()) b.setEnabled(false);
        Gb.async("set-mode", m, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                Gb.reportMode(SettingsActivity.this, "set-mode", o);
                Gb.requestTileUpdate(SettingsActivity.this);
                SwitchProvider.notifyChanged(SettingsActivity.this);
                refreshStatus();
            }
        });
    }

    private void setPolicy(final boolean all) {
        allApps.setEnabled(false);
        Gb.async("set-app-policy", all ? "all" : "games", this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                if (!o.ok()) Gb.toast(SettingsActivity.this, Texts.error("set-app-policy", o));
                refreshStatus();
            }
        });
    }

    private void refreshStatus() {
        Gb.async("status", null, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                if (started) render(o.status());
            }
        });
    }

    private void changeRow(final AppRows.Row row, String which, boolean on) {
        final String before = row.choice;
        final AppRows.Change ch = AppRows.change(row.pkg, which, on);
        row.choice = ch.choice; // optimistic: turning one switch on turns the other off
        bindRow(row);
        final RowViews v = rowViews.get(row.pkg);
        if (v != null) {
            v.useNvidia.setEnabled(false);
            v.alwaysIntel.setEnabled(false);
        }
        Gb.async(ch.cmd, ch.arg, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                if (!AppRows.apply(row, before, o)) Gb.toast(SettingsActivity.this, Texts.error(ch.cmd, o));
                bindRow(row);
            }
        });
    }

    // ------------------------------------------------------------------ the per-app list

    private void loadApps() {
        final Context app = getApplicationContext();
        Gb.io(new Runnable() {
            @Override
            public void run() {
                PackageManager pm = app.getPackageManager();
                List<ResolveInfo> ris = Gb.launchable(app);
                final List<CatalogBuilder.Entry> catalog = Gb.catalog(app, ris);
                final Map<String, Drawable> icons = new HashMap<String, Drawable>();
                for (ResolveInfo ri : ris) {
                    String pkg = ri.activityInfo == null ? null : ri.activityInfo.packageName;
                    if (pkg != null && !icons.containsKey(pkg)) icons.put(pkg, ri.loadIcon(pm));
                }
                final List<AppRows.Choice> choices = Gb.CLIENT.getAllChoices();
                Gb.main(new Runnable() {
                    @Override
                    public void run() {
                        if (started) showApps(catalog, choices, icons);
                    }
                });
            }
        });
    }

    private void showApps(List<CatalogBuilder.Entry> catalog, List<AppRows.Choice> choices,
            Map<String, Drawable> icons) {
        choicesOk = choices != null;
        rows.clear();
        rowViews.clear();
        appList.removeAllViews();
        rows.addAll(AppRows.build(catalog, choices, status == null ? null : status.holders));
        appsNote.setText(rows.isEmpty() ? getString(R.string.apps_none)
                : choicesOk ? "" : getString(R.string.choices_failed));
        appsNote.setVisibility(rows.isEmpty() || !choicesOk ? View.VISIBLE : View.GONE);
        for (final AppRows.Row row : rows) {
            LinearLayout line = new LinearLayout(this);
            line.setOrientation(LinearLayout.HORIZONTAL);
            line.setGravity(Gravity.CENTER_VERTICAL);
            line.setPadding(0, dp(6), 0, dp(6));
            ImageView icon = new ImageView(this);
            Drawable d = icons.get(row.pkg);
            if (d != null) icon.setImageDrawable(d);
            icon.setContentDescription(row.label);
            line.addView(icon, new LinearLayout.LayoutParams(dp(40), dp(40)));
            LinearLayout texts = new LinearLayout(this);
            texts.setOrientation(LinearLayout.VERTICAL);
            texts.setPadding(dp(12), 0, dp(8), 0);
            TextView label = new TextView(this);
            label.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Medium);
            label.setText(row.label);
            texts.addView(label);
            TextView pkg = new TextView(this);
            pkg.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Small);
            pkg.setText(row.pkg);
            texts.addView(pkg);
            LinearLayout badges = new LinearLayout(this);
            badges.setOrientation(LinearLayout.HORIZONTAL);
            if (row.game) {
                TextView g = new TextView(this);
                g.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Small);
                g.setText(Texts.GAME_BADGE);
                g.setPadding(0, 0, dp(12), 0);
                badges.addView(g);
            }
            RowViews v = new RowViews();
            v.effective = new TextView(this);
            v.effective.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Small);
            badges.addView(v.effective);
            texts.addView(badges);
            v.hint = new TextView(this);
            v.hint.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Small);
            texts.addView(v.hint);
            line.addView(texts, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            LinearLayout sw = new LinearLayout(this);
            sw.setOrientation(LinearLayout.VERTICAL);
            v.useNvidia = rowSwitch(sw, Texts.USE_NVIDIA, row, AppRows.NVIDIA);
            v.alwaysIntel = rowSwitch(sw, Texts.ALWAYS_INTEL, row, AppRows.INTEL);
            line.addView(sw, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT));
            appList.addView(line, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT));
            rowViews.put(row.pkg, v);
            bindRow(row);
        }
    }

    private Switch rowSwitch(LinearLayout parent, String label, final AppRows.Row row, final String which) {
        Switch s = new Switch(this);
        s.setText(label);
        s.setTextAppearance(android.R.style.TextAppearance_DeviceDefault_Small);
        s.setFilterTouchesWhenObscured(true);
        s.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton b, boolean on) {
                if (!binding) changeRow(row, which, on);
            }
        });
        parent.addView(s, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return s;
    }

    // ------------------------------------------------------------------ compute allowlist (wave 1)

    private void loadAllowList() {
        Gb.async("compute-list", null, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                if (!started) return;
                allowList.removeAllViews();
                if (!o.ok()) {
                    TextView t = new TextView(SettingsActivity.this);
                    t.setText(Texts.error("compute-list", o));
                    allowList.addView(t);
                    return;
                }
                for (Object e : Json.list(o.reply.obj, "allow")) {
                    Map<String, Object> m = Json.obj(e);
                    final String pkg = Json.str(m, "pkg", "");
                    if (!GbPowerClient.validPackage(pkg)) continue;
                    LinearLayout row = new LinearLayout(SettingsActivity.this);
                    row.setOrientation(LinearLayout.HORIZONTAL);
                    row.setGravity(Gravity.CENTER_VERTICAL);
                    TextView t = new TextView(SettingsActivity.this);
                    t.setText(pkg);
                    row.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                    button(row, getString(R.string.remove_button), new View.OnClickListener() {
                        @Override
                        public void onClick(View v) {
                            change("compute-deny", pkg);
                        }
                    });
                    allowList.addView(row);
                }
            }
        });
    }

    private void allow(String pkg) {
        if (!GbPowerClient.validPackage(pkg)) {
            pkgEdit.setError(getString(R.string.package_invalid));
            return;
        }
        change("compute-allow", pkg);
    }

    private void change(final String cmd, String pkg) {
        Gb.async(cmd, pkg, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                if (!o.ok()) {
                    Gb.toast(SettingsActivity.this, Texts.error(cmd, o));
                } else if ("compute-allow".equals(cmd)) {
                    pkgEdit.setText("");
                }
                loadAllowList();
            }
        });
    }
}
