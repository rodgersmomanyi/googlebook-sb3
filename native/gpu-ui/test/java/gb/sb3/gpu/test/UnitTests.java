package gb.sb3.gpu.test;

import gb.sb3.gpu.core.AppRows;
import gb.sb3.gpu.core.CatalogBuilder;
import gb.sb3.gpu.core.Conn;
import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.GraphicsPage;
import gb.sb3.gpu.core.Holder;
import gb.sb3.gpu.core.HoldersModel;
import gb.sb3.gpu.core.Json;
import gb.sb3.gpu.core.Labels;
import gb.sb3.gpu.core.NotifModel;
import gb.sb3.gpu.core.Reply;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.StateMapper.Tap;
import gb.sb3.gpu.core.StateMapper.TileModel;
import gb.sb3.gpu.core.StateMapper.TileState;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;
import gb.sb3.gpu.core.Transport;
import gb.sb3.gpu.core.UserPolicy;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

/**
 * Host unit tests. v1.2 (TASKS_1B 4.2): UI-1B-01 graphicsTile, UI-1B-02 nextMode, UI-1B-03 tap mapping, UI-1B-05
 * GraphicsPage, UI-1B-06 AppRows, UI-1B-07 CatalogBuilder, UI-1B-08 NotifModel, UI-1B-09 HoldersModel. Kept from wave
 * 1: the legacy 8.3 mapping (UI-03, still exercised as 8.3 says), the detach tile, JSON, reply/status parsing and
 * request validation (extended to the 26.1 commands).
 */
final class UnitTests {
    private UnitTests() {
    }

    /** A v1.1 status reply line (9.5 without the v1.2 fields), as gb_power 1.1 printed it. */
    static String statusLine(String state, int persist, String extra) {
        return "{\"v\":1,\"ok\":true,\"cmd\":\"status\",\"state\":\"" + state + "\",\"n\":0,\"holders\":[],"
                + "\"mode\":\"default\",\"result\":\"idle\",\"persist\":" + persist + ",\"clean_boots\":0,"
                + "\"probation\":0,\"used\":0,\"foreign\":0,\"wedged\":0,\"fault_reason\":\"\",\"bdf\":\"0000:02:00.0\","
                + "\"driver\":\"\",\"runtime_status\":\"suspended\",\"power_state\":\"D3cold\",\"refcnt\":-1,"
                + "\"latch\":\"unlocked\",\"base\":\"attached\",\"dtx\":\"ready\",\"compute\":\"stopped\","
                + "\"payload\":\"legacy\",\"gfxdriver\":0" + (extra == null ? "" : extra) + ",\"t_ms\":1234567}";
    }

    static Status st(String state, int persist) {
        try {
            return Reply.parse(statusLine(state, persist, null)).status();
        } catch (Reply.ProtocolException e) {
            throw new AssertionError(e);
        }
    }

    static Status stWith(String json) {
        try {
            return Reply.parse(json).status();
        } catch (Reply.ProtocolException e) {
            return null;
        }
    }

    static final GraphicsPage.DateFmt DF = new GraphicsPage.DateFmt() {
        @Override
        public String date(long t) {
            return "D" + t;
        }
    };

    static void run() {
        graphicsTile();
        nextMode();
        tapMapping();
        graphicsPage();
        appRows();
        catalog();
        notifModel();
        holdersModel();
        legacyMapping();
        detachMapping();
        texts();
        json();
        replies();
        validation();
        userPolicy();
    }

    // ------------------------------------------------------------------ wave 6: which user acts on the state

    static void userPolicy() {
        T.group("W6-NOTIFY UserPolicy (headless system user mode, INTERFACES 12.1 wave 6)");
        T.eq(false, UserPolicy.actsOnState(true, true), "headless mode, system user 0: ignores state broadcasts");
        T.eq(true, UserPolicy.actsOnState(true, false), "headless mode, full user (10): acts");
        T.eq(true, UserPolicy.actsOnState(false, true), "not headless, user 0 is the person: acts");
        T.eq(true, UserPolicy.actsOnState(false, false), "not headless, secondary user: acts");
    }

    // ------------------------------------------------------------------ UI-1B-01 graphicsTile (INTERFACES 27.1)

    static void tile(Status s, TileState ts, String sub, int row, String what) {
        TileModel m = StateMapper.graphicsTile(s);
        T.check(m.state == ts && sub.equals(m.subtitle) && m.row == row, "27.1 " + what + ": want " + ts + "/" + sub
                + "/row" + row + " got " + m);
    }

    /** Every row in auto and nvidia mode (rows 3-4 exist only for intel mode). */
    static void graphicsTile() {
        T.group("UI-1B-01 graphicsTile (INTERFACES 27.1)");
        TileState U = TileState.UNAVAILABLE;
        TileState I = TileState.INACTIVE;
        TileState A = TileState.ACTIVE;
        tile(null, U, "Unavailable", 1, "row 1 no socket / hello fails / role other");
        for (String m : new String[] {"auto", "nvidia", "intel"}) {
            tile(Fx.st("gfx_mode", m, "gfx_cap", 0), U, "Unavailable", 1, "row 1 gfx_cap 0 (" + m + ")");
            tile(Fx.st("gfx_mode", m, "dtx", "n/a"), U, "Unavailable", 1, "row 1 dtx n/a (" + m + ")");
            tile(Fx.st("gfx_mode", m, "state", "detaching"), U, "Preparing to detach", 2, "row 2 detaching (" + m + ")");
            tile(Fx.st("gfx_mode", m, "pending", "detach", "state", "busy:1"), U, "Preparing to detach", 2,
                    "row 2 pending detach (" + m + ")");
        }
        tile(Fx.st("gfx_mode", "intel", "state", "busy:3", "gfx_active", "intel", "gfx_reason", "mode"), I,
                "Off (Intel mode, 3 on NVIDIA)", 3, "row 3");
        tile(Fx.st("gfx_mode", "intel", "gfx_active", "intel", "gfx_reason", "mode"), I, "Off (Intel mode)", 4, "row 4");
        tile(Fx.st("gfx_mode", "intel", "state", "wedged", "gfx_reason", "wedged"), I, "Off (Intel mode)", 4,
                "row 4 before row 5 (intel + wedged)");
        String[][] both = {
            // state, reason, active, base, auto subtitle, nvidia subtitle, row, tile state (A/I)
            {"wedged", "wedged", "intel", "attached", "Restart required", "Restart required", "5", "I"},
            {"fault", "fault", "intel", "attached", "Fault", "Fault", "6", "I"},
            {"standby", "kill", "intel", "attached", "Disabled for this boot", "Disabled for this boot", "7", "I"},
            {"disabled", "nodriver", "intel", "attached", "Disabled for this boot", "Disabled for this boot", "7", "I"},
            {"crashloop", "crashloop", "intel", "attached", "Intel (recovered)", "NVIDIA unavailable - recovered", "8",
                "I"},
            {"standby", "test", "intel", "attached", "Intel (test boot)", "Intel (test boot)", "9", "I"},
            {"unavailable", "detached", "intel", "detached", "Unavailable (base detached)",
                "NVIDIA unavailable - base detached", "10", "I"},
            {"unavailable", "detached", "intel", "unknown", "Unavailable (base detached)",
                "NVIDIA unavailable - base detached", "10", "I"},
            {"standby", "ok", "nvidia", "not-feasible", "NVIDIA", "NVIDIA (manual)", "12", "A"},
            {"busy:2", "ok", "nvidia", "attached", "NVIDIA (busy 2)", "NVIDIA (manual, busy 2)", "11", "A"},
            {"standby", "ok", "nvidia", "attached", "NVIDIA", "NVIDIA (manual)", "12", "A"},
            {"loading", "loading", "intel", "attached", "Intel (starting NVIDIA)", "NVIDIA unavailable - starting", "13",
                "I"},
            {"standby", "untested", "intel", "attached", "Intel (testing NVIDIA)", "NVIDIA unavailable - testing", "14",
                "I"},
            {"standby", "testerror", "intel", "attached", "Intel (NVIDIA test not run)",
                "NVIDIA unavailable - test not run", "15", "I"},
            {"standby", "selftest", "intel", "attached", "Intel (NVIDIA test failed)",
                "NVIDIA unavailable - test failed", "16", "I"},
            {"off", "model", "intel", "attached", "Intel (NVIDIA not supported)", "NVIDIA unavailable - not supported",
                "17", "I"},
            {"off", "nodriver", "intel", "attached", "Intel", "NVIDIA unavailable - not ready", "18", "I"},
            {"standby", "detaching", "intel", "attached", "Intel", "NVIDIA unavailable - not ready", "18", "I"},
        };
        for (String[] r : both) {
            for (String mode : new String[] {"auto", "nvidia"}) {
                Status s = Fx.st("gfx_mode", mode, "state", r[0], "gfx_reason", r[1], "gfx_active", r[2], "base", r[3]);
                tile(s, "A".equals(r[7]) ? A : I, "auto".equals(mode) ? r[4] : r[5], Integer.parseInt(r[6]),
                        "row " + r[6] + " " + mode + " state=" + r[0] + " reason=" + r[1] + " base=" + r[3]);
            }
        }
        // busy N 1..999 in rows 3 and 11
        boolean allOk = true;
        for (int n = 1; n <= 999; n++) {
            TileModel a = StateMapper.graphicsTile(Fx.st("state", "busy:" + n));
            TileModel v = StateMapper.graphicsTile(Fx.st("state", "busy:" + n, "gfx_mode", "nvidia"));
            TileModel i = StateMapper.graphicsTile(Fx.st("state", "busy:" + n, "gfx_mode", "intel",
                    "gfx_active", "intel", "gfx_reason", "mode"));
            allOk &= a.state == A && a.subtitle.equals("NVIDIA (busy " + n + ")") && a.busy == n
                    && v.subtitle.equals("NVIDIA (manual, busy " + n + ")")
                    && i.state == I && i.subtitle.equals("Off (Intel mode, " + n + " on NVIDIA)");
        }
        T.check(allOk, "busy N for N = 1..999 in rows 11 (auto, nvidia) and 3 (intel)");
        // 15 unknown values
        tile(Fx.st("gfx_mode", "Intel"), A, "NVIDIA", 12, "unknown gfx_mode 'Intel' counts as auto");
        tile(Fx.st("gfx_mode", "turbo"), A, "NVIDIA", 12, "unknown gfx_mode 'turbo' counts as auto");
        tile(Fx.st("gfx_mode", ""), A, "NVIDIA", 12, "empty gfx_mode counts as auto");
        tile(Fx.st("gfx_active", "intel", "gfx_reason", "weird"), I, "Intel", 18, "unknown gfx_reason -> row 18");
        tile(Fx.st("gfx_active", "intel", "gfx_reason", ""), I, "Intel", 18, "empty gfx_reason -> row 18");
        tile(Fx.st("gfx_active", "intel", "gfx_reason", "Model"), I, "Intel", 18, "gfx_reason 'Model' is not model");
        tile(Fx.st("gfx_active", "NVIDIA", "gfx_reason", "ok"), I, "Intel", 18, "gfx_active 'NVIDIA' is not nvidia");
        tile(Fx.st("state", "busy:0"), A, "NVIDIA", 12, "state busy:0 is not busy");
        tile(Fx.st("state", "busy:1000"), A, "NVIDIA", 12, "state busy:1000 is not busy");
        tile(Fx.st("state", "busy:01"), A, "NVIDIA", 12, "state busy:01 is not busy");
        tile(Fx.st("state", "sleeping", "gfx_active", "intel", "gfx_reason", "nodriver"), I, "Intel", 18,
                "unknown state");
        tile(Fx.st("base", "Attached", "gfx_active", "intel", "gfx_reason", "detached"), I,
                "Unavailable (base detached)", 10, "base 'Attached' is not attached");
        tile(Fx.st("pending", "weird"), A, "NVIDIA", 12, "unknown pending is not detach");
        tile(Fx.st("dtx", "weird", "gfx_active", "intel", "gfx_reason", "detaching"), I, "Intel", 18,
                "unknown dtx is not n/a");
        tile(Fx.st("gfx_cap", 2), U, "Unavailable", 1, "gfx_cap 2 is not 1");
        tile(st("standby", 1), U, "Unavailable", 1, "a v1.1 status without the v1.2 fields (gfx_cap missing)");
    }

