package gb.sb3.gpu.test;

import gb.sb3.gpu.core.AppRows;
import gb.sb3.gpu.core.CatalogBuilder;
import gb.sb3.gpu.core.Conn;
import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.GbPowerClient.Failure;
import gb.sb3.gpu.core.GbPowerClient.Outcome;
import gb.sb3.gpu.core.GraphicsPage;
import gb.sb3.gpu.core.Holder;
import gb.sb3.gpu.core.HoldersModel;
import gb.sb3.gpu.core.Json;
import gb.sb3.gpu.core.NotifModel;
import gb.sb3.gpu.core.Reply;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;
import gb.sb3.gpu.core.WatchSession;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * UI-1B-04 / UI-04 (protocol client against mock_gb_power.py over a real UNIX socket), UI-1B-11 / UI-05 (no socket),
 * the HoldersActivity and notification flows against the mock, and the same client against the real gb_power v1.2 host
 * build on the wave-1 tree (no device, no DTX) and on the INTERFACES 26.3 tree (DTX attached, nouveau bound).
 */
final class ProtocolTests {
    private ProtocolTests() {
    }

    /** Control channel of the mock (raw lines, no 255-byte request limit). */
    static final class Ctl {
        private final String path;

        Ctl(String path) {
            this.path = path;
        }

        Map<String, Object> send(String json) {
            UnixTransport t = new UnixTransport(path);
            try {
                t.connect();
                t.write((json + "\n").getBytes(StandardCharsets.UTF_8));
                // the control channel has no 4096-byte line limit (the request log grows long)
                java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                for (;;) {
                    int r = t.read(buf, 5000);
                    if (r < 0) break;
                    b.write(buf, 0, r);
                    if (r > 0 && buf[r - 1] == '\n') break;
                }
                String l = new String(b.toByteArray(), StandardCharsets.UTF_8).trim();
                return l.isEmpty() ? null : Json.parseObject(l);
            } catch (IOException | Json.ParseException e) {
                T.check(false, "ctl " + json + ": " + e);
                return null;
            } finally {
                t.close();
            }
        }
    }

    /** Collects watch callbacks. */
    static final class Rec implements WatchSession.Listener {
        final List<Status> statuses = Collections.synchronizedList(new ArrayList<Status>());
        final List<String> downs = Collections.synchronizedList(new ArrayList<String>());
        volatile long firstDownAt;

        @Override
        public void onStatus(Status s) {
            statuses.add(s);
        }

        @Override
        public void onUnavailable(String why) {
            if (firstDownAt == 0) firstDownAt = System.currentTimeMillis();
            downs.add(why);
        }

        Status last() {
            synchronized (statuses) {
                return statuses.isEmpty() ? null : statuses.get(statuses.size() - 1);
            }
        }

        boolean sawState(String s) {
            synchronized (statuses) {
                for (Status x : statuses) if (s.equals(x.state)) return true;
            }
            return false;
        }
    }

    static WatchSession.Options fast() {
        WatchSession.Options o = new WatchSession.Options();
        o.lineTimeoutMs = 3000;
        o.callTimeoutMs = 1000;
        o.pollMs = 200;
        o.backoffMs = new int[] {200, 300, 500};
        return o;
    }

    static final String GET_MODE_26_3 = "{\"v\":1,\"ok\":true,\"cmd\":\"get-mode\",\"mode\":\"auto\",\"policy\":\"games\","
            + "\"active\":\"nvidia\",\"reason\":\"ok\",\"gfx_ok\":1,\"test\":\"pass\",\"test_t\":1761390000,\"cap\":1,"
            + "\"base\":\"attached\",\"pending\":\"\",\"apps_sync\":1,\"prefs_n\":0,\"opt_in_n\":0}";

    /** The first tree of INTERFACES 26.3 in the mock's world terms. */
    static final String GFX_TREE = "{\"set\":{\"state\":\"standby\",\"driver\":\"nouveau\",\"refcnt\":0,\"holders\":[],"
            + "\"result\":\"clean\",\"mode\":\"auto\",\"base\":\"attached\",\"dtx\":\"ready\",\"cap\":1,\"stack\":1,"
            + "\"gfx_ok\":1,\"test_t\":1761390000,\"catalog\":[],\"catalog_gen\":0,\"pkgs_gen\":0,\"prefs\":{},"
            + "\"gfx_mode\":\"\",\"gfx_apps\":\"\",\"power_state\":\"D3cold\",\"runtime_status\":\"suspended\","
            + "\"booted\":1,\"kill\":0,\"boot_nv\":\"\",\"wedged\":0,\"keep\":0,\"noload\":0,\"recovered\":0,"
            + "\"test_running\":0,\"pend_detach\":0,\"detach_ref\":0,\"st_finishing\":0,\"fault_reason\":\"\","
            + "\"latch\":\"locked\",\"compute\":\"stopped\",\"compute_running\":0,\"decision\":0}}";

    static String line(UnixTransport t, Conn c, String req) throws IOException, Reply.ProtocolException {
        c.send(req);
        return c.readLine(2000);
    }

    // ------------------------------------------------------------------ mock

    static void mock(String sock, String ctlPath) {
        final Ctl ctl = new Ctl(ctlPath);
        final GbPowerClient cl = new GbPowerClient(UnixTransport.factory(sock), 2000);

        T.group("UI-04 golden transcripts (INTERFACES 9.6) through the app client");
        ctl.send("{\"uid\":10123,\"set\":{\"booted\":1,\"mode\":\"default\",\"state\":\"off\",\"gfx_mode\":\"\"}}");
        golden(cl, ctl, sock);

        T.group("UI-1B-04 golden transcripts (INTERFACES 26.3) through the app's framing");
        ctl.send(GFX_TREE);
        T.check(T.within(2000, () -> "nvidia".equals(st(cl).gfxActive)), "26.3 tree: gfx_active nvidia");
        golden263(ctl, sock);

        T.group("UI-04 hello / status / roles");
        ctl.send(GFX_TREE);
        Outcome h = cl.call("hello");
        T.check(h.ok(), "hello ok: " + h);
        T.eq("ui", h.role(), "role ui");
        T.eq(1L, h.reply.num("api", -1), "api 1");
        T.check(T.within(2000, () -> "nvidia".equals(st(cl).gfxActive)), "nvidia again after the goldens");
        Status st = st(cl);
        T.eq("standby", st.state, "status state");
        T.eq(1, st.probation, "probation derived (mode auto, clean_boots 0 < 3)");
        T.eq("NVIDIA", StateMapper.graphicsTile(st).subtitle, "tile NVIDIA");
        T.eq("Auto: NVIDIA", StateMapper.summary(st), "Settings summary");
        ctl.send("{\"uid\":10999}");
        Outcome other = cl.status();
        T.eq(Failure.ROLE, other.failure, "role other -> ROLE failure");
        T.eq("other", other.role(), "role reported");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.graphicsTile(other.status()), "role other -> Unavailable");
        Outcome raw = cl.exchange("set-mode intel", false);
        T.eq("EPERM", raw.err(), "set-mode as other -> EPERM");
        T.eq("Not allowed", Texts.error("set-mode", raw), "EPERM message");
        ctl.send("{\"uid\":2000}");
        T.eq("shell", cl.call("hello").role(), "uid 2000 -> shell");
        T.check(cl.status().status() != null, "shell may read status");
        ctl.send("{\"uid\":10123}");