    // ------------------------------------------------------------------ UI-1B-02 nextMode

    static void nextMode() {
        T.group("UI-1B-02 nextMode (27.1 cycle)");
        T.eq("intel", StateMapper.nextMode("auto"), "auto -> intel");
        T.eq("nvidia", StateMapper.nextMode("intel"), "intel -> nvidia");
        T.eq("auto", StateMapper.nextMode("nvidia"), "nvidia -> auto");
        for (String u : new String[] {null, "", "AUTO", "Intel", "nvidia ", "persist", "0"}) {
            T.eq("intel", StateMapper.nextMode(u), "unset/unknown '" + u + "' counts as auto -> intel");
        }
        String m = "auto";
        StringBuilder seen = new StringBuilder(m);
        for (int i = 0; i < 6; i++) {
            m = StateMapper.nextMode(m);
            seen.append(">").append(m);
        }
        T.eq("auto>intel>nvidia>auto>intel>nvidia>auto", seen.toString(), "the cycle repeats");
        T.eq("Auto", StateMapper.modeLabel("auto"), "mode label Auto");
        T.eq("Intel only", StateMapper.modeLabel("intel"), "mode label Intel only");
        T.eq("NVIDIA", StateMapper.modeLabel("nvidia"), "mode label NVIDIA");
        T.eq("Auto", StateMapper.modeLabel("bogus"), "mode label of an unknown mode");
    }

    // ------------------------------------------------------------------ UI-1B-03 tap mapping

    static void tapMapping() {
        T.group("UI-1B-03 tap mapping (27.1)");
        T.eq(Tap.NONE, StateMapper.graphicsTile(null).tap, "row 1 (no status): no tap");
        T.eq(Tap.NONE, StateMapper.graphicsTile(Fx.st("gfx_cap", 0)).tap, "row 1 (cap 0): no tap");
        T.eq(Tap.NONE, StateMapper.graphicsTile(Fx.st("state", "detaching")).tap, "row 2: no tap");
        Set<Integer> rows = new HashSet<Integer>();
        Object[][] cases = {
            {"gfx_mode", "intel", "state", "busy:1"}, {"gfx_mode", "intel"}, {"state", "wedged"}, {"state", "fault"},
            {"gfx_reason", "kill", "gfx_active", "intel"}, {"gfx_reason", "crashloop", "gfx_active", "intel"},
            {"gfx_reason", "test", "gfx_active", "intel"}, {"base", "detached", "gfx_active", "intel"},
            {"state", "busy:4"}, {}, {"gfx_reason", "loading", "gfx_active", "intel"},
            {"gfx_reason", "untested", "gfx_active", "intel"}, {"gfx_reason", "testerror", "gfx_active", "intel"},
            {"gfx_reason", "selftest", "gfx_active", "intel"}, {"gfx_reason", "model", "gfx_active", "intel"},
            {"gfx_reason", "nodriver", "gfx_active", "intel"}};
        for (Object[] c : cases) {
            for (String mode : new String[] {"auto", "nvidia"}) {
                boolean intelRow = c.length > 0 && "gfx_mode".equals(c[0]);
                Object[] kv = intelRow ? c : Arrays.copyOf(c, c.length + 2);
                if (!intelRow) {
                    kv[c.length] = "gfx_mode";
                    kv[c.length + 1] = mode;
                }
                TileModel m = StateMapper.graphicsTile(Fx.st(kv));
                rows.add(m.row);
                T.check(m.tap == Tap.SET_MODE && m.state != TileState.UNAVAILABLE,
                        "row " + m.row + " (" + mode + "): tap = set-mode, tile not UNAVAILABLE");
            }
        }
        T.eq(16, rows.size(), "rows 3-18 all reached by the tap cases " + rows);
        // the command a tap sends: set-mode <nextMode(gfx_mode)>
        T.eq("intel", StateMapper.nextMode(Fx.st().gfxMode), "tap in auto sends set-mode intel");
        T.eq("nvidia", StateMapper.nextMode(Fx.st("gfx_mode", "intel").gfxMode), "tap in intel sends set-mode nvidia");
        T.eq("auto", StateMapper.nextMode(Fx.st("gfx_mode", "nvidia").gfxMode), "tap in nvidia sends set-mode auto");
        T.check(GbPowerClient.validRequest("set-mode", StateMapper.nextMode("auto")), "set-mode <next> is valid");
        // effect toasts (exact)
        T.eq("Takes effect after restart", StateMapper.effectToast("next-boot"), "next-boot toast");
        T.eq("Takes effect when the keyboard base is attached", StateMapper.effectToast("next-attach"),
                "next-attach toast");
        T.eq("Switching to Intel when apps on NVIDIA close", StateMapper.effectToast("pending"), "pending toast");
        T.eq(null, StateMapper.effectToast("now"), "now: no toast");
        T.eq(null, StateMapper.effectToast("bogus"), "unknown effect: no toast");
        T.eq(null, StateMapper.effectToast(null), "no effect: no toast");
        // summary of the injected entry
        T.eq("Auto: NVIDIA (busy 2)", StateMapper.summary(Fx.st("state", "busy:2")), "summary example of 27.2");
        T.eq("Intel only: Off (Intel mode)", StateMapper.summary(Fx.st("gfx_mode", "intel", "gfx_active", "intel")),
                "summary in Intel mode");
        T.eq("NVIDIA: NVIDIA unavailable - base detached", StateMapper.summary(Fx.st("gfx_mode", "nvidia",
                "base", "detached", "gfx_active", "intel", "gfx_reason", "detached")), "summary nvidia detached");
        T.eq("Unavailable", StateMapper.summary(null), "summary without a status");
    }

    // ------------------------------------------------------------------ UI-1B-05 GraphicsPage (27.2)

    static void graphicsPage() {
        T.group("UI-1B-05 GraphicsPage (27.2)");
        for (String[] m : new String[][] {{"auto", "auto"}, {"intel", "intel"}, {"nvidia", "nvidia"}, {"", "auto"},
            {"weird", "auto"}}) {
            T.eq(m[1], GraphicsPage.build(Fx.st("gfx_mode", m[0]), null, DF).mode, "radio for gfx_mode '" + m[0] + "'");
        }
        String[][] reasons = {
            {"nopkg", "Intel (no NVIDIA app graphics on this system)"}, {"nodtx", "Intel (no keyboard base support)"},
            {"kill", "Intel (NVIDIA disabled for this boot)"}, {"test", "Intel (NVIDIA test boot)"},
            {"crashloop", "Intel (NVIDIA switched off after a problem)"}, {"wedged", "Intel (restart required)"},
            {"fault", "Intel (NVIDIA GPU fault)"}, {"mode", "Intel (Intel only mode)"},
            {"detached", "Intel (keyboard base detached)"}, {"detaching", "Intel (preparing to detach)"},
            {"loading", "Intel (starting NVIDIA)"}, {"nodriver", "Intel (NVIDIA driver not running)"},
            {"model", "Intel (this NVIDIA GPU is not supported)"},
            {"untested", "Intel (testing NVIDIA)"}, {"testerror", "Intel (NVIDIA test could not run)"},
            {"selftest", "Intel (NVIDIA self-test failed)"}, {"bogus", "Intel"}, {"", "Intel"}, {"ok", "Intel"}};
        for (String[] r : reasons) {
            T.eq("Apps you open now use: " + r[1], GraphicsPage.build(Fx.st("gfx_active", "intel", "gfx_reason", r[0]),
                    null, DF).statusLine, "status line for reason '" + r[0] + "'");
        }
        T.eq("Apps you open now use: NVIDIA", GraphicsPage.build(Fx.st(), null, DF).statusLine, "status line nvidia");
        T.eq("NVIDIA", Texts.reasonText("ok"), "27.6 ok -> NVIDIA");
        // self-test line
        String[][] tests = {
            {"running", "1", "0", "", "Self-test: Running..."},
            {"pass", "1", "1761390000", "", "Self-test: Passed on D1761390000"},
            {"fail", "0", "1761390001", "", "Self-test: Failed on D1761390001"},
            {"fail", "0", "1761390001", "ahb-i915-readback", "Self-test: Failed on D1761390001: ahb-i915-readback"},
            {"error", "-2", "1761390002", "", "Self-test: Could not run"},
            {"error", "-2", "1761390002", "icd", "Self-test: Could not run: icd"},
            {"pending", "-1", "0", "", "Self-test: Not run yet"},
            {"none", "-1", "0", "", "Self-test: Not run yet"},
            {"weird", "1", "5", "", "Self-test: Passed on D5"},
            {"weird", "0", "5", "", "Self-test: Failed on D5"},
            {"weird", "-2", "5", "", "Self-test: Could not run"},
            {"weird", "-1", "0", "", "Self-test: Not run yet"},
            {"pass", "1", "0", "", "Self-test: Passed on an unknown date"}};
        for (String[] t : tests) {
            String line = Fx.line("gfx_test", t[0], "gfx_ok", Integer.parseInt(t[1]), "gfx_test_t", Long.parseLong(t[2]));
            if (!t[3].isEmpty()) line = line.replace(",\"t_ms\"", ",\"gfx_test_reason\":\"" + t[3] + "\",\"t_ms\"");
            T.eq(t[4], GraphicsPage.build(stWith(line), null, DF).selfTestLine, "self-test line test=" + t[0]
                    + " ok=" + t[1] + " reason=" + t[3]);
        }
        // Run self-test again: enabled exactly per 27.2 item 4 (oracle over every combination)
        int combos = 0;
        int mism = 0;
        for (int cap : new int[] {0, 1}) {
            for (String mode : new String[] {"auto", "intel", "nvidia"}) {
                for (String state : new String[] {"standby", "busy:2", "off", "loading", "wedged", "fault", "detaching",
                    "unavailable"}) {
                    for (String test : new String[] {"pass", "running", "none"}) {
                        for (String pend : new String[] {"", "intel", "detach"}) {
                            for (int wedged : new int[] {0, 1}) {
                                for (String fault : new String[] {"", "rpm-error"}) {
                                    Status s = Fx.st("gfx_cap", cap, "gfx_mode", mode, "state", state, "gfx_test", test,
                                            "pending", pend, "wedged", wedged, "fault_reason", fault);
                                    boolean want = cap == 1 && !"intel".equals(mode)
                                            && ("standby".equals(state) || state.startsWith("busy:"))
                                            && !"running".equals(test) && pend.isEmpty() && wedged == 0
                                            && fault.isEmpty();
                                    combos++;
                                    if (GraphicsPage.build(s, null, DF).rerunEnabled != want) mism++;
                                }
                            }
                        }
                    }
                }
            }
        }
        T.check(mism == 0, "Run self-test again enable rule: " + combos + " combinations, " + mism + " mismatches");
        // pending line and Close apps
        Status pend = Fx.st("gfx_mode", "intel", "gfx_active", "intel", "gfx_reason", "mode", "pending", "intel",
                "state", "busy:2", "refcnt", 2, "holders", Fx.holders(Fx.holder(10, "game", 10500, "com.example.game",
                        true), Fx.holder(11, "chat", 10501, "com.example.chat", true)));
        GraphicsPage p = GraphicsPage.build(pend, Fx.labels("com.example.game", "Space Game"), DF);
        T.eq("Space Game, com.example.chat still use the NVIDIA GPU.", p.pendingLine, "pending line with labels");
        T.check(GraphicsPage.build(Fx.st(), null, DF).pendingLine == null, "no pending line without pending");
        T.check(GraphicsPage.build(Fx.st("pending", "detach"), null, DF).pendingLine == null,
                "no pending line for a pending detach");
        T.eq("2 app(s) still use the NVIDIA GPU.", GraphicsPage.build(Fx.st("pending", "intel", "gfx_mode", "intel",
                "state", "busy:2"), null, DF).pendingLine, "pending line without a holder list");
        // GPU line
        T.eq("NVIDIA GPU: asleep (D3cold)", GraphicsPage.build(Fx.st(), null, DF).gpuLine, "GPU asleep");
        T.eq("NVIDIA GPU: active", GraphicsPage.build(Fx.st("power_state", "D0"), null, DF).gpuLine, "GPU active");
        T.eq("NVIDIA GPU: no driver", GraphicsPage.build(Fx.st("driver", "", "state", "off"), null, DF).gpuLine,
                "GPU no driver");
        T.eq("NVIDIA GPU: not supported (no driver)", GraphicsPage.build(Fx.st("driver", "", "state", "off",
                "gfx_active", "intel", "gfx_reason", "model"), null, DF).gpuLine, "GPU not supported (B-4 model)");
        T.eq("NVIDIA GPU: no driver", GraphicsPage.build(Fx.st("driver", "", "state", "off", "gfx_active", "intel",
                "gfx_reason", "nodriver"), null, DF).gpuLine, "GPU no driver for reason nodriver");
        T.eq("NVIDIA GPU: not connected", GraphicsPage.build(Fx.st("bdf", "", "gfx_active", "intel", "gfx_reason",
                "model"), null, DF).gpuLine, "GPU not connected wins over model");
        T.eq("Auto: Intel (NVIDIA not supported)", StateMapper.summary(Fx.st("driver", "", "state", "off",
                "gfx_active", "intel", "gfx_reason", "model")), "summary for a model refusal (B-4)");
        T.eq("NVIDIA GPU: not connected", GraphicsPage.build(Fx.st("bdf", "", "state", "unavailable"), null, DF)
                .gpuLine, "GPU not connected");
        Status used = Fx.st("state", "busy:2", "power_state", "D0", "refcnt", 2, "holders", Fx.holders(
                Fx.holder(10, "game", 10500, "com.example.game", true), Fx.holder(11, "chat", 10501, "com.example.chat",
                        true)));
        T.eq("NVIDIA GPU: active, used by 2 app(s): Space Game, com.example.chat", GraphicsPage.build(used,
                Fx.labels("com.example.game", "Space Game"), DF).gpuLine, "GPU used by");
        // Use NVIDIA for all apps
        GraphicsPage g = GraphicsPage.build(Fx.st(), null, DF);
        T.check(!g.allApps && g.allAppsSecondary.equals("Games and the apps marked Use NVIDIA run on NVIDIA"),
                "all-apps switch off + secondary text");
        g = GraphicsPage.build(Fx.st("gfx_policy", "all"), null, DF);
        T.check(g.allApps && g.allAppsSecondary.equals(
                "Every app you installed runs on NVIDIA, except the ones marked Always Intel"),
                "all-apps switch on + secondary text");
        // no socket / cap 0
        GraphicsPage none = GraphicsPage.build(null, null, DF);
        T.check(!none.connected && !none.graphics && !none.rerunEnabled && none.pendingLine == null
                && "Unavailable".equals(none.statusLine), "no socket: Unavailable, every control disabled");
        GraphicsPage cap0 = GraphicsPage.build(Fx.st("gfx_cap", 0, "gfx_active", "intel", "gfx_reason", "nopkg"), null,
                DF);
        T.check(cap0.connected && !cap0.graphics && !cap0.rerunEnabled && "Unavailable".equals(cap0.statusLine)
                && cap0.selfTestLine.isEmpty(), "gfx_cap 0: Unavailable, graphics controls disabled");
        T.eq("idle", GraphicsPage.build(Fx.st("compute", "idle"), null, DF).computeLine, "compute state line");
    }

    // ------------------------------------------------------------------ UI-1B-06 AppRows (24.4)

    /** An independent oracle of INTERFACES 24.4 with sets, as gb_power computes it. */
    static Set<String> oracle(List<CatalogBuilder.Entry> catalog, Map<String, String> prefs, String policy,
            Set<String> installed) {
        Set<String> base = new HashSet<String>();
        for (CatalogBuilder.Entry e : catalog) if ("all".equals(policy) || e.game) base.add(e.pkg);
        Set<String> in = new HashSet<String>(base);
        for (Map.Entry<String, String> e : prefs.entrySet()) if ("nvidia".equals(e.getValue())) in.add(e.getKey());
        for (Map.Entry<String, String> e : prefs.entrySet()) if ("intel".equals(e.getValue())) in.remove(e.getKey());
        in.retainAll(installed);
        return in;
    }