        T.group("UI-1B-04 set-mode effects (26.2) and the tile toasts");
        ctl.send(GFX_TREE);
        T.check(T.within(2000, () -> "nvidia".equals(st(cl).gfxActive)), "start in auto with NVIDIA");
        effect(cl, ctl, "intel", null, "now", null);
        T.eq("Off (Intel mode)", StateMapper.graphicsTile(st(cl)).subtitle, "tile Off (Intel mode)");
        T.eq("intel", st(cl).effectiveMode(), "gfx_mode intel");
        effect(cl, ctl, "nvidia", null, "now", null);
        T.check(T.within(2000, () -> "NVIDIA (manual)".equals(StateMapper.graphicsTile(st(cl)).subtitle)),
                "tile NVIDIA (manual) once gfx_active is nvidia again");
        effect(cl, ctl, "auto", null, "now", null);
        effect(cl, ctl, "nvidia", "{\"set\":{\"base\":\"detached\"}}", "next-attach",
                "Takes effect when the keyboard base is attached");
        T.eq("NVIDIA unavailable - base detached", StateMapper.graphicsTile(st(cl)).subtitle,
                "tile NVIDIA unavailable - base detached");
        effect(cl, ctl, "auto", "{\"set\":{\"base\":\"attached\",\"booted\":0}}", "next-boot", "Takes effect after restart");
        effect(cl, ctl, "intel", "{\"set\":{\"booted\":1,\"boot_nv\":\"once\"}}", "next-boot", "Takes effect after restart");
        effect(cl, ctl, "auto", "{\"set\":{\"boot_nv\":\"\",\"kill\":1}}", "next-boot", "Takes effect after restart");
        T.eq("Disabled for this boot", StateMapper.graphicsTile(st(cl)).subtitle, "kill switch: Disabled for this boot");
        effect(cl, ctl, "auto", "{\"set\":{\"kill\":0,\"noload\":1}}", "next-boot", "Takes effect after restart");
        T.eq("Intel (recovered)", StateMapper.graphicsTile(st(cl)).subtitle, "noload: Intel (recovered)");
        effect(cl, ctl, "intel", "{\"set\":{\"noload\":0,\"keep\":1}}", "next-boot", "Takes effect after restart");
        ctl.send("{\"set\":{\"keep\":0,\"gfx_mode\":\"auto\",\"state\":\"busy:2\",\"refcnt\":2,\"holders\":["
                + Fx.holder(4242, "game", 10500, "com.example.game", true) + ","
                + Fx.holder(4243, "chat", 10501, "com.example.chat", true) + "]}}");
        Outcome pend = cl.call("set-mode", "intel");
        T.check(pend.ok() && "pending".equals(pend.reply.str("effect")) && pend.reply.num("n", 0) == 2,
                "set-mode intel with 2 holders -> pending, n 2: " + pend);
        T.eq("Switching to Intel when apps on NVIDIA close", StateMapper.effectToast(pend.reply.str("effect")),
                "pending toast");
        Status ps = st(cl);
        T.eq("intel", ps.pending, "status pending intel");
        T.eq("Off (Intel mode, 2 on NVIDIA)", StateMapper.graphicsTile(ps).subtitle, "tile row 3");
        GraphicsPage page = GraphicsPage.build(ps, Fx.labels("com.example.game", "Space Game"), null);
        T.eq("Space Game, com.example.chat still use the NVIDIA GPU.", page.pendingLine, "page pending line");
        HoldersModel.Plan ip = HoldersModel.plan(HoldersModel.Mode.INTEL, ps.holders, ps.computeRunning(), null);
        Outcome closed = cl.call(ip.cmd, ip.arg);
        T.check(closed.ok() && Json.list(closed.reply.obj, "closed").size() == 2, "intel mode dialog: close-holders");
        T.eq("", st(cl).pending, "pending cleared once the holders are gone");
        // enable / disable are set-mode auto / intel with the v1.1 replies (26.2)
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"base\":\"detached\"}}");
        Outcome en = cl.call("enable");
        T.check(en.ok() && "now".equals(en.reply.str("effect")), "enable while detached: next-attach reported as now");
        ctl.send("{\"set\":{\"base\":\"attached\",\"state\":\"busy:1\",\"refcnt\":1,\"holders\":["
                + Fx.holder(4242, "game", 10500, "com.example.game", true) + "]}}");
        Outcome dis = cl.call("disable");
        T.check(dis.ok() && "now".equals(dis.reply.str("effect")), "disable with a holder: pending reported as now");
        T.eq("intel", st(cl).gfxMode, "disable = set-mode intel");
        ctl.send("{\"set\":{\"stack\":0}}");
        Outcome nodev = cl.call("set-mode", "auto");
        T.eq("ENODEV", nodev.err(), "no stack -> ENODEV");
        T.eq("This system has no NVIDIA driver", Texts.error("set-mode", nodev), "ENODEV message");
        ctl.send("{\"set\":{\"stack\":1,\"wedged\":1,\"state\":\"wedged\"}}");
        T.eq("EBUSY", cl.call("enable").err(), "wedged: enable -> EBUSY");
        T.eq("next-boot", cl.call("set-mode", "auto").reply.str("effect"), "wedged: set-mode -> next-boot");
        T.eq("Restart required", StateMapper.graphicsTile(Fx.st("state", "wedged")).subtitle, "wedged tile");

        T.group("UI-1B-04 error codes mapped to texts");
        ctl.send(GFX_TREE);
        Outcome einval = cl.exchange("set-mode turbo", true);
        T.check("EINVAL".equals(einval.err()) && "Invalid request".equals(Texts.error("set-mode", einval)),
                "EINVAL bad mode -> Invalid request");
        ctl.send("{\"set\":{\"cap\":0}}");
        Outcome capo = cl.call("set-app-policy", "all");
        T.check("ENODEV".equals(capo.err()) && "No NVIDIA app graphics on this system".equals(Texts.error(
                "set-app-policy", capo)), "ENODEV (cap 0) -> text");
        ctl.send("{\"set\":{\"cap\":1,\"test_running\":1}}");
        Outcome busyT = cl.call("run-selftest");
        T.check("EBUSY".equals(busyT.err()) && "The self-test cannot run now (self-test running)".equals(
                Texts.error("run-selftest", busyT)), "EBUSY self-test running -> text: " + busyT);
        ctl.send("{\"set\":{\"test_running\":0,\"st_finishing\":1}}");
        Outcome again = cl.call("run-selftest");
        T.check("EAGAIN".equals(again.err()) && Texts.error("run-selftest", again).contains("try again"),
                "EAGAIN self-test finishing -> try again: " + again);
        ctl.send("{\"set\":{\"st_finishing\":0,\"state\":\"loading\",\"result\":\"loading\"}}");
        T.eq("EAGAIN", cl.call("run-selftest").err(), "EAGAIN while loading");
        T.eq("Intel (starting NVIDIA)", StateMapper.graphicsTile(st(cl)).subtitle, "loading tile row 13");
        ctl.send(GFX_TREE);
        Outcome missing = cl.call("set-app-nvidia", "com.example.missing:1");
        T.check("ENOENT".equals(missing.err()) && "App not installed".equals(Texts.error("set-app-nvidia", missing)),
                "ENOENT -> App not installed");
        T.eq(Failure.PROTOCOL, cl.call("set-app-nvidia", "com.example.game").failure,
                "set-app-* without :0/:1 never reaches the socket");

        T.group("UI-1B-04 run-selftest and the page's self-test line");
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"selftest_ms\":300,\"selftest_result\":\"fail\"}}");
        T.check(T.within(2000, () -> GraphicsPage.build(st(cl), null, null).rerunEnabled), "rerun enabled (auto, standby)");
        Outcome rs = cl.call("run-selftest");
        T.check(rs.ok() && "running".equals(rs.reply.str("test")), "run-selftest -> test running");
        Status running = st(cl);
        T.check("running".equals(running.gfxTest) && !GraphicsPage.build(running, null, null).rerunEnabled
                && "Self-test: Running...".equals(GraphicsPage.build(running, null, null).selfTestLine),
                "while running: Running..., button disabled");
        T.check(T.within(3000, () -> "fail".equals(st(cl).gfxTest)), "the simulated run ends with fail");
        Status failed = st(cl);
        T.check(GraphicsPage.build(failed, null, null).selfTestLine.startsWith("Self-test: Failed on ")
                && "selftest".equals(failed.gfxReason) && "Intel (NVIDIA test failed)".equals(
                        StateMapper.graphicsTile(failed).subtitle), "fail: page line, reason selftest, tile row 16");

        T.group("UI-1B-04 set-app-* (AppRows changes) and get-apps paging");
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"packages\":{\"gb.sb3.gpu\":10123,\"com.example.game\":10500,\"com.example.chat\":10501}}}");
        AppRows.Row row = new AppRows.Row("com.example.chat", "Chat", false, "default", false);
        String before = row.choice;
        AppRows.Change ch = AppRows.change(row.pkg, AppRows.NVIDIA, true);
        row.choice = ch.choice;
        Outcome o1 = cl.call(ch.cmd, ch.arg);
        T.check(AppRows.apply(row, before, o1) && row.useNvidia(), "Use NVIDIA on: " + o1);
        before = row.choice;
        ch = AppRows.change(row.pkg, AppRows.INTEL, true);
        row.choice = ch.choice;
        Outcome o2 = cl.call(ch.cmd, ch.arg);
        T.check(AppRows.apply(row, before, o2) && row.alwaysIntel() && !row.useNvidia(),
                "Always Intel on replaces Use NVIDIA (server and row agree): " + o2);
        before = row.choice;
        ch = AppRows.change(row.pkg, AppRows.NVIDIA, false);
        row.choice = ch.choice;
        Outcome o3 = cl.call(ch.cmd, ch.arg);
        T.check(AppRows.apply(row, before, o3) && row.alwaysIntel(),
                "Use NVIDIA off while Always Intel is set: gb_power keeps intel, the row follows the reply");
        AppRows.Row ghost = new AppRows.Row("com.example.gone", "Gone", false, "default", false);
        ch = AppRows.change(ghost.pkg, AppRows.NVIDIA, true);
        ghost.choice = ch.choice;
        T.check(!AppRows.apply(ghost, "default", cl.call(ch.cmd, ch.arg)) && "default".equals(ghost.choice),
                "a failed set-app-nvidia (ENOENT) reverts the switch");
        StringBuilder prefs = new StringBuilder("{\"set\":{\"prefs\":{");
        StringBuilder pkgs = new StringBuilder("{\"set\":{\"packages\":{\"com.example.game\":10500,\"gb.sb3.gpu\":10123");
        String pad = "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz";
        for (int i = 0; i < 128; i++) {
            String p = String.format("com.example.%s.p%03d", pad, i);
            prefs.append(i == 0 ? "" : ",").append('"').append(p).append("\":\"").append(i % 3 == 0 ? "intel" : "nvidia")
                    .append('"');
            pkgs.append(",\"").append(p).append("\":").append(10600 + i);
        }
        ctl.send(prefs.append("}}}").toString());
        ctl.send(pkgs.append("}}}").toString());
        Outcome page1 = cl.call("get-apps");
        long next = page1.reply.num("next", -1);
        T.check(page1.ok() && next > 0 && next < 128 && page1.reply.line.length() + 1 <= 3500 + 200,
                "get-apps page 1: next " + next + ", " + page1.reply.line.length() + " bytes");
        List<AppRows.Choice> all = cl.getAllChoices();
        T.check(all != null && all.size() == 128, "getAllChoices follows next to the end: " + (all == null ? null
                : all.size()));
        if (all != null && all.size() == 128) {
            T.check(all.get(0).pkg.endsWith(".p000") && "intel".equals(all.get(0).choice)
                    && all.get(127).pkg.endsWith(".p127") && "nvidia".equals(all.get(127).choice), "paged entries in order");
        }
        Outcome full = cl.call("set-app-nvidia", "com.example.game:1");
        T.check("EBUSY".equals(full.err()) && "Too many app choices (128)".equals(Texts.error("set-app-nvidia", full)),
                "129th choice -> EBUSY");

        T.group("UI-1B-04 list-holders parsing and the close/detach dialogs");
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"state\":\"busy:2\",\"refcnt\":3,\"compute_running\":1,\"compute\":\"idle\",\"holders\":["
                + Fx.holder(4242, "game", 10500, "com.example.game", true) + ","
                + Fx.holder(77, "nvk_probe", 0, "", false) + "," + Fx.holder(88, "gb_gpu_compute", 9999, "", false)
                + "]}}");
        Outcome lh = cl.call("list-holders");
        List<Holder> hs = new ArrayList<Holder>();
        for (Object e : Json.list(lh.reply.obj, "holders")) {
            Holder x = Holder.from(e);
            if (x != null) hs.add(x);
        }
        T.check(lh.ok() && "list-holders".equals(lh.reply.cmd) && hs.size() == 3, "list-holders parsed: " + hs);
        HoldersModel.Plan cp = HoldersModel.plan(HoldersModel.Mode.CLOSE, hs, st(cl).computeRunning(), null);
        T.check("close-holders".equals(cp.cmd) && "4242".equals(cp.arg) && cp.stopCompute
                && "nvk_probe".equals(Fx.join(cp.others)), "close plan from list-holders");
        // detach flow: close-holders-and-detach -> Preparing to detach... -> dtx confirmed
        ctl.send("{\"set\":{\"pend_outcome\":\"confirm\",\"holders\":[" + Fx.holder(4242, "game", 10500,
                "com.example.game", true) + "],\"refcnt\":1,\"state\":\"busy:1\",\"compute_running\":0,"
                + "\"compute\":\"stopped\",\"detach_ref\":1}}");
        Status d0 = st(cl);
        HoldersModel.Plan dp = HoldersModel.plan(HoldersModel.Mode.DETACH, d0.holders, d0.computeRunning(), null);
        Outcome cd = cl.call(dp.cmd, dp.arg);
        T.check(cd.ok() && "pending".equals(cd.reply.str("detach")), "close-holders-and-detach -> detach pending: " + cd);
        Status dp1 = st(cl);
        T.check("detach".equals(dp1.pending) && "Preparing to detach".equals(StateMapper.graphicsTile(dp1).subtitle)
                && HoldersModel.progress(dp1, d0.detachRef) == HoldersModel.Progress.WAIT,
                "pending detach: tile Preparing to detach, dialog waits");
        T.check(T.within(3000, () -> HoldersModel.progress(st(cl), d0.detachRef) == HoldersModel.Progress.DONE),
                "dtx confirmed: the dialog closes");
        // the failing variant: the apps do not go, detach_ref grows
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"pend_outcome\":\"timeout\",\"holders\":[" + Fx.holder(5000, "game", 10500,
                "com.example.game", true) + "," + Fx.holder(5001, "chat", 10501, "com.example.chat", true)
                + "],\"refcnt\":2,\"state\":\"busy:2\",\"detach_ref\":3}}");
        Status f0 = st(cl);
        Outcome cd2 = cl.call("close-holders-and-detach", "5000");
        T.check(cd2.ok(), "close-holders-and-detach 5000: " + cd2);
        T.eq("EBUSY", cl.call("close-holders-and-detach", "5001").err(), "a second request while pending -> EBUSY");
        T.check(T.within(3000, () -> HoldersModel.progress(st(cl), f0.detachRef) == HoldersModel.Progress.FAILED),
                "detach_ref grew: Some apps could not be closed");
        T.eq("Some apps could not be closed: com.example.chat", HoldersModel.failedText(st(cl), null),
                "failure text names the remaining app");
        // and the app's detach notification follows detach_ref (NotifModel with the stored value)
        Status f1 = st(cl);
        NotifModel.Detach dn = NotifModel.detach(f1, f0.detachRef, true);
        T.check(dn.post && dn.seen == f0.detachRef + 1, "detach notification posted for the new detach_ref: " + dn);
        T.check(!NotifModel.detach(f1, dn.seen, true).post, "not posted twice for the same detach_ref");
        T.eq("ESTALE", cl.call("close-holders-and-detach", "4242").err(), "not a holder -> ESTALE");
        ctl.send("{\"set\":{\"base\":\"detached\"}}");
        T.eq("ENODEV", cl.call("close-holders-and-detach", "5001").err(), "base detached -> ENODEV");
        T.check(NotifModel.detach(st(cl), 0, true).cancel, "base detached: the detach notification is cancelled");

        T.group("UI-1B-04 ui-caps");
        Outcome uc = cl.call("ui-caps", NotifModel.uiCaps(true, false));
        T.check(uc.ok() && uc.reply.num("notify", -1) == 1, "ui-caps notify -> notify 1");
        uc = cl.call("ui-caps", NotifModel.uiCaps(false, false));
        T.check(uc.ok() && uc.reply.num("notify", -1) == 0, "ui-caps none -> notify 0");

        T.group("UI-1B-04 catalog push (27.5) with exchangeAll");
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"catalog\":null,\"packages\":{\"gb.sb3.gpu\":10123,\"com.example.game\":10500,"
                + "\"com.example.chat\":10501,\"com.example.notes\":10502}}}");
        T.eq(0, st(cl).appsSync, "no catalog yet: apps_sync 0 (the app pushes)");
        List<CatalogBuilder.App> apps = new ArrayList<CatalogBuilder.App>();
        apps.add(new CatalogBuilder.App("com.example.game", "Game", 0, CatalogBuilder.CATEGORY_GAME));
        apps.add(new CatalogBuilder.App("com.example.chat", "Chat", 0, -1));
        apps.add(new CatalogBuilder.App("com.example.notes", "Notes", CatalogBuilder.FLAG_IS_GAME, -1));
        apps.add(new CatalogBuilder.App("gb.sb3.gpu", "NVIDIA GPU", 0, -1));
        apps.add(new CatalogBuilder.App("com.android.settings", "Settings", CatalogBuilder.FLAG_SYSTEM, -1));
        List<String> lines = CatalogBuilder.lines(CatalogBuilder.build(apps));
        GbPowerClient.Batch b = cl.exchangeAll(lines, GbPowerClient.BATCH_TIMEOUT_MS);
        T.check(b.ok(lines.size()), "push committed: " + b);
        Reply commit = b.last();
        T.check(commit != null && commit.num("n", -1) == 3 && commit.num("dropped", -1) == 0
                && commit.num("opt_in_n", -1) == 2 && commit.num("apps_sync", -1) == 1,
                "apps-commit: n 3, dropped 0, opt_in_n 2 (two games), apps_sync 1: " + commit);
        T.eq(1, st(cl).appsSync, "apps_sync 1 after the push");
        Outcome gm = cl.call("get-mode");
        T.eq(2L, gm.reply.num("opt_in_n", -1), "get-mode opt_in_n 2");
        T.eq(3L, cl.call("set-app-policy", "all").reply.num("opt_in_n", -1),
                "policy all: every catalog app opted in (3)");
        ctl.send("{\"set\":{\"pkgs_gen\":1}}");
        T.eq(0, st(cl).appsSync, "a packages.list change: apps_sync 0 again");
        List<CatalogBuilder.App> big = new ArrayList<CatalogBuilder.App>();
        StringBuilder bigPkgs = new StringBuilder("{\"set\":{\"packages\":{");
        for (int i = 0; i < 520; i++) {
            String p = String.format("org.example.%s.app%04d", pad.substring(0, 60), i);
            big.add(new CatalogBuilder.App(p, "App " + i, 0, i % 4 == 0 ? CatalogBuilder.CATEGORY_GAME : -1));
            bigPkgs.append(i == 0 ? "" : ",").append('"').append(p).append("\":").append(20000 + i);
        }
        ctl.send(bigPkgs.append("}}}").toString());
        lines = CatalogBuilder.lines(CatalogBuilder.build(big));
        b = cl.exchangeAll(lines, GbPowerClient.BATCH_TIMEOUT_MS);
        T.check(b.ok(lines.size()) && b.last().num("n", -1) == 512 && b.last().num("apps_sync", -1) == 1,
                "512-entry push in " + lines.size() + " lines on one connection: " + b);
        List<String> broken = new ArrayList<String>(lines.subList(0, 2));
        broken.add("apps-commit 512");
        b = cl.exchangeAll(broken, GbPowerClient.BATCH_TIMEOUT_MS);
        T.check(!b.ok(broken.size()) && b.replies.size() == 3 && "EINVAL".equals(b.last().err),
                "a count mismatch stops the batch with the error reply: " + b);
        ctl.send("{\"set\":{\"cap\":1}}");

        wave1(cl, ctl, sock);
    }

    static void effect(GbPowerClient cl, Ctl ctl, String mode, String setup, String want, String toast) {
        if (setup != null) ctl.send(setup);
        Outcome o = cl.call("set-mode", mode);
        T.check(o.ok() && want.equals(o.reply.str("effect")) && mode.equals(o.reply.str("mode")),
                "set-mode " + mode + (setup == null ? "" : " " + setup) + " -> " + want + ": " + o);
        T.eq(toast, StateMapper.effectToast(o.reply.str("effect")), "toast for " + want);
    }

    /** INTERFACES 26.3 on the mock, byte for byte, with the app's Conn framing. */
    static void golden263(Ctl ctl, String sock) {
        String[][] g = {
            {"ui", "get-mode", GET_MODE_26_3},
            {"ui", "set-mode turbo", "{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"EINVAL\",\"msg\":\"bad mode\"}"},
            {"other", "set-mode intel", "{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"EPERM\",\"msg\":\"not allowed\"}"},
            {"ui", "set-app-intel com.example.game",
                "{\"v\":1,\"ok\":false,\"cmd\":\"set-app-intel\",\"err\":\"EINVAL\",\"msg\":\"bad argument\"}"},
            {"ui", "apps-commit 3", "{\"v\":1,\"ok\":false,\"cmd\":\"apps-commit\",\"err\":\"EINVAL\",\"msg\":\"no apps-begin\"}"},
            {"ui", "ui-caps notify", "{\"v\":1,\"ok\":true,\"cmd\":\"ui-caps\",\"notify\":1}"},
            {"vm", "set-mode intel",
                "{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"ENODEV\",\"msg\":\"no NVIDIA driver on this image\"}"},
            {"ui", "set-mode intel",
                "{\"v\":1,\"ok\":true,\"cmd\":\"set-mode\",\"mode\":\"intel\",\"active\":\"intel\",\"effect\":\"now\",\"n\":0}"},
        };
        UnixTransport t = new UnixTransport(sock);
        try {
            t.connect();
            Conn c = new Conn(t);
            for (String[] x : g) {
                String got;
                if ("other".equals(x[0])) {
                    ctl.send("{\"uid\":10500}");
                    UnixTransport o = new UnixTransport(sock);
                    o.connect();
                    got = line(o, new Conn(o), x[1]);
                    o.close();
                    ctl.send("{\"uid\":10123}");
                } else if ("vm".equals(x[0])) {
                    ctl.send("{\"set\":{\"stack\":0}}");
                    got = line(t, c, x[1]);
                    ctl.send("{\"set\":{\"stack\":1}}");
                } else {
                    got = line(t, c, x[1]);
                }
                T.eq(x[2], got, "26.3 golden '" + x[1] + "' (" + x[0] + ")");
                T.check(Reply.parse(got).cmd.equals(x[1].split(" ")[0]), "26.3 golden reply parses");
            }
        } catch (IOException | Reply.ProtocolException e) {
            T.check(false, "26.3 golden transcript: " + e);
        } finally {
            t.close();
        }
    }

    /** The wave-1 UI-04 groups that still apply (rewritten where v1.2 changed the semantics, TASKS_1B 9). */
    static void wave1(GbPowerClient cl, Ctl ctl, String sock) {
        T.group("UI-04 holders, close-holders, compute allowlist, report, detach");
        ctl.send(GFX_TREE);
        ctl.send("{\"set\":{\"packages\":{\"gb.sb3.gpu\":10123,\"com.example.game\":10500,\"com.example.chat\":10501},"
                + "\"state\":\"busy:2\",\"driver\":\"nouveau\",\"refcnt\":2,\"compute_running\":1,"
                + "\"compute\":\"idle\",\"holders\":["
                + "{\"pid\":4242,\"comm\":\"game\",\"uid\":10500,\"user\":0,\"pkg\":\"com.example.game\",\"closable\":true},"
                + "{\"pid\":77,\"comm\":\"nvk_probe\",\"uid\":0,\"user\":0,\"pkg\":\"\",\"closable\":false}]}}");
        T.check(T.within(2000, () -> "nvidia".equals(st(cl).gfxActive)), "nvidia while busy");
        Status b = st(cl);
        T.eq("NVIDIA (busy 2)", StateMapper.graphicsTile(b).subtitle, "busy tile");
        T.eq(StateMapper.Tap.SET_MODE, StateMapper.graphicsTile(b).tap, "busy tap -> set-mode (never a dialog)");
        T.eq(2, b.holders.size(), "holders in status");
        T.check(b.computeRunning(), "compute running visible");
        String[] note = NotifModel.inUse(b, null);
        T.eq("NVIDIA in use by: com.example.game, nvk_probe", note == null ? null : note[1], "in-use text from status");
        Outcome hs = cl.call("holders");
        T.eq(2, Json.list(hs.reply.obj, "holders").size(), "holders command");
        Outcome bad = cl.exchange("close-holders 1,abc", true);
        T.eq("EINVAL", bad.err(), "close-holders syntax -> EINVAL");
        Outcome stale = cl.call("close-holders", "77");
        T.eq("ESTALE", stale.err(), "closing only a system holder -> ESTALE");
        Outcome closed = cl.call("close-holders", "4242,77");
        T.check(closed.ok(), "close-holders ok: " + closed);
        T.eq(1, Json.list(closed.reply.obj, "closed").size(), "one closed");
        Map<String, Object> ref = Json.obj(Json.list(closed.reply.obj, "refused").get(0));
        T.eq("system", Json.str(ref, "reason", ""), "refused reason system");
        T.eq("NVIDIA (busy 1)", StateMapper.graphicsTile(st(cl)).subtitle, "one holder left");
        T.check(cl.call("stop-compute").ok(), "stop-compute");
        T.eq("stopped", st(cl).compute, "compute stopped");
        T.eq("ENOENT", cl.call("compute-allow", "com.example.missing").err(), "compute-allow unknown pkg");
        Outcome al = cl.call("compute-allow", "com.example.chat");
        T.eq(10501L, al.reply.num("appId", -1), "compute-allow appId");
        Outcome li = cl.call("compute-list");
        T.eq(1, Json.list(li.reply.obj, "allow").size(), "compute-list");
        T.check(cl.call("compute-deny", "com.example.chat").ok(), "compute-deny");
        T.eq("ENOENT", cl.call("compute-deny", "com.example.chat").err(), "compute-deny twice");
        T.eq(Failure.PROTOCOL, cl.call("compute-allow", "bad name").failure, "invalid package never reaches the socket");
        T.check(cl.call("report").ok(), "report");
        T.check(cl.call("detach-request").ok(), "detach-request");
        ctl.send("{\"set\":{\"dtx\":\"n/a\"}}");
        T.eq("ENODEV", cl.call("detach-request").err(), "detach-request without DTX");
        T.eq("Detach control is not available", Texts.error("detach-request", cl.call("detach-request")),
                "detach ENODEV message");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.graphicsTile(st(cl)), "dtx n/a: Graphics tile Unavailable (row 1)");
        ctl.send("{\"set\":{\"dtx\":\"ready\",\"state\":\"off\",\"holders\":[],\"refcnt\":-1,\"driver\":\"\"}}");
        Map<String, Object> log = ctl.send("{\"log\":true}");
        boolean onlyKnown = true;
        for (Object e : Json.list(log, "log")) {
            String line = Json.str(Json.obj(e), "line", "");
            if (!GbPowerClient.validLine(line) && !line.equals("frobnicate") && !line.equals("close-holders 1,abc")
                    && !line.equals("enable") && !line.equals("set-mode turbo") && !line.equals("set-mode intel")
                    && !line.equals("set-app-intel com.example.game") && !line.equals("apps-commit 3")) {
                onlyKnown = false;
                T.check(false, "unexpected request reached the server: " + line);
            }
        }
        T.check(onlyKnown, "server saw only valid requests (plus the deliberate golden ones)");

        T.group("UI-04 malformed replies");
        ctl.send("{\"raw\":[\"not json\"]}");
        T.eq(Failure.PROTOCOL, cl.status().failure, "non-JSON reply -> PROTOCOL");
        ctl.send("{\"raw\":[\"{\\\"v\\\":2,\\\"ok\\\":true,\\\"cmd\\\":\\\"status\\\",\\\"state\\\":\\\"off\\\"}\"]}");
        T.eq(Failure.PROTOCOL, cl.status().failure, "v=2 reply -> PROTOCOL");
        StringBuilder big = new StringBuilder("{\\\"v\\\":1,\\\"ok\\\":true,\\\"cmd\\\":\\\"status\\\",\\\"x\\\":\\\"");
        for (int i = 0; i < 5000; i++) big.append('a');
        big.append("\\\"}");
        ctl.send("{\"raw\":[\"" + big + "\"]}");
        T.eq(Failure.PROTOCOL, cl.status().failure, "reply > 4096 bytes -> PROTOCOL");
        ctl.send("{\"raw\":[\"{\\\"v\\\":1,\\\"ok\\\":true,\\\"cmd\\\":\\\"status\\\",\\\"state\\\":\\\"teleporting\\\"}\"]}");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.graphicsTile(cl.status().status()),
                "a status without the v1.2 fields -> Unavailable");
        T.check(cl.status().status() != null, "normal again after the injected replies");

        T.group("UI-05 deadline: server accepts but never answers");
        ctl.send("{\"stall\":true}");
        long t0 = System.currentTimeMillis();
        Outcome stalled = cl.status();
        long dt = System.currentTimeMillis() - t0;
        T.eq(Failure.TIMEOUT, stalled.failure, "stalled server -> TIMEOUT");
        T.check(dt < 2600, "stalled server: answer within the 2 s deadline (" + dt + " ms)");
        T.info("stalled server: " + stalled.failure + " after " + dt + " ms");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.graphicsTile(stalled.status()), "stalled -> Unavailable");
        GbPowerClient.Batch sb = cl.exchangeAll(java.util.Arrays.asList("apps-begin 0", "apps-commit 0"), 1500);
        T.eq(Failure.TIMEOUT, sb.failure, "stalled server: exchangeAll -> TIMEOUT");
        ctl.send("{\"stall\":false}");

        T.group("UI-04 watch: updates, pings, no t_ms-only updates");
        Rec rec = new Rec();
        WatchSession w = new WatchSession(cl, rec, fast());
        w.start();
        T.check(T.within(2000, () -> rec.last() != null), "watch: first status");
        T.sleep(300);
        int before = rec.statuses.size();
        T.sleep(1200); // several pings (--ping-ms 300) and t_ms changes: no callbacks
        T.eq(before, rec.statuses.size(), "pings and t_ms alone do not produce updates");
        ctl.send("{\"set\":{\"state\":\"standby\",\"driver\":\"nouveau\",\"refcnt\":0}}");
        T.check(T.within(2000, () -> rec.last() != null && "standby".equals(rec.last().state)), "watch: standby pushed");
        T.check(T.within(2000, () -> rec.last() != null && "nvidia".equals(rec.last().gfxActive)),
                "watch: gfx_active nvidia pushed (a v1.2 field change)");
        ctl.send("{\"set\":{\"state\":\"busy:1\",\"refcnt\":1,\"holders\":[{\"pid\":5,\"comm\":\"a\",\"uid\":10500,"
                + "\"user\":0,\"pkg\":\"com.example.game\",\"closable\":true}]}}");
        T.check(T.within(2000, () -> rec.last() != null && "busy:1".equals(rec.last().state)), "watch: busy pushed");
        ctl.send("{\"set\":{\"detach_ref\":7}}");
        T.check(T.within(2000, () -> rec.last() != null && rec.last().detachRef == 7), "watch: detach_ref pushed");
        ctl.send("{\"push\":\"{\\\"v\\\":1,\\\"ok\\\":true,\\\"cmd\\\":\\\"ping\\\"}\"}");
        T.check(rec.downs.isEmpty(), "watch: no unavailable while healthy");

        T.group("UI-04 reconnect after a gb_power restart");
        ctl.send("{\"restart\":1.0}");
        T.check(T.within(2000, () -> !rec.downs.isEmpty()), "restart: unavailable reported");
        ctl.send("{\"set\":{\"state\":\"off\",\"holders\":[],\"refcnt\":-1,\"driver\":\"\"}}");
        T.check(T.within(4000, () -> rec.last() != null && "off".equals(rec.last().state)), "restart: reconnected");
        final int n0 = rec.statuses.size();
        final int d0 = rec.downs.size();
        ctl.send("{\"push\":\"garbage that is not json\"}");
        T.check(T.within(2000, () -> rec.downs.size() > d0), "garbage line in the watch stream -> reconnect");
        T.check(T.within(3000, () -> rec.statuses.size() > n0), "recovered after garbage (status delivered again)");
        w.stop();
        T.check(T.within(1000, () -> !w.running()), "watch stops");

        T.group("UI-04 watch limit: fallback to polling");
        List<UnixTransport> held = new ArrayList<UnixTransport>();
        for (int i = 0; i < 4; i++) {
            UnixTransport t = new UnixTransport(sock);
            try {
                t.connect();
                Conn c = new Conn(t);
                c.request("hello", 2000);
                c.request("watch", 2000);
                held.add(t);
            } catch (IOException | Reply.ProtocolException e) {
                T.check(false, "holding watcher " + i + ": " + e);
            }
        }
        Rec rec2 = new Rec();
        WatchSession w2 = new WatchSession(cl, rec2, fast());
        w2.start();
        T.check(T.within(2000, () -> rec2.last() != null), "5th watcher (EBUSY) still gets a status by polling");
        ctl.send("{\"set\":{\"state\":\"disabled\",\"mode\":\"disabled\"}}");
        T.check(T.within(2000, () -> rec2.sawState("disabled")), "polling sees changes");
        w2.stop();
        for (UnixTransport t : held) t.close();

        T.group("UI-04 connection limit and idle close");
        ctl.send("{\"set\":{\"state\":\"off\",\"mode\":\"default\"}}");
        List<UnixTransport> eight = new ArrayList<UnixTransport>();
        T.sleep(300);
        for (int i = 0; i < 8; i++) {
            UnixTransport t = new UnixTransport(sock);
            try {
                t.connect();
                eight.add(t);
            } catch (IOException e) {
                T.check(false, "connection " + i);
            }
        }
        T.sleep(300);
        Outcome ninth = cl.status();
        T.eq(Failure.HELLO, ninth.failure, "9th connection (EBUSY too many connections) -> HELLO failure");
        T.eq("EBUSY", ninth.detail, "9th connection: the server's EBUSY line was read even if our hello hit EPIPE");
        T.eq(StateMapper.UNAVAILABLE, StateMapper.graphicsTile(ninth.status()), "9th connection -> Unavailable");
        for (UnixTransport t : eight) t.close();
        T.sleep(300);
        T.check(cl.status().status() != null, "served again after the others closed");
        T.sleep(1200); // --idle-ms 800: idle connections are closed by the server; the client opens a new one per call
        T.check(cl.status().status() != null, "new connection per call is unaffected by the idle close");
    }

    static Status st(GbPowerClient cl) {
        Status s = cl.status().status();
        return s != null ? s : UnitTests.st("unavailable", 0);
    }

    /** INTERFACES 9.6 transcripts on one connection, byte for byte (role ui, booted=1, mode=default). */
    static void golden(GbPowerClient cl, Ctl ctl, String sock) {
        String[][] g = {
            {"hello", "{\"v\":1,\"ok\":true,\"cmd\":\"hello\",\"api\":1,\"role\":\"ui\",\"uid\":10123,\"server\":\"gb_power 1\"}"},
            {"frobnicate", "{\"v\":1,\"ok\":false,\"cmd\":\"frobnicate\",\"err\":\"ENOSYS\",\"msg\":\"unknown command\"}"},
            {"enable", "{\"v\":1,\"ok\":true,\"cmd\":\"enable\",\"effect\":\"now\"}"},
            {"close-holders 1,abc", "{\"v\":1,\"ok\":false,\"cmd\":\"close-holders\",\"err\":\"EINVAL\",\"msg\":\"bad pid list\"}"},
        };
        UnixTransport t = new UnixTransport(sock);
        try {
            t.connect();
            Conn c = new Conn(t);
            for (String[] x : g) {
                c.send(x[0]);
                String got = c.readLine(2000);
                T.eq(x[1], got, "golden '" + x[0] + "'");
                Reply r = Reply.parse(got);
                T.check(r.cmd.equals(x[0].split(" ")[0]), "golden reply cmd parsed");
            }
        } catch (IOException | Reply.ProtocolException e) {
            T.check(false, "golden transcript: " + e);
        } finally {
            t.close();
        }
        ctl.send("{\"uid\":10500}");
        Outcome o = cl.exchange("enable", false);
        T.eq("{\"v\":1,\"ok\":false,\"cmd\":\"enable\",\"err\":\"EPERM\",\"msg\":\"not allowed\"}",
                o.reply == null ? null : o.reply.line, "golden 'enable' as role other");
        ctl.send("{\"uid\":10123}");
        T.sleep(800); // let the simulated load from the golden enable finish
        ctl.send("{\"set\":{\"state\":\"off\",\"driver\":\"\",\"refcnt\":-1,\"result\":\"idle\",\"mode\":\"default\"}}");
    }

    // ------------------------------------------------------------------ UI-05 / UI-1B-11

    static void noSocket(String dir) {
        T.group("UI-05 / UI-1B-11 no socket");
        String missing = dir + "/does-not-exist.sock";
        GbPowerClient cl = new GbPowerClient(UnixTransport.factory(missing), 2000);
        long t0 = System.currentTimeMillis();
        Outcome o = cl.status();
        long dt = System.currentTimeMillis() - t0;
        T.eq(Failure.NO_SERVER, o.failure, "missing socket -> NO_SERVER");
        T.check(dt < 2000, "missing socket answered in " + dt + " ms (< 2 s)");
        T.info("missing socket: " + o.failure + " after " + dt + " ms");
        TileModelCheck.unavailable(o.status(), "missing socket");
        String stale = dir + "/stale.sock";
        try {
            java.nio.channels.ServerSocketChannel ss = java.nio.channels.ServerSocketChannel.open(
                    java.net.StandardProtocolFamily.UNIX);
            ss.bind(java.net.UnixDomainSocketAddress.of(stale));
            ss.close(); // the file stays, nobody listens
        } catch (IOException e) {
            T.check(false, "stale socket setup: " + e);
        }
        T.check(new File(stale).exists(), "stale socket file present");
        Outcome o2 = new GbPowerClient(UnixTransport.factory(stale), 2000).status();
        T.eq(Failure.NO_SERVER, o2.failure, "stale socket -> NO_SERVER");
        TileModelCheck.unavailable(o2.status(), "stale socket");
        GbPowerClient.Batch b = cl.exchangeAll(java.util.Arrays.asList("apps-begin 0", "apps-commit 0"), 2000);
        T.eq(Failure.NO_SERVER, b.failure, "catalog push without a socket -> NO_SERVER (retried at the next broadcast)");
        T.check(cl.getAllChoices() == null, "get-apps without a socket -> null (the page disables the switches)");
        Rec rec = new Rec();
        WatchSession w = new WatchSession(cl, rec, fast());
        long t1 = System.currentTimeMillis();
        w.start();
        T.check(T.within(2000, () -> !rec.downs.isEmpty()), "watch reports Unavailable within 2 s");
        T.check(rec.firstDownAt > 0 && rec.firstDownAt - t1 < 2000, "first unavailable after "
                + (rec.firstDownAt - t1) + " ms");
        T.info("watch without a socket: first onUnavailable after " + (rec.firstDownAt - t1) + " ms");
        T.sleep(1500);
        T.eq(1, rec.downs.size(), "unavailable reported once while retrying");
        T.check(rec.statuses.isEmpty(), "no status without a socket");
        w.stop();
        new File(stale).delete();
    }

    /** UI-1B-11: tile Unavailable, page Unavailable with every control disabled. */
    static final class TileModelCheck {
        private TileModelCheck() {
        }

        static void unavailable(Status s, String what) {
            StateMapper.TileModel m = StateMapper.graphicsTile(s);
            T.check(m.state == StateMapper.TileState.UNAVAILABLE && "Unavailable".equals(m.subtitle)
                    && m.tap == StateMapper.Tap.NONE, what + ": tile Unavailable, no tap");
            GraphicsPage p = GraphicsPage.build(s, null, null);
            T.check(!p.connected && !p.graphics && !p.rerunEnabled && "Unavailable".equals(p.statusLine)
                    && p.pendingLine == null, what + ": page Unavailable, every control disabled");
            T.eq("Unavailable", StateMapper.summary(s), what + ": Settings summary Unavailable");
        }
    }

    // ------------------------------------------------------------------ real gb_power v1.2 host build

    /** The wave-1 tree: native, nouveau.ko, GbGpu.apk, no NVIDIA device, no DTX (-D), no driver package. */
    static void real(String sock, String propsDir) {
        T.group("Real gb_power v1.2 (host build, wave-1 tree): golden transcripts and the app client");
        GbPowerClient cl = new GbPowerClient(UnixTransport.factory(sock), 2000);
        String[][] g = {
            {"hello", "{\"v\":1,\"ok\":true,\"cmd\":\"hello\",\"api\":1,\"role\":\"ui\",\"uid\":10123,\"server\":\"gb_power 1\"}"},
            {"frobnicate", "{\"v\":1,\"ok\":false,\"cmd\":\"frobnicate\",\"err\":\"ENOSYS\",\"msg\":\"unknown command\"}"},
            {"enable", "{\"v\":1,\"ok\":true,\"cmd\":\"enable\",\"effect\":\"now\"}"},
            {"close-holders 1,abc", "{\"v\":1,\"ok\":false,\"cmd\":\"close-holders\",\"err\":\"EINVAL\",\"msg\":\"bad pid list\"}"},
        };
        UnixTransport t = new UnixTransport(sock);
        try {
            t.connect();
            Conn c = new Conn(t);
            for (String[] x : g) {
                c.send(x[0]);
                T.eq(x[1], c.readLine(2000), "real golden 9.6 '" + x[0] + "'");
            }
        } catch (IOException | Reply.ProtocolException e) {
            T.check(false, "real golden: " + e);
        } finally {
            t.close();
        }
        Outcome h = cl.call("hello");
        T.eq("ui", h.role(), "real: role ui (GB_POWER_TEST_UID 10123 + packages.list + APK present)");
        Status st = cl.status().status();
        T.check(st != null, "real: status parses");
        if (st != null) {
            T.eq("unavailable", st.state, "real: no NVIDIA device in the fake tree");
            T.eq(1, st.persist, "real: persist 1 (gfx_mode auto after the golden enable)");
            T.eq("auto", st.gfxMode, "real: gfx_mode auto");
            T.eq(0, st.gfxCap, "real: gfx_cap 0 (no driver package)");
            T.eq("nopkg", st.gfxReason, "real: gfx_reason nopkg");
            T.eq(0, st.appsSync, "real: apps_sync 0 (no catalog yet)");
            T.eq("n/a", st.dtx, "real: dtx n/a (-D)");
            T.eq(StateMapper.UNAVAILABLE, StateMapper.graphicsTile(st), "real: tile Unavailable (row 1, gfx_cap 0)");
            T.eq("Auto: Unavailable", StateMapper.summary(st), "real: summary Auto: Unavailable");
            GraphicsPage p = GraphicsPage.build(st, null, null);
            T.check(p.connected && !p.graphics && "Unavailable".equals(p.statusLine) && !p.rerunEnabled,
                    "real: page Unavailable, graphics controls disabled (cap 0)");
            T.eq(StateMapper.UNAVAILABLE, StateMapper.detachTile(st), "real: detach tile Unavailable");
        }
        Outcome sm = cl.call("set-mode", "nvidia");
        T.check(sm.ok() && "next-attach".equals(sm.reply.str("effect")), "real: set-mode nvidia without DTX -> next-attach");
        T.eq("Takes effect when the keyboard base is attached", StateMapper.effectToast(sm.reply.str("effect")),
                "real: next-attach toast");
        Outcome dis = cl.call("disable");
        T.check(dis.ok() && "now".equals(dis.reply.str("effect")), "real: disable effect now: " + dis);
        T.eq(0, cl.status().status().persist, "real: persist 0 after disable");
        T.eq("intel", cl.status().status().gfxMode, "real: gfx_mode intel after disable");
        T.eq("ENODEV", cl.call("holders").err(), "real: holders without nouveau -> ENODEV");
        T.eq("ENODEV", cl.call("list-holders").err(), "real: list-holders without nouveau -> ENODEV");
        T.eq("ENODEV", cl.call("detach-request").err(), "real: detach-request without DTX -> ENODEV");
        T.eq("ENODEV", cl.call("run-selftest").err(), "real: run-selftest with gfx_cap 0 -> ENODEV");
        T.eq("ENODEV", cl.call("set-app-policy", "all").err(), "real: set-app-policy with gfx_cap 0 -> ENODEV");
        T.check(cl.call("report").ok(), "real: report ok");
        T.check(cl.call("compute-list").ok(), "real: compute-list ok");
        T.eq("ENOENT", cl.call("compute-allow", "com.example.missing").err(), "real: compute-allow unknown -> ENOENT");
        T.eq("ENODEV", cl.call("compute-start").err(), "real: compute-start without GPU -> ENODEV");
        T.eq("ESTALE", cl.call("close-holders", "4242").err(), "real: close-holders non-holder -> ESTALE");
        List<CatalogBuilder.App> apps = new ArrayList<CatalogBuilder.App>();
        apps.add(new CatalogBuilder.App("com.example.other", "Other", 0, CatalogBuilder.CATEGORY_GAME));
        List<String> lines = CatalogBuilder.lines(CatalogBuilder.build(apps));
        GbPowerClient.Batch b = cl.exchangeAll(lines, GbPowerClient.BATCH_TIMEOUT_MS);
        T.check(b.ok(lines.size()) && b.last().num("apps_sync", -1) == 1, "real: catalog push committed: " + b);
        T.eq(1, cl.status().status().appsSync, "real: apps_sync 1 after the push");

        T.group("Real gb_power v1.2: watch");
        Rec rec = new Rec();
        WatchSession w = new WatchSession(cl, rec, fast());
        w.start();
        T.check(T.within(2000, () -> rec.last() != null), "real watch: first status");
        writeProp(propsDir, "vendor.gb.nouveau.mode", "disabled");
        T.sleep(300);
        cl.status(); // a request makes the daemon re-read its inputs (as in power/test/test_socket.py)
        T.check(T.within(3000, () -> rec.last() != null && "disabled".equals(rec.last().state)),
                "real watch: disabled pushed");
        writeProp(propsDir, "vendor.gb.nouveau.booted", "0");
        Outcome en = cl.call("enable");
        T.check(en.ok() && "next-boot".equals(en.reply.str("effect")), "real: enable before booted=1 -> next-boot: " + en);
        T.eq("Takes effect after restart", StateMapper.effectToast(en.reply.str("effect")), "real: next-boot toast");
        w.stop();
        writeProp(propsDir, "vendor.gb.nouveau.booted", "1");
        writeProp(propsDir, "vendor.gb.nouveau.mode", "default");
    }

    /** The INTERFACES 26.3 tree with the host-only EC (GB_POWER_TEST_DTX=1): the whole graphics flow. */
    static void real12(String sock, String propsDir) {
        T.group("Real gb_power v1.2 (26.3 tree, DTX attached): the app client end to end");
        final GbPowerClient cl = new GbPowerClient(UnixTransport.factory(sock), 2000);
        T.check(T.within(6000, () -> {
            Outcome o = cl.call("get-mode");
            return o.ok() && GET_MODE_26_3.equals(o.reply.line);
        }), "real: golden 26.3 get-mode (after the worker wrote gfx_session)");
        Status st = cl.status().status();
        T.check(st != null && st.gfxCap == 1 && "nvidia".equals(st.gfxActive) && "ok".equals(st.gfxReason),
                "real: status gfx_cap 1, gfx_active nvidia, reason ok: " + st);
        T.eq("NVIDIA", StateMapper.graphicsTile(st).subtitle, "real: tile NVIDIA (row 12)");
        T.eq(StateMapper.TileState.ACTIVE, StateMapper.graphicsTile(st).state, "real: tile ACTIVE");
        T.eq("Auto: NVIDIA", StateMapper.summary(st), "real: summary Auto: NVIDIA");
        GraphicsPage p = GraphicsPage.build(st, null, UnitTests.DF);
        T.eq("Apps you open now use: NVIDIA", p.statusLine, "real: page status line");
        T.eq("Self-test: Passed on D1761390000", p.selfTestLine, "real: page self-test line from the record");
        T.eq("NVIDIA GPU: asleep (D3cold)", p.gpuLine, "real: page GPU line");
        T.check(p.graphics && p.rerunEnabled && "auto".equals(p.mode), "real: page controls enabled, radio auto");
        T.eq("intel", StateMapper.nextMode(st.gfxMode), "real: a tap would send set-mode intel");
        Outcome uc = cl.call("ui-caps", "notify");
        T.check(uc.ok() && uc.reply.num("notify", -1) == 1, "real: ui-caps notify");
        // catalog push: the builder drops gb.sb3.* and system apps; gb_power drops the uninstalled package
        List<CatalogBuilder.App> apps = new ArrayList<CatalogBuilder.App>();
        apps.add(new CatalogBuilder.App("com.example.other", "Other", 0, CatalogBuilder.CATEGORY_GAME));
        apps.add(new CatalogBuilder.App("com.example.missing", "Missing", 0, -1));
        apps.add(new CatalogBuilder.App("gb.sb3.gpu", "NVIDIA GPU", 0, -1));
        apps.add(new CatalogBuilder.App("com.android.settings", "Settings", CatalogBuilder.FLAG_SYSTEM, -1));
        List<String> lines = CatalogBuilder.lines(CatalogBuilder.build(apps));
        T.eq("[apps-begin 2, apps-add g:com.example.other, apps-add a:com.example.missing, apps-commit 2]",
                lines.toString(), "real: push lines");
        GbPowerClient.Batch b = cl.exchangeAll(lines, GbPowerClient.BATCH_TIMEOUT_MS);
        T.check(b.ok(lines.size()) && b.last().num("n", -1) == 1 && b.last().num("dropped", -1) == 1
                && b.last().num("opt_in_n", -1) == 1, "real: apps-commit n 1, dropped 1, opt_in_n 1: " + b);
        // per-app choices with the AppRows flow
        AppRows.Row row = new AppRows.Row("com.example.other", "Other", true, "default", false);
        AppRows.Change ch = AppRows.change(row.pkg, AppRows.INTEL, true);
        row.choice = ch.choice;
        T.check(AppRows.apply(row, "default", cl.call(ch.cmd, ch.arg)) && row.alwaysIntel(), "real: Always Intel on");
        T.check(!row.nvidia("all"), "real: Always Intel wins (24.4)");
        Outcome gm = cl.call("get-mode");
        T.check(gm.ok() && gm.reply.num("opt_in_n", -1) == 0 && gm.reply.num("prefs_n", -1) == 1,
                "real: Always Intel removes the game from the opt-in list: " + gm);
        List<AppRows.Choice> choices = cl.getAllChoices();
        T.check(choices != null && choices.size() == 1 && "intel".equals(choices.get(0).choice),
                "real: get-apps paging: " + (choices == null ? null : choices.size()));
        ch = AppRows.change(row.pkg, AppRows.NVIDIA, true);
        row.choice = ch.choice;
        T.check(AppRows.apply(row, "intel", cl.call(ch.cmd, ch.arg)) && row.useNvidia() && !row.alwaysIntel(),
                "real: Use NVIDIA replaces Always Intel");
        T.check(AppRows.apply(row, "nvidia", cl.call("set-app-nvidia", "com.example.other:0"))
                && "default".equals(row.choice), "real: Use NVIDIA off -> default");
        Outcome pol = cl.call("set-app-policy", "all");
        T.check(pol.ok() && "restart-apps".equals(pol.reply.str("effect")), "real: set-app-policy all");
        T.check(GraphicsPage.build(cl.status().status(), null, null).allApps, "real: all-apps switch on");
        T.check(cl.call("set-app-policy", "games").ok(), "real: set-app-policy games");
        // holders and dialogs
        Outcome lh = cl.call("list-holders");
        T.check(lh.ok() && Json.list(lh.reply.obj, "holders").isEmpty(), "real: list-holders [] (nouveau loaded)");
        Outcome es = cl.call("close-holders-and-detach", "4242");
        T.eq("ESTALE", es.err(), "real: close-holders-and-detach a non-holder -> ESTALE");
        T.eq("None of these apps could be closed", Texts.error("close-holders-and-detach", es), "real: ESTALE text");
        // the mode cycle from the tile: auto -> intel -> nvidia -> auto
        Outcome s1 = cl.call("set-mode", StateMapper.nextMode(cl.status().status().gfxMode));
        T.check(s1.ok() && "intel".equals(s1.reply.str("mode")) && "now".equals(s1.reply.str("effect"))
                && StateMapper.effectToast(s1.reply.str("effect")) == null, "real: tap 1 -> set-mode intel, now, no toast");
        Status si = cl.status().status();
        T.eq("Off (Intel mode)", StateMapper.graphicsTile(si).subtitle, "real: tile Off (Intel mode)");
        T.eq("Apps you open now use: Intel (Intel only mode)", GraphicsPage.statusLine(si), "real: status line mode");
        T.check(!GraphicsPage.rerunEnabled(si), "real: Run self-test again disabled in Intel mode");
        T.eq("EBUSY", cl.call("run-selftest").err(), "real: run-selftest in Intel mode -> EBUSY");
        Outcome s2 = cl.call("set-mode", StateMapper.nextMode(si.gfxMode));
        T.check(s2.ok() && "nvidia".equals(s2.reply.str("mode")), "real: tap 2 -> set-mode nvidia");
        T.check(T.within(6000, () -> "NVIDIA (manual)".equals(StateMapper.graphicsTile(cl.status().status()).subtitle)),
                "real: tile NVIDIA (manual) once gb_power re-enabled NVIDIA");
        Outcome s3 = cl.call("set-mode", StateMapper.nextMode(cl.status().status().gfxMode));
        T.check(s3.ok() && "auto".equals(s3.reply.str("mode")), "real: tap 3 -> set-mode auto");
        T.check(T.within(3000, () -> "NVIDIA".equals(StateMapper.graphicsTile(cl.status().status()).subtitle)),
                "real: tile NVIDIA (auto)");
    }

    static void writeProp(String dir, String k, String v) {
        try {
            java.nio.file.Files.write(new File(dir, k).toPath(), v.getBytes(StandardCharsets.US_ASCII));
        } catch (IOException e) {
            T.check(false, "write prop " + k + ": " + e);
        }
    }
}