    static void appRows() {
        T.group("UI-1B-06 AppRows (24.4, 27.2 item 6)");
        int n = 0;
        for (boolean game : new boolean[] {true, false}) {
            for (String ch : new String[] {"default", "intel", "nvidia"}) {
                for (String pol : new String[] {"games", "all"}) {
                    boolean want = !"intel".equals(ch) && ("all".equals(pol) || game || "nvidia".equals(ch));
                    T.eq(want, AppRows.effectiveNvidia(game, ch, pol), "24.4 game=" + game + " choice=" + ch
                            + " policy=" + pol);
                    n++;
                }
            }
        }
        T.eq(12, n, "all 12 single-app combinations");
        Random r = new Random(1761390000L);
        int checked = 0;
        int bad = 0;
        for (int round = 0; round < 40; round++) {
            List<CatalogBuilder.Entry> cat = new ArrayList<CatalogBuilder.Entry>();
            Map<String, String> prefs = new HashMap<String, String>();
            List<AppRows.Choice> choices = new ArrayList<AppRows.Choice>();
            Set<String> installed = new HashSet<String>();
            for (int i = 0; i < 60; i++) {
                String pkg = "com.example.a" + i;
                cat.add(new CatalogBuilder.Entry(pkg, "App " + (59 - i), r.nextInt(3) == 0));
                installed.add(pkg);
                int c = r.nextInt(4);
                if (c == 1) prefs.put(pkg, "intel");
                if (c == 2) prefs.put(pkg, "nvidia");
            }
            for (Map.Entry<String, String> e : prefs.entrySet()) choices.add(new AppRows.Choice(e.getKey(), e.getValue()));
            for (String pol : new String[] {"games", "all"}) {
                Set<String> want = oracle(cat, prefs, pol, installed);
                for (AppRows.Row row : AppRows.build(cat, choices, null)) {
                    checked++;
                    if (row.nvidia(pol) != want.contains(row.pkg)) bad++;
                    if (!row.effectiveText(pol).equals(want.contains(row.pkg) ? "NVIDIA" : "Intel")) bad++;
                }
            }
        }
        T.check(bad == 0, "effective graphics == the 24.4 oracle: " + checked + " rows (40 random catalogs x 2 "
                + "policies), " + bad + " mismatches");
        // order: games first, then by label (case-insensitive)
        List<CatalogBuilder.Entry> cat = Arrays.asList(new CatalogBuilder.Entry("com.b.app", "beta", false),
                new CatalogBuilder.Entry("com.z.game", "Zed", true), new CatalogBuilder.Entry("com.a.app", "Alpha",
                        false), new CatalogBuilder.Entry("com.c.game", "chess", true));
        List<AppRows.Row> rows = AppRows.build(cat, null, null);
        StringBuilder order = new StringBuilder();
        for (AppRows.Row x : rows) order.append(x.label).append(',');
        T.eq("chess,Zed,Alpha,beta,", order.toString(), "rows: games first, then by label");
        // switches: one choice per package
        AppRows.Row row = new AppRows.Row("com.example.game", "Game", false, "intel", false);
        AppRows.Change c = AppRows.change(row.pkg, AppRows.NVIDIA, true);
        T.eq("set-app-nvidia", c.cmd, "Use NVIDIA on -> set-app-nvidia");
        T.eq("com.example.game:1", c.arg, "argument <pkg>:1");
        T.check(GbPowerClient.validRequest(c.cmd, c.arg), "the change is a valid request");
        row.choice = c.choice;
        T.check(row.useNvidia() && !row.alwaysIntel(), "Use NVIDIA on turns Always Intel off");
        c = AppRows.change(row.pkg, AppRows.INTEL, true);
        T.check("set-app-intel".equals(c.cmd) && "com.example.game:1".equals(c.arg), "Always Intel on -> set-app-intel :1");
        row.choice = c.choice;
        T.check(row.alwaysIntel() && !row.useNvidia(), "Always Intel on turns Use NVIDIA off");
        c = AppRows.change(row.pkg, AppRows.INTEL, false);
        T.check("set-app-intel".equals(c.cmd) && "com.example.game:0".equals(c.arg) && "default".equals(c.choice),
                "Always Intel off -> set-app-intel :0, default");
        c = AppRows.change(row.pkg, AppRows.NVIDIA, false);
        T.check("set-app-nvidia".equals(c.cmd) && "com.example.game:0".equals(c.arg), "Use NVIDIA off -> :0");
        // replies: success takes gb_power's choice, failure reverts
        try {
            row.choice = "nvidia";
            boolean ok = AppRows.apply(row, "intel", GbPowerClient.Outcome.replied(Reply.parse(
                    "{\"v\":1,\"ok\":true,\"cmd\":\"set-app-nvidia\",\"pkg\":\"com.example.game\",\"choice\":\"nvidia\","
                            + "\"effect\":\"restart-app\"}")));
            T.check(ok && row.useNvidia(), "a successful set-app-nvidia keeps Use NVIDIA");
            row.choice = "nvidia";
            ok = AppRows.apply(row, "intel", GbPowerClient.Outcome.replied(Reply.parse(
                    "{\"v\":1,\"ok\":false,\"cmd\":\"set-app-nvidia\",\"err\":\"ENOENT\",\"msg\":\"package not installed\"}")));
            T.check(!ok && row.alwaysIntel(), "a failed set-app-nvidia reverts the switch (back to Always Intel)");
            row.choice = "default";
            ok = AppRows.apply(row, "default", GbPowerClient.Outcome.fail(GbPowerClient.Failure.TIMEOUT, "x"));
            T.check(!ok && "default".equals(row.choice), "a timeout reverts the switch");
        } catch (Reply.ProtocolException e) {
            T.check(false, "reply parse: " + e);
        }
        // holder hints
        List<Holder> hs = Arrays.asList(Fx.h(10, "game", 10500, "com.z.game", true), Fx.h(11, "a", 10501, "com.a.app",
                true));
        rows = AppRows.build(cat, Arrays.asList(new AppRows.Choice("com.z.game", "intel")), hs);
        for (AppRows.Row x : rows) {
            if ("com.z.game".equals(x.pkg)) {
                T.eq("Using NVIDIA now - restart the app to switch", x.hint("games"),
                        "a holder marked Always Intel (effective Intel): restart hint");
                T.eq("Using NVIDIA now - restart the app to switch", x.hint("all"),
                        "Always Intel wins under the all-apps policy too");
            } else if ("com.a.app".equals(x.pkg)) {
                T.eq("Using NVIDIA now - restart the app to switch", x.hint("games"),
                        "a non-game holder under the games policy (effective Intel): restart hint");
                T.eq("Using NVIDIA now", x.hint("all"), "a holder whose effective graphics is NVIDIA: Using NVIDIA now");
            } else {
                T.eq(null, x.hint("games"), "no hint for a non-holder " + x.pkg);
            }
        }
        T.check(AppRows.Choice.from(null) == null, "Choice.from(null)");
        Map<String, Object> bad1 = new HashMap<String, Object>();
        bad1.put("pkg", "com.x.y");
        bad1.put("choice", "default");
        T.check(AppRows.Choice.from(bad1) == null, "get-apps entry with choice 'default' ignored");
    }

    // ------------------------------------------------------------------ UI-1B-07 CatalogBuilder (24.3)

    static void catalog() {
        T.group("UI-1B-07 CatalogBuilder (24.3, 27.5)");
        int sys = CatalogBuilder.FLAG_SYSTEM;
        int updated = CatalogBuilder.FLAG_SYSTEM | 0x80; // FLAG_UPDATED_SYSTEM_APP
        List<CatalogBuilder.App> in = Arrays.asList(
                new CatalogBuilder.App("com.android.chrome", "Chrome", updated, -1),
                new CatalogBuilder.App("com.android.settings", "Settings", sys, -1),
                new CatalogBuilder.App("com.google.game.stock", "Stock game", sys, CatalogBuilder.CATEGORY_GAME),
                new CatalogBuilder.App("gb.sb3.gpu", "NVIDIA GPU", 0, -1),
                new CatalogBuilder.App("gb.sb3.gfxdriver", "NVIDIA app graphics", 0, -1),
                new CatalogBuilder.App("com.example.cat", "Cat game", 0, CatalogBuilder.CATEGORY_GAME),
                new CatalogBuilder.App("com.example.legacy", "Legacy game", CatalogBuilder.FLAG_IS_GAME, -1),
                new CatalogBuilder.App("com.example.audio", "Music", 0, 1),
                new CatalogBuilder.App("com.example.notes", "Notes", 0, -1),
                new CatalogBuilder.App("com.example.notes", "Notes 2nd activity", 0, -1),
                new CatalogBuilder.App("bad", "No dot", 0, -1),
                new CatalogBuilder.App("com.example.legacy", "Legacy game again", 0, -1));
        List<CatalogBuilder.Entry> out = CatalogBuilder.build(in);
        StringBuilder b = new StringBuilder();
        for (CatalogBuilder.Entry e : out) b.append(e).append(' ');
        T.eq("g:com.example.cat g:com.example.legacy a:com.example.audio a:com.example.notes ", b.toString(),
                "FLAG_SYSTEM (stock and updated) and gb.sb3.* excluded; CATEGORY_GAME or FLAG_IS_GAME -> g; games first");
        T.check(CatalogBuilder.isGame(0, CatalogBuilder.CATEGORY_GAME) && CatalogBuilder.isGame(0x02000000, -1)
                && !CatalogBuilder.isGame(0, -1) && !CatalogBuilder.isGame(0, 1), "isGame rule");
        List<String> lines = CatalogBuilder.lines(out);
        T.eq(Arrays.asList("apps-begin 4", "apps-add g:com.example.cat,com.example.legacy",
                "apps-add a:com.example.audio,com.example.notes", "apps-commit 4"), lines, "the push lines");
        // 600 apps with long names: at most 512, games first, every line <= 255 bytes incl. the newline
        List<CatalogBuilder.App> many = new ArrayList<CatalogBuilder.App>();
        StringBuilder pad = new StringBuilder();
        while (pad.length() < 90) pad.append('x');
        for (int i = 0; i < 600; i++) {
            boolean game = i % 2 == 0;
            many.add(new CatalogBuilder.App("com.example." + pad + ".p" + String.format("%04d", i), "App " + i, 0,
                    game ? CatalogBuilder.CATEGORY_GAME : -1));
        }
        out = CatalogBuilder.build(many);
        T.eq(512, out.size(), "at most 512 entries");
        int games = 0;
        boolean gamesFirst = true;
        boolean seenOther = false;
        for (CatalogBuilder.Entry e : out) {
            if (e.game) {
                games++;
                if (seenOther) gamesFirst = false;
            } else {
                seenOther = true;
            }
        }
        T.check(games == 300 && gamesFirst, "all 300 games kept, games first (" + games + ")");
        lines = CatalogBuilder.lines(out);
        int maxLen = 0;
        int pkgs = 0;
        boolean valid = true;
        boolean gThenA = true;
        boolean sawA = false;
        for (String l : lines) {
            maxLen = Math.max(maxLen, (l + "\n").getBytes(java.nio.charset.StandardCharsets.US_ASCII).length);
            valid &= GbPowerClient.validLine(l);
            if (l.startsWith("apps-add ")) {
                pkgs += l.substring(11).split(",").length;
                if (l.startsWith("apps-add a:")) sawA = true;
                if (l.startsWith("apps-add g:") && sawA) gThenA = false;
            }
        }
        T.check(maxLen <= 255, "every request line <= 255 bytes including the newline (max " + maxLen + ")");
        T.check(valid, "every line passes the client's request validation");
        T.check(gThenA, "g lines before a lines");
        T.eq(512, pkgs, "the apps-add lines carry exactly the 512 packages");
        T.eq("apps-begin 512", lines.get(0), "apps-begin count");
        T.eq("apps-commit 512", lines.get(lines.size() - 1), "apps-commit count = apps-begin count");
        T.eq(Arrays.asList("apps-begin 0", "apps-commit 0"), CatalogBuilder.lines(CatalogBuilder.build(
                new ArrayList<CatalogBuilder.App>())), "an empty catalog is still committed (apps-begin 0)");
    }

    // ------------------------------------------------------------------ UI-1B-08 NotifModel (12.3, 27.3)

    static void notifModel() {
        T.group("UI-1B-08 NotifModel (12.3 v1.2, 27.3)");
        T.check(NotifModel.inUse(Fx.st(), null) == null, "no in-use notification while standby");
        T.check(NotifModel.inUse(null, null) == null, "no in-use notification without a status");
        String[] hs = new String[7];
        for (int i = 0; i < 7; i++) hs[i] = Fx.holder(100 + i, "p" + i, 10500 + i, "com.example.p" + i, true);
        Status seven = Fx.st("state", "busy:7", "refcnt", 7, "holders", Fx.holders(hs));
        String[] m = NotifModel.inUse(seven, Fx.labels("com.example.p0", "Zero", "com.example.p2", "Two"));
        T.eq("NVIDIA GPU in use", m[0], "in-use title");
        T.eq("NVIDIA in use by: Zero, com.example.p1, Two, com.example.p3, com.example.p4 and 2 more", m[1],
                "in-use text: label, else package; at most 5 then 'and N more'");
        Status mixed = Fx.st("state", "busy:3", "refcnt", 4, "holders", Fx.holders(
                Fx.holder(1, "game", 10500, "com.example.game", true), Fx.holder(2, "game:render", 10500,
                        "com.example.game", true), Fx.holder(3, "nvk_probe", 0, "", false),
                Fx.holder(4, "gb_gpu_compute", 9999, "", false)));
        T.eq("NVIDIA in use by: Game, nvk_probe, NVIDIA compute service", NotifModel.inUse(mixed,
                Fx.labels("com.example.game", "Game"))[1], "one name per app; comm for root; the compute service");
        T.eq("NVIDIA in use by: 2 app(s)", NotifModel.inUse(Fx.st("state", "busy:2"), null)[1],
                "empty (rate-limited) holder list: N app(s)");
        // detach notification
        Status busy = Fx.st("state", "busy:1", "detach_ref", 1);
        T.eq("post seen=1", NotifModel.detach(busy, 0, true).toString(), "detach_ref 1 > seen 0: post");
        T.eq("keep seen=1", NotifModel.detach(busy, 1, true).toString(), "detach_ref 1 = seen 1: nothing");
        T.eq("post seen=2", NotifModel.detach(Fx.st("state", "busy:1", "detach_ref", 2), 1, true).toString(),
                "detach_ref grows to 2: post again");
        T.eq("post seen=1", NotifModel.detach(busy, 5, false).toString(), "seen from an earlier boot counts as 0");
        T.eq("post seen=1", NotifModel.detach(busy, 5, true).toString(),
                "detach_ref went backwards (gb_power restarted): counts from 0");
        T.eq("keep seen=0", NotifModel.detach(Fx.st("state", "busy:1", "detach_ref", 0), 3, true).toString(),
                "restarted gb_power with no refusal yet: nothing");
        T.eq("cancel seen=1", NotifModel.detach(Fx.st("state", "standby", "detach_ref", 1), 0, true).toString(),
                "no longer busy: cancel");
        T.eq("cancel seen=2", NotifModel.detach(Fx.st("state", "busy:1", "detach_ref", 2, "base", "detached"), 1, true)
                .toString(), "base not attached: cancel");
        T.eq("post seen=1", NotifModel.detach(Fx.st("state", "busy:1", "detach_ref", 1, "base", "not-feasible"), 0,
                true).toString(), "not-feasible counts as attached");
        T.eq("keep seen=4", NotifModel.detach(null, 4, true).toString(), "no status: nothing changes");
        String[] d = NotifModel.detachText(Arrays.asList(Fx.h(1, "game", 10500, "com.example.game", true)),
                Fx.labels("com.example.game", "Space Game"), 1);
        T.eq("Can't detach yet", d[0], "detach title");
        T.eq("Space Game are using the NVIDIA GPU. Close them to detach the screen.", d[1], "detach text");
        T.eq("notify", NotifModel.uiCaps(true, false), "ui-caps notify: notifications on, channel not blocked");
        T.eq("none", NotifModel.uiCaps(false, false), "ui-caps none: notifications off");
        T.eq("none", NotifModel.uiCaps(true, true), "ui-caps none: gb_gpu_detach channel blocked");
        // action targets
        T.eq("detach", HoldersModel.Mode.DETACH.extra(), "Close apps and detach -> HoldersActivity mode detach");
        T.eq("close", HoldersModel.Mode.CLOSE.extra(), "Close apps -> HoldersActivity mode close");
        T.eq(HoldersModel.Mode.DETACH, HoldersModel.Mode.parse("detach"), "mode extra round trip detach");
        T.eq(HoldersModel.Mode.INTEL, HoldersModel.Mode.parse("intel"), "mode extra round trip intel");
        T.eq(HoldersModel.Mode.CLOSE, HoldersModel.Mode.parse(null), "missing mode extra -> close");
        T.eq(HoldersModel.Mode.CLOSE, HoldersModel.Mode.parse("bogus"), "unknown mode extra -> close");
        T.check(Texts.CH_DETACH_ID.equals("gb_gpu_detach") && Texts.CH_DETACH_NAME.equals("Detach")
                && Texts.NOTIF_DETACH == 3 && Texts.NOTIF_IN_USE == 1, "channel gb_gpu_detach 'Detach', ids 1 and 3");
        T.check(Texts.ACTION_CLOSE_AND_DETACH.equals("Close apps and detach") && Texts.ACTION_CANCEL.equals("Cancel")
                && Texts.ACTION_CLOSE_APPS.equals("Close apps"), "action labels");
    }

    // ------------------------------------------------------------------ UI-1B-09 HoldersModel (27.4)

    static void holdersModel() {
        T.group("UI-1B-09 HoldersActivity logic (27.4)");
        T.eq("Close apps using the NVIDIA GPU?", HoldersModel.title(HoldersModel.Mode.CLOSE), "close title");
        T.eq("These apps will be closed. Unsaved work in them may be lost:", HoldersModel.text(HoldersModel.Mode.CLOSE),
                "close text");
        T.eq("Close apps", HoldersModel.positive(HoldersModel.Mode.CLOSE), "close positive");
        T.eq("Close apps to switch to Intel?", HoldersModel.title(HoldersModel.Mode.INTEL), "intel title");
        T.eq("These apps still use the NVIDIA GPU. They will be closed. Unsaved work in them may be lost:",
                HoldersModel.text(HoldersModel.Mode.INTEL), "intel text");
        T.eq("Close apps", HoldersModel.positive(HoldersModel.Mode.INTEL), "intel positive");
        T.eq("Close apps and detach?", HoldersModel.title(HoldersModel.Mode.DETACH), "detach title");
        T.eq("These apps are using the NVIDIA GPU. They will be closed so the screen can be detached. Unsaved work in "
                + "them may be lost:", HoldersModel.text(HoldersModel.Mode.DETACH), "detach text");
        T.eq("Close apps and detach", HoldersModel.positive(HoldersModel.Mode.DETACH), "detach positive");
        T.eq("Cancel", Texts.HOLDERS_CANCEL, "negative button");
        List<Holder> hs = Arrays.asList(Fx.h(4242, "game", 10500, "com.example.game", true),
                Fx.h(4243, "game:2", 10500, "com.example.game", true), Fx.h(77, "nvk_probe", 0, "", false),
                Fx.h(88, "gb_gpu_compute", 9999, "", false), Fx.h(99, "chat", 10501, "com.example.chat", true));
        Labels.Labeler l = Fx.labels("com.example.game", "Space Game", "com.example.chat", "Chat");
        HoldersModel.Plan p = HoldersModel.plan(HoldersModel.Mode.CLOSE, hs, true, l);
        T.eq("close-holders", p.cmd, "close: close-holders");
        T.eq("4242,4243,99", p.arg, "close: every closable pid");
        T.check(p.stopCompute && !p.detachRequest, "close: + stop-compute when the compute service runs");
        T.eq("Space Game|Chat", Fx.join(p.names), "closable names, one per app");
        T.eq("nvk_probe", Fx.join(p.others), "not closable (the compute service is not listed there)");
        T.eq("These apps will be closed. Unsaved work in them may be lost:\n\u2022 Space Game\n\u2022 Chat\n\u2022 "
                + "NVIDIA compute service\n\nCannot be closed here: nvk_probe", p.message(), "dialog message");
        p = HoldersModel.plan(HoldersModel.Mode.INTEL, hs, false, l);
        T.check("close-holders".equals(p.cmd) && !p.stopCompute, "intel: close-holders");
        p = HoldersModel.plan(HoldersModel.Mode.DETACH, hs, true, l);
        T.check("close-holders-and-detach".equals(p.cmd) && "4242,4243,99".equals(p.arg) && !p.detachRequest,
                "detach: close-holders-and-detach <pids>");
        T.check(GbPowerClient.validRequest(p.cmd, p.arg), "detach command is a valid request");
        p = HoldersModel.plan(HoldersModel.Mode.DETACH, Arrays.asList(Fx.h(88, "gb_gpu_compute", 9999, "", false)),
                true, l);
        T.check(p.cmd == null && p.stopCompute && p.detachRequest && p.canProceed(),
                "detach with only the compute service: stop-compute + detach-request");
        p = HoldersModel.plan(HoldersModel.Mode.CLOSE, Arrays.asList(Fx.h(77, "nvk_probe", 0, "", false)), false, l);
        T.check(!p.canProceed() && p.cmd == null, "only non-closable holders: no positive button");
        List<Holder> many = new ArrayList<Holder>();
        for (int i = 0; i < 20; i++) many.add(Fx.h(1000 + i, "a" + i, 10500 + i, "com.example.a" + i, true));
        p = HoldersModel.plan(HoldersModel.Mode.CLOSE, many, false, null);
        T.check(p.pids.size() == 16 && GbPowerClient.validRequest(p.cmd, p.arg), "at most 16 pids (9.3 limit)");
        // detach progress
        T.eq(HoldersModel.Progress.WAIT, HoldersModel.progress(Fx.st("state", "busy:1", "pending", "detach",
                "detach_ref", 2), 2), "pending: Preparing to detach...");
        T.eq(HoldersModel.Progress.DONE, HoldersModel.progress(Fx.st("dtx", "confirmed", "detach_ref", 2), 2),
                "dtx confirmed: the dialog closes");
        T.eq(HoldersModel.Progress.DONE, HoldersModel.progress(Fx.st("base", "detached", "detach_ref", 2), 2),
                "base already detached: the dialog closes");
        T.eq(HoldersModel.Progress.FAILED, HoldersModel.progress(Fx.st("state", "busy:1", "detach_ref", 3), 2),
                "detach_ref grew: failure text");
        T.eq(HoldersModel.Progress.WAIT, HoldersModel.progress(null, 2), "no status: keep waiting");
        T.eq("Preparing to detach...", Texts.PREPARING_DETACH_DOTS, "progress text");
        T.eq("Some apps could not be closed: Space Game", HoldersModel.failedText(Fx.st("state", "busy:1", "refcnt", 1,
                "holders", Fx.holders(Fx.holder(5, "game", 10500, "com.example.game", true))), l),
                "failure text with the remaining labels");
        T.eq("Some apps could not be closed", HoldersModel.failedText(Fx.st(), l), "failure text without holders");
        // R-16 (INTERFACES 29): detach mode ignores apps that exited meanwhile; ESTALE falls back to detach-request
        T.check(!HoldersModel.refusalShown(HoldersModel.Mode.DETACH, "not-holder"), "R-16 detach: not-holder not shown");
        T.check(HoldersModel.refusalShown(HoldersModel.Mode.DETACH, "system")
                && HoldersModel.refusalShown(HoldersModel.Mode.DETACH, "unknown-pkg")
                && HoldersModel.refusalShown(HoldersModel.Mode.DETACH, "self"), "R-16 detach: other reasons shown");
        T.check(HoldersModel.refusalShown(HoldersModel.Mode.CLOSE, "not-holder")
                && HoldersModel.refusalShown(HoldersModel.Mode.INTEL, "not-holder"), "R-16 close/intel: not-holder shown");
        T.check(HoldersModel.detachFallback(HoldersModel.Mode.DETACH, "close-holders-and-detach", "ESTALE"),
                "R-16 detach + ESTALE: detach-request fallback");
        T.check(!HoldersModel.detachFallback(HoldersModel.Mode.DETACH, "close-holders-and-detach", "EBUSY")
                && !HoldersModel.detachFallback(HoldersModel.Mode.CLOSE, "close-holders", "ESTALE")
                && !HoldersModel.detachFallback(HoldersModel.Mode.DETACH, "close-holders-and-detach", null),
                "R-16 no fallback for other errors or modes");
    }

    // ------------------------------------------------------------------ wave 1 (kept)

    static void legacy(String state, int persist, TileState ts, String subtitle, Tap tap) {
        TileModel m = StateMapper.gpuTile(st(state, persist));
        T.eq(new TileModel(ts, subtitle, tap, StateMapper.busyCount(state)), m,
                "8.3 state=" + state + " persist=" + persist);
    }

    /** UI-03 (v1.1): the legacy 8.3 mapping, still exercised as INTERFACES 8.3 says (gpuTile() is not used by the app). */
    static void legacyMapping() {
        T.group("UI-03 legacy NVIDIA GPU tile mapping (INTERFACES 8.3, v1.1 tests)");
        legacy("off", 0, TileState.INACTIVE, "Off", Tap.ENABLE);
        legacy("off", 1, TileState.INACTIVE, "On at next restart", Tap.ENABLE);
        legacy("crashloop", 0, TileState.INACTIVE, "Off (recovered)", Tap.ENABLE);
        legacy("crashloop", 1, TileState.INACTIVE, "On at next restart", Tap.ENABLE);
        legacy("loading", 0, TileState.ACTIVE, "Starting", Tap.NONE);
        legacy("loading", 1, TileState.ACTIVE, "Starting", Tap.NONE);
        legacy("standby", 1, TileState.ACTIVE, "Standby", Tap.DISABLE);
        legacy("standby", 0, TileState.ACTIVE, "Standby", Tap.DISABLE);
        legacy("busy:1", 1, TileState.ACTIVE, "Busy (1)", Tap.HOLDERS);
        legacy("busy:2", 0, TileState.ACTIVE, "Busy (2)", Tap.HOLDERS);
        legacy("busy:999", 1, TileState.ACTIVE, "Busy (999)", Tap.HOLDERS);
        legacy("detaching", 1, TileState.UNAVAILABLE, "Preparing to detach", Tap.NONE);
        legacy("disabled", 1, TileState.UNAVAILABLE, "Disabled for this boot", Tap.NONE);
        legacy("disabled", 0, TileState.UNAVAILABLE, "Disabled for this boot", Tap.NONE);
        legacy("unavailable", 1, TileState.UNAVAILABLE, "Unavailable", Tap.NONE);
        legacy("wedged", 0, TileState.UNAVAILABLE, "Restart required", Tap.NONE);
        legacy("fault", 1, TileState.UNAVAILABLE, "Fault", Tap.NONE);
        T.eq(new TileModel(TileState.UNAVAILABLE, "Unavailable", Tap.NONE, 0), StateMapper.gpuTile(null),
                "8.3 no status (no socket / hello fails / role other)");
        for (String bad : new String[] {"", "Standby", "OFF", "off ", "busy", "busy:", "busy:0", "busy:01",
                "busy:1000", "busy:-1", "busy:x", "busy:1,2", "sleeping", "loading\n"}) {
            T.eq(new TileModel(TileState.UNAVAILABLE, "Unavailable", Tap.NONE, 0),
                    StateMapper.gpuTile(st(bad.replace("\n", "\\n"), 1)),
                    "8.3 unknown state '" + bad.replace("\n", "\\n") + "'");
        }
        T.eq("Off", StateMapper.gpuTile(st("off", 2)).subtitle, "persist=2 -> Off");
        T.eq("Off", StateMapper.gpuTile(st("off", -1)).subtitle, "persist=-1 -> Off");
        T.eq("Off", StateMapper.gpuTile(stWith(statusLine("off", 1, null).replace("\"persist\":1",
                "\"persist\":\"1\""))).subtitle, "persist as string -> 0 -> Off");
        T.eq(2, StateMapper.gpuTile(st("busy:2", 1)).busy, "busy N");
    }

    static Status dtx(String state, String base, String dtx) {
        return stWith(statusLine(state, 1, null).replace("\"base\":\"attached\"", "\"base\":\"" + base + "\"")
                .replace("\"dtx\":\"ready\"", "\"dtx\":\"" + dtx + "\""));
    }

    static void detachMapping() {
        T.group("Detach tile mapping (INTERFACES 13.2)");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(null), "no status");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(dtx("standby", "attached", "n/a")), "dtx n/a");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(dtx("off", "detached", "ready")), "base detached");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(dtx("off", "unknown", "ready")), "base unknown");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(dtx("off", "not-feasible", "ready")), "not feasible");
        T.eq(new TileModel(TileState.INACTIVE, null, Tap.DETACH, 0), StateMapper.detachTile(dtx("off", "attached",
                "ready")), "attached + ready -> tap = detach-request");
        T.eq(new TileModel(TileState.INACTIVE, null, Tap.DETACH, 0), StateMapper.detachTile(dtx("busy:1",
                "attached", "ready")), "busy GPU: gb_power decides, the tile still sends the request");
        T.eq(new TileModel(TileState.ACTIVE, "In progress", Tap.NONE, 0), StateMapper.detachTile(dtx("detaching",
                "attached", "in-progress")), "detaching");
        T.eq(new TileModel(TileState.ACTIVE, "In progress", Tap.NONE, 0), StateMapper.detachTile(dtx("off",
                "attached", "in-progress")), "EC in progress");
        T.eq(new TileModel(TileState.ACTIVE, "Ready to detach", Tap.NONE, 0), StateMapper.detachTile(dtx("off",
                "attached", "confirmed")), "confirmed");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(dtx("off", "attached", "weird")), "unknown dtx");
    }

    static void texts() {
        T.group("Exact texts (INTERFACES 12.3, 13.2, 27; the spec cross-check is the 'spec' suite)");
        T.eq("Graphics", Texts.TILE_LABEL, "tile label (13.2 v1.2)");
        T.eq("Graphics", Texts.SETTINGS_TITLE, "Settings title (13.2 v1.2)");
        T.eq("Detach screen", Texts.DETACH_LABEL, "detach label");
        T.eq("gb_gpu_status", Texts.CH_STATUS_ID, "status channel id");
        T.eq("NVIDIA GPU status", Texts.CH_STATUS_NAME, "status channel name");
        T.eq("gb_gpu_alerts", Texts.CH_ALERTS_ID, "alerts channel id");
        T.eq("NVIDIA GPU alerts", Texts.CH_ALERTS_NAME, "alerts channel name");
        T.eq(2, Texts.NOTIF_ALERT, "alert notification id");
        T.eq("NVIDIA GPU in use", Texts.IN_USE_TITLE, "in-use title");
        T.eq("NVIDIA in use by: A, B", Texts.inUseText("A, B"), "in-use text");
        for (String s : new String[] {Texts.UNAVAILABLE, Texts.PREPARING_TO_DETACH, Texts.OFF_INTEL_MODE,
            Texts.offIntelBusy(999), Texts.RESTART_REQUIRED, Texts.FAULT, Texts.DISABLED_FOR_THIS_BOOT,
            Texts.INTEL_RECOVERED, Texts.NV_RECOVERED, Texts.INTEL_TEST_BOOT, Texts.UNAVAILABLE_DETACHED,
            Texts.NV_DETACHED, Texts.nvidiaBusy(999), Texts.nvidiaManualBusy(999), Texts.NVIDIA, Texts.NVIDIA_MANUAL,
            Texts.INTEL_STARTING, Texts.NV_STARTING, Texts.INTEL_TESTING, Texts.NV_TESTING, Texts.INTEL_TEST_NOT_RUN,
            Texts.NV_TEST_NOT_RUN, Texts.INTEL_TEST_FAILED, Texts.NV_TEST_FAILED, Texts.INTEL, Texts.NV_NOT_READY,
            Texts.INTEL_NOT_SUPPORTED, Texts.NV_NOT_SUPPORTED}) {
            T.check(s.length() <= 40 && s.matches("[ -~]+"), "subtitle short printable ASCII: " + s);
        }
        // errors of the new commands
        try {
            Reply r = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"run-selftest\",\"err\":\"EBUSY\",\"msg\":\"Intel only mode\"}");
            T.eq("The self-test cannot run now (Intel only mode)", Texts.error("run-selftest",
                    GbPowerClient.Outcome.replied(r)), "run-selftest EBUSY text");
            r = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"set-app-intel\",\"err\":\"ENODEV\",\"msg\":\"x\"}");
            T.eq("No NVIDIA app graphics on this system", Texts.error("set-app-intel", GbPowerClient.Outcome.replied(r)),
                    "set-app-* ENODEV text");
            r = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"ENODEV\",\"msg\":\"x\"}");
            T.eq("This system has no NVIDIA driver", Texts.error("set-mode", GbPowerClient.Outcome.replied(r)),
                    "set-mode ENODEV text");
            r = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"run-selftest\",\"err\":\"EAGAIN\",\"msg\":\"x\"}");
            T.eq("The NVIDIA driver is starting, try again in a moment", Texts.error("run-selftest",
                    GbPowerClient.Outcome.replied(r)), "run-selftest EAGAIN text");
            r = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"set-app-nvidia\",\"err\":\"EBUSY\",\"msg\":\"x\"}");
            T.eq("Too many app choices (128)", Texts.error("set-app-nvidia", GbPowerClient.Outcome.replied(r)),
                    "set-app-* EBUSY text");
            r = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"set-app-nvidia\",\"err\":\"EIO\",\"msg\":\"x\"}");
            T.eq("The setting could not be saved", Texts.error("set-app-nvidia", GbPowerClient.Outcome.replied(r)),
                    "EIO text");
        } catch (Reply.ProtocolException e) {
            T.check(false, "parse: " + e);
        }
    }

    static void json() {
        T.group("JSON reader");
        try {
            Map<String, Object> o = Json.parseObject("{\"a\":\"x\\u0041\\n\\\"\",\"b\":-12,\"c\":[1,{\"d\":true}],"
                    + "\"e\":null,\"f\":1.5e2}");
            T.eq("xA\n\"", o.get("a"), "string escapes");
            T.eq(-12L, o.get("b"), "integer");
            T.eq(true, Json.obj(Json.list(o, "c").get(1)).get("d"), "nested");
            T.check(o.containsKey("e") && o.get("e") == null, "null");
            T.eq(150.0, o.get("f"), "real number");
        } catch (Json.ParseException e) {
            T.check(false, "valid JSON rejected: " + e);
        }
        for (String bad : new String[] {"", "{", "{\"a\":1}x", "{\"a\":01}", "{\"a\":1,}", "{a:1}",
                "{\"a\":\"\u0001\"}", "{\"a\":1,\"a\":2}", "[1,2", "{\"a\":tru}", "{\"a\":99999999999999999999}",
                "{\"a\":\"\\x\"}", "{\"a\":-}", "{\"a\":1.}", deep(20)}) {
            boolean threw = false;
            try {
                Json.parse(bad);
            } catch (Json.ParseException e) {
                threw = true;
            }
            T.check(threw, "malformed JSON rejected: " + (bad.length() > 30 ? bad.substring(0, 30) + "..." : bad));
        }
        try {
            Json.parse(deep(15));
            T.check(true, "depth 15 accepted");
        } catch (Json.ParseException e) {
            T.check(false, "depth 15 accepted");
        }
    }

    static String deep(int n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append("[");
        for (int i = 0; i < n; i++) b.append("]");
        return b.toString();
    }

    static void replies() {
        T.group("Reply and status parsing (INTERFACES 9.4, 9.5 with the v1.2 fields)");
        String full = "{\"v\":1,\"ok\":true,\"cmd\":\"status\",\"state\":\"busy:1\",\"n\":1,"
                + "\"holders\":[{\"pid\":4242,\"comm\":\"nvk_probe\",\"uid\":0,\"user\":0,\"pkg\":\"\",\"closable\":false}],"
                + "\"mode\":\"persist\",\"result\":\"clean\",\"persist\":1,\"clean_boots\":3,\"probation\":0,"
                + "\"used\":1,\"foreign\":1,\"wedged\":0,\"fault_reason\":\"\","
                + "\"bdf\":\"0000:02:00.0\",\"driver\":\"nouveau\",\"runtime_status\":\"active\",\"power_state\":\"D0\","
                + "\"refcnt\":1,\"latch\":\"locked\",\"base\":\"attached\",\"dtx\":\"ready\","
                + "\"compute\":\"stopped\",\"payload\":\"mounted\",\"gfxdriver\":0,"
                + "\"gfx_mode\":\"auto\",\"gfx_policy\":\"games\",\"gfx_active\":\"nvidia\",\"gfx_reason\":\"ok\",\"gfx_ok\":1,"
                + "\"gfx_test\":\"pass\",\"gfx_test_t\":1761390000,\"gfx_cap\":1,\"gfx_seq\":3,\"pending\":\"\","
                + "\"detach_ref\":0,\"apps_sync\":1,\"t_ms\":1234567}";
        Status s = stWith(full);
        T.check(s != null, "9.5 example (with the v1.2 block) parses");
        if (s != null) {
            T.eq("busy:1", s.state, "state");
            T.eq(1, s.holders.size(), "holders");
            T.eq(4242, s.holders.get(0).pid, "holder pid");
            T.eq("nvk_probe", s.holders.get(0).comm, "holder comm");
            T.check(!s.holders.get(0).closable, "holder closable false");
            T.eq("persist", s.mode, "mode");
            T.eq(3, s.cleanBoots, "clean_boots");
            T.eq("nouveau", s.driver, "driver");
            T.eq(1, s.refcnt, "refcnt");
            T.eq("locked", s.latch, "latch");
            T.eq("mounted", s.payload, "payload");
            T.eq(1234567L, s.tMs, "t_ms");
            T.eq("auto", s.gfxMode, "gfx_mode");
            T.eq("games", s.gfxPolicy, "gfx_policy");
            T.eq("nvidia", s.gfxActive, "gfx_active");
            T.eq("ok", s.gfxReason, "gfx_reason");
            T.eq(1, s.gfxOk, "gfx_ok");
            T.eq("pass", s.gfxTest, "gfx_test");
            T.eq(1761390000L, s.gfxTestT, "gfx_test_t");
            T.eq(1, s.gfxCap, "gfx_cap");
            T.eq(3, s.gfxSeq, "gfx_seq");
            T.eq("", s.pending, "pending");
            T.eq(0, s.detachRef, "detach_ref");
            T.eq(1, s.appsSync, "apps_sync");
            T.eq("NVIDIA (busy 1)", StateMapper.graphicsTile(s).subtitle, "9.5 example -> NVIDIA (busy 1)");
        }
        Status old = st("standby", 1);
        T.check(old.gfxCap == 0 && old.appsSync == 1 && "".equals(old.gfxMode) && old.gfxOk == -1
                && "none".equals(old.gfxTest) && "intel".equals(old.gfxActive),
                "a v1.1 status: gfx_cap 0, apps_sync 1 (no push), gfx_ok -1, gfx_test none, gfx_active intel");
        // unknown fields ignored, t_ms alone does not make a change; v1.2 fields do
        Status a = stWith(Fx.line("future_field", Fx.raw("{\"x\":[1,2]}")));
        Status b = stWith(Fx.line("t_ms", 7654321L));
        T.check(a != null && a.sameAs(b), "unknown fields ignored; t_ms excluded from change detection");
        for (Object[] kv : new Object[][] {{"gfx_mode", "intel"}, {"gfx_policy", "all"}, {"gfx_active", "intel"},
            {"gfx_reason", "loading"}, {"gfx_ok", 0}, {"gfx_test", "running"}, {"gfx_test_t", 5L}, {"gfx_cap", 0},
            {"gfx_seq", 9}, {"pending", "intel"}, {"detach_ref", 4}, {"apps_sync", 0}, {"persist", 0}}) {
            T.check(!b.sameAs(Fx.st(kv)), "change detected in " + kv[0]);
        }
        T.check(stWith("{\"v\":1,\"ok\":true,\"cmd\":\"ping\"}") == null, "ping is not a status");
        T.check(stWith("{\"v\":1,\"ok\":false,\"cmd\":\"status\",\"err\":\"EPERM\",\"msg\":\"not allowed\"}") == null,
                "error reply is not a status");
        T.check(stWith(statusLine("standby", 1, null).replace("\"state\":\"standby\"", "\"state\":7")) == null,
                "non-string state is not a status");
        T.check(stWith(statusLine("standby", 1, null).replace("\"cmd\":\"status\"", "\"cmd\":\"hello\"")) == null,
                "other cmd is not a status");
        Status h = stWith(statusLine("busy:2", 1, null).replace("\"holders\":[]",
                "\"holders\":[1,{\"pid\":-3},{\"pid\":77,\"comm\":\"x\",\"uid\":10500,\"user\":0,"
                        + "\"pkg\":\"com.example.game\",\"closable\":true}]"));
        T.eq(1, h == null ? -1 : h.holders.size(), "bad holder entries skipped");
        Status typed = Fx.st("gfx_ok", "1", "gfx_cap", "1", "detach_ref", "x");
        T.check(typed.gfxOk == -1 && typed.gfxCap == 0 && typed.detachRef == 0, "mistyped v1.2 fields take defaults");
        for (String bad : new String[] {"{\"v\":2,\"ok\":true,\"cmd\":\"hello\"}", "{\"ok\":true,\"cmd\":\"x\"}",
                "{\"v\":1,\"cmd\":\"x\"}", "{\"v\":1,\"ok\":true}", "[1]", "hello", "{\"v\":1,\"ok\":\"yes\",\"cmd\":\"x\"}"}) {
            boolean threw = false;
            try {
                Reply.parse(bad);
            } catch (Reply.ProtocolException e) {
                threw = true;
            }
            T.check(threw, "invalid reply rejected: " + bad);
        }
        try {
            Reply e = Reply.parse("{\"v\":1,\"ok\":false,\"cmd\":\"enable\",\"err\":\"EPERM\",\"msg\":\"not allowed\"}");
            T.eq("EPERM", e.err, "error code");
            T.eq("not allowed", e.msg, "error msg");
        } catch (Reply.ProtocolException ex) {
            T.check(false, "error reply parses");
        }
    }

    /** A transport that records writes (request validation must stop bad lines before the socket). */
    static final class Recorder implements Transport {
        final StringBuilder sent = new StringBuilder();

        @Override
        public void connect() {
        }

        @Override
        public int read(byte[] buf, int timeoutMs) {
            return -1;
        }

        @Override
        public void write(byte[] data) {
            sent.append(new String(data, java.nio.charset.StandardCharsets.US_ASCII));
        }

        @Override
        public void close() {
        }
    }

    static void validation() {
        T.group("Request validation (INTERFACES 9.1, 9.3, 26.1)");
        T.check(GbPowerClient.validRequest("status", null), "status");
        T.check(!GbPowerClient.validRequest("status", "x"), "status with argument");
        T.check(!GbPowerClient.validRequest("frobnicate", null), "unknown command");
        T.check(!GbPowerClient.validRequest("compute-state", "idle"), "compute-state is not an app command");
        T.check(!GbPowerClient.validRequest(null, null), "null command");
        T.check(GbPowerClient.validRequest("close-holders", "1,22,333"), "pid list");
        T.check(!GbPowerClient.validRequest("close-holders", null), "pid list missing");
        T.check(!GbPowerClient.validRequest("close-holders", "1,abc"), "pid list with letters");
        T.check(!GbPowerClient.validRequest("close-holders", "1,"), "pid list trailing comma");
        T.check(!GbPowerClient.validRequest("close-holders", "0"), "pid 0");
        T.check(!GbPowerClient.validRequest("close-holders", "12345678"), "pid > 7 digits");
        StringBuilder p17 = new StringBuilder("1");
        for (int i = 2; i <= 17; i++) p17.append(',').append(i);
        T.check(!GbPowerClient.validRequest("close-holders", p17.toString()), "17 pids");
        T.check(GbPowerClient.validRequest("close-holders", p17.substring(0, p17.lastIndexOf(","))), "16 pids");
        T.check(GbPowerClient.validRequest("compute-allow", "com.example.app"), "package");
        T.check(GbPowerClient.validRequest("compute-deny", "a_b.c9"), "package with _ and digits");
        for (String bad : new String[] {"com", "1com.x", "com.", ".com", "com..x", "com.x y", "com.x\n", "com.x;rm",
                "com.\u00e9x"}) {
            T.check(!GbPowerClient.validPackage(bad), "bad package rejected: " + bad.replace("\n", "\\n"));
        }
        StringBuilder longPkg = new StringBuilder("a.");
        while (longPkg.length() < 128) longPkg.append('b');
        T.check(!GbPowerClient.validPackage(longPkg.toString()), "128-char package rejected");
        T.check(GbPowerClient.validPackage(longPkg.substring(0, 127)), "127-char package accepted");
        // v1.2 commands (26.1)
        String[][] ok = {{"get-mode", null}, {"run-selftest", null}, {"list-holders", null}, {"get-apps", null},
            {"get-apps", "0"}, {"get-apps", "128"}, {"set-mode", "auto"}, {"set-mode", "intel"}, {"set-mode", "nvidia"},
            {"set-app-policy", "games"}, {"set-app-policy", "all"}, {"set-app-intel", "com.example.game:1"},
            {"set-app-nvidia", "com.example.game:0"}, {"apps-begin", "0"}, {"apps-begin", "512"},
            {"apps-commit", "3"}, {"apps-add", "g:com.a.b"}, {"apps-add", "a:com.a.b,org.c.d"},
            {"close-holders-and-detach", "4242,77"}, {"ui-caps", "notify"}, {"ui-caps", "none"}};
        for (String[] x : ok) T.check(GbPowerClient.validRequest(x[0], x[1]), "valid: " + x[0] + " " + x[1]);
        String[][] bad = {{"get-mode", "x"}, {"run-selftest", "x"}, {"get-apps", "129"}, {"get-apps", "007"},
            {"get-apps", "-1"}, {"set-mode", null}, {"set-mode", "turbo"}, {"set-mode", "Auto"},
            {"set-app-policy", "some"}, {"set-app-intel", "com.example.game"}, {"set-app-intel", "com.example.game:2"},
            {"set-app-intel", ":1"}, {"set-app-nvidia", "bad:1"}, {"set-app-nvidia", "com.x.y:1:1"},
            {"apps-begin", "513"}, {"apps-begin", "x"}, {"apps-commit", null}, {"apps-add", "x:com.a.b"},
            {"apps-add", "g:"}, {"apps-add", "g:com.a.b,"}, {"apps-add", "g:com.a.b,,c.d"}, {"apps-add", "g:bad"},
            {"close-holders-and-detach", null}, {"close-holders-and-detach", "1,abc"}, {"ui-caps", "maybe"},
            {"ui-caps", null}, {"enable", "x"}};
        for (String[] x : bad) T.check(!GbPowerClient.validRequest(x[0], x[1]), "refused: " + x[0] + " " + x[1]);
        T.check(GbPowerClient.validLine("apps-add g:com.a.b") && !GbPowerClient.validLine("apps-add") && !GbPowerClient
                .validLine("get-mode x"), "validLine splits command and argument");
        // Conn framing limits
        Recorder r = new Recorder();
        Conn c = new Conn(r);
        StringBuilder l254 = new StringBuilder();
        while (l254.length() < 254) l254.append('a');
        try {
            c.send(l254.toString());
            T.check(true, "254 + newline = 255 bytes accepted");
        } catch (IOException e) {
            T.check(false, "254 + newline = 255 bytes accepted");
        }
        boolean refused = false;
        try {
            c.send(l254 + "a");
        } catch (IOException e) {
            refused = true;
        }
        T.check(refused, "256-byte request refused before the socket");
        refused = false;
        try {
            c.send("status\nenable");
        } catch (IOException e) {
            refused = true;
        }
        T.check(refused, "embedded newline refused");
        T.check(!r.sent.toString().contains("enable"), "nothing of the refused lines was written");
        final Recorder rec = new Recorder();
        GbPowerClient cl = new GbPowerClient(new Transport.Factory() {
            @Override
            public Transport create() {
                return rec;
            }
        }, 500);
        T.eq(GbPowerClient.Failure.PROTOCOL, cl.call("compute-allow", "x y.z").failure,
                "client refuses an invalid argument without connecting");
        T.eq(GbPowerClient.Failure.PROTOCOL, cl.call("set-mode", "turbo").failure, "client refuses set-mode turbo");
        GbPowerClient.Batch bt = cl.exchangeAll(Arrays.asList("apps-begin 1", "apps-add g:bad", "apps-commit 1"), 500);
        T.check(bt.failure == GbPowerClient.Failure.PROTOCOL && rec.sent.length() == 0,
                "exchangeAll refuses a batch with one invalid line before sending anything");
    }
}
