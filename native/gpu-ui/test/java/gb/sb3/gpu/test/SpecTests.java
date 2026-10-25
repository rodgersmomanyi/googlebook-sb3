package gb.sb3.gpu.test;

import gb.sb3.gpu.core.GraphicsPage;
import gb.sb3.gpu.core.HoldersModel;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.StateMapper.TileModel;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The exact strings and the tile mapping checked against the text of INTERFACES.md v1.2 itself (not a copy): the 27.1
 * table (every row, both mode columns), the 27.1 toasts, 27.2's labels and help text, the 27.4 dialog table, the 27.6
 * reason table and the v1.2 rows of 12.3.
 */
final class SpecTests {
    private SpecTests() {
    }

    static String section(String spec, String heading) {
        int a = spec.indexOf("\n" + heading);
        if (a < 0) return "";
        int b = spec.indexOf("\n### ", a + 1);
        int c = spec.indexOf("\n## ", a + 1);
        int e = b < 0 ? c : c < 0 ? b : Math.min(b, c);
        return spec.substring(a, e < 0 ? spec.length() : e);
    }

    /** Collapses markdown line wraps (newline + indentation) into single spaces. */
    static String flat(String s) {
        return s.replaceAll("\\s*\\n\\s*", " ");
    }

    static List<String[]> table(String sec) {
        List<String[]> rows = new ArrayList<String[]>();
        for (String l : sec.split("\n")) {
            if (!l.startsWith("| ") || l.startsWith("|---") || l.startsWith("| #") || l.startsWith("| Mode")
                    || l.startsWith("| `gfx_reason`")) {
                continue;
            }
            String[] c = l.substring(2, l.length() - (l.endsWith(" |") ? 2 : 0)).split(" \\| ", -1);
            for (int i = 0; i < c.length; i++) c[i] = c[i].trim();
            rows.add(c);
        }
        return rows;
    }

    static String tick(String cell) {
        Matcher m = Pattern.compile("`([^`]*)`").matcher(cell);
        return m.find() ? m.group(1) : cell;
    }

    /** A status that selects 27.1 row {@code row} (null for row 1), in {@code mode}. */
    static Status rowStatus(int row, String mode) {
        switch (row) {
            case 1: return null;
            case 2: return Fx.st("gfx_mode", mode, "state", "detaching");
            case 3: return Fx.st("gfx_mode", "intel", "state", "busy:3", "gfx_active", "intel", "gfx_reason", "mode");
            case 4: return Fx.st("gfx_mode", "intel", "gfx_active", "intel", "gfx_reason", "mode");
            case 5: return Fx.st("gfx_mode", mode, "state", "wedged", "gfx_active", "intel", "gfx_reason", "wedged");
            case 6: return Fx.st("gfx_mode", mode, "state", "fault", "gfx_active", "intel", "gfx_reason", "fault");
            case 7: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "kill");
            case 8: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "crashloop");
            case 9: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "test");
            case 10: return Fx.st("gfx_mode", mode, "base", "detached", "gfx_active", "intel", "gfx_reason", "detached");
            case 11: return Fx.st("gfx_mode", mode, "state", "busy:3", "refcnt", 3);
            case 12: return Fx.st("gfx_mode", mode);
            case 13: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "loading");
            case 14: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "untested");
            case 15: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "testerror");
            case 16: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "selftest");
            default: return Fx.st("gfx_mode", mode, "gfx_active", "intel", "gfx_reason", "nodriver");
        }
    }

    static void run(String path) {
        String spec;
        try {
            spec = new String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
        } catch (IOException e) {
            T.check(false, "read " + path + ": " + e);
            return;
        }
        T.group("Spec cross-check: INTERFACES.md version");
        T.check(spec.contains("## 27. GUI (v1.2, D7; component 6)"), "INTERFACES.md is v1.2 (section 27 present)");

        T.group("Spec 27.1 table: every row, both mode columns");
        String s271 = section(spec, "### 27.1 ");
        List<String[]> rows = table(s271);
        int seen = 0;
        for (String[] r : rows) {
            if (r.length != 5 || !r[0].matches("\\d+")) continue;
            int row = Integer.parseInt(r[0]);
            seen++;
            String auto = tick(r[3]).replace(" N ", " 3 ").replace(" N)", " 3)");
            String nv = r[4].equals("same") ? auto : r[4].equals("(n/a)") ? null
                    : tick(r[4]).replace(" N ", " 3 ").replace(" N)", " 3)");
            TileModel ma = StateMapper.graphicsTile(rowStatus(row, "auto"));
            T.check(ma.row == row && ma.state.name().equals(r[2]) && ma.subtitle.equals(auto),
                    "27.1 row " + row + " auto: spec " + r[2] + "/" + auto + ", app " + ma);
            if (nv != null) {
                TileModel mn = StateMapper.graphicsTile(rowStatus(row, "nvidia"));
                T.check(mn.row == row && mn.state.name().equals(r[2]) && mn.subtitle.equals(nv),
                        "27.1 row " + row + " nvidia: spec " + r[2] + "/" + nv + ", app " + mn);
            }
        }
        T.eq(17, seen, "27.1 table has 17 rows");
        T.check(s271.contains("Label `" + Texts.TILE_LABEL + "`"), "27.1 tile label " + Texts.TILE_LABEL);
        Matcher tm = Pattern.compile("`(next-boot|next-attach|pending)` -> `([^`]+)`").matcher(flat(s271));
        int toasts = 0;
        while (tm.find()) {
            toasts++;
            T.eq(tm.group(2), StateMapper.effectToast(tm.group(1)), "27.1 toast for " + tm.group(1));
        }
        T.eq(3, toasts, "27.1 has 3 toasts");
        T.check(flat(s271).contains("`now` -> none") && StateMapper.effectToast("now") == null, "27.1 now: no toast");
        T.check(flat(s271).contains("Auto -> Intel -> NVIDIA -> Auto") && "intel".equals(StateMapper.nextMode("auto"))
                && "nvidia".equals(StateMapper.nextMode("intel")) && "auto".equals(StateMapper.nextMode("nvidia")),
                "27.1 cycle");

        T.group("Spec 27.6 reason texts");
        Map<String, String> reasons = new LinkedHashMap<String, String>();
        for (String[] r : table(section(spec, "### 27.6 "))) {
            if (r.length == 2) reasons.put(r[0].startsWith("`") ? tick(r[0]) : r[0], tick(r[1]));
        }
        T.eq(17, reasons.size(), "27.6 has 16 reasons + other");
        for (Map.Entry<String, String> e : reasons.entrySet()) {
            String key = "other".equals(e.getKey()) ? "no-such-reason" : e.getKey();
            T.eq(e.getValue(), Texts.reasonText(key), "27.6 " + e.getKey());
            if (!"ok".equals(e.getKey()) && !"other".equals(e.getKey())) {
                T.eq("Apps you open now use: " + e.getValue(), GraphicsPage.statusLine(Fx.st("gfx_active", "intel",
                        "gfx_reason", e.getKey())), "27.2 status line with the 27.6 text for " + e.getKey());
            }
        }

        T.group("Spec 27.4 HoldersActivity modes");
        int modes = 0;
        for (String[] r : table(section(spec, "### 27.4 "))) {
            if (r.length != 5) continue;
            HoldersModel.Mode m = HoldersModel.Mode.parse(tick(r[0]));
            if (!m.extra().equals(tick(r[0]))) continue;
            modes++;
            T.eq(tick(r[1]), HoldersModel.title(m), "27.4 " + tick(r[0]) + " title");
            T.eq(tick(r[2]), HoldersModel.text(m), "27.4 " + tick(r[0]) + " text");
            T.eq(tick(r[3]), HoldersModel.positive(m), "27.4 " + tick(r[0]) + " positive");
            HoldersModel.Plan p = HoldersModel.plan(m, Arrays.asList(Fx.h(5, "game", 10500, "com.example.game", true)),
                    false, null);
            T.eq(tick(r[4]), p.cmd, "27.4 " + tick(r[0]) + " command");
        }
        T.eq(3, modes, "27.4 has the modes close, intel, detach");
        String s274 = flat(section(spec, "### 27.4 "));
        T.check(s274.contains("Negative button `" + Texts.HOLDERS_CANCEL + "`"), "27.4 negative button Cancel");
        T.check(s274.contains("`" + Texts.NOT_CLOSABLE + "`"), "27.4 Cannot be closed here:");
        T.check(s274.contains("`" + Texts.PREPARING_DETACH_DOTS + "`"), "27.4 Preparing to detach...");
        T.check(s274.contains("`" + Texts.SOME_NOT_CLOSED + "`"), "27.4 Some apps could not be closed");

        T.group("Spec 27.2 Graphics page labels and help text");
        String s272 = flat(section(spec, "### 27.2 "));
        String[] exact = {Texts.RADIO_AUTO, Texts.RADIO_AUTO_SUB, Texts.RADIO_INTEL, Texts.RADIO_INTEL_SUB,
            Texts.RADIO_NVIDIA, Texts.RADIO_NVIDIA_SUB, Texts.STATUS_PREFIX + Texts.NVIDIA, Texts.GPU_ASLEEP,
            Texts.GPU_ACTIVE, Texts.GPU_NO_DRIVER, Texts.GPU_NOT_CONNECTED, Texts.SELFTEST_NOT_RUN,
            Texts.SELFTEST_RUNNING, Texts.SELFTEST_RERUN, Texts.ALL_APPS_OFF, Texts.ALL_APPS_ON, Texts.APPS_HINT,
            Texts.USE_NVIDIA, Texts.ALWAYS_INTEL, Texts.GAME_BADGE, Texts.HOLDER_RESTART, Texts.HOLDER_NOW,
            Texts.HELP_TEXT, Texts.CLOSE_APPS, Texts.MODE_AUTO, Texts.MODE_INTEL, Texts.MODE_NVIDIA, Texts.UNAVAILABLE,
            Texts.NVIDIA, Texts.INTEL};
        for (String x : exact) T.check(s272.contains("`" + x + "`"), "27.2 has `" + x + "`");
        T.check(s272.contains("**" + Texts.ALL_APPS + "**"), "27.2 has **Use NVIDIA for all apps**");
        T.check(s272.contains("**" + Texts.HELP_HEADING + "**"), "27.2 has **How graphics switching works**");
        T.check(s272.contains("**" + Texts.COMPUTE_HEADING + ":**"), "27.2 has **NVIDIA compute service:**");
        T.check(s272.contains("**" + Texts.APPS_HEADING + "**"), "27.2 has **Apps**");
        // templates with <placeholders>
        String[][] tpl = {
            {"Apps you open now use: Intel (<reason text>)", Texts.STATUS_PREFIX + "Intel (<reason text>)"},
            {", used by <N> app(s): <labels>", Texts.usedBy(7, "<labels>").replace("7", "<N>")},
            {"Passed on <date>", Texts.selfTestPassed("<date>")},
            {"Failed on <date>: <reason>", Texts.selfTestFailed("<date>", "<reason>")},
            {"Could not run: <reason>", Texts.selfTestCouldNotRun("<reason>")},
            {"<labels> still use the NVIDIA GPU.", Texts.pendingLine("<labels>")},
            {"<Mode label>: <tile subtitle>", StateMapper.summary(Fx.st()).replace("Auto", "<Mode label>")
                    .replace(": NVIDIA", ": <tile subtitle>")},
            {"Auto: NVIDIA (busy 2)", StateMapper.summary(Fx.st("state", "busy:2"))}};
        for (String[] t : tpl) {
            T.check(s272.contains("`" + t[0] + "`") && t[0].equals(t[1]), "27.2 template `" + t[0] + "` = app '" + t[1]
                    + "'");
        }

        T.group("Spec 12.3 v1.2 app notifications");
        String s123 = flat(section(spec, "### 12.3 "));
        String[] n = {Texts.CH_STATUS_ID, Texts.CH_DETACH_ID, Texts.IN_USE_TITLE, Texts.DETACH_TITLE,
            Texts.inUseText("<labels>"), Texts.detachText("<labels>")};
        for (String x : n) T.check(s123.contains("`" + x + "`"), "12.3 has `" + x + "`");
        T.check(s123.contains("| (v1.2) `gb_gpu_detach` (new) | " + Texts.CH_DETACH_NAME + " | HIGH | id "
                + Texts.NOTIF_DETACH + ","), "12.3 channel gb_gpu_detach: name Detach, HIGH, id 3");
        T.check(s123.contains("| " + Texts.CH_STATUS_NAME + " | LOW | id " + Texts.NOTIF_IN_USE + ","),
                "12.3 channel gb_gpu_status: NVIDIA GPU status, LOW, id 1");
        for (String a : new String[] {Texts.ACTION_CLOSE_APPS, Texts.ACTION_CLOSE_AND_DETACH, Texts.ACTION_CANCEL}) {
            T.check(s123.contains("**" + a + "**"), "12.3 action **" + a + "**");
        }
        T.check(s123.contains("max 5, then `and N more`") && "A, B, C, D, E and 1 more".equals(
                gb.sb3.gpu.core.Labels.join(Arrays.asList("A", "B", "C", "D", "E", "F"))), "12.3 max 5, then and N more");

        T.group("Spec 13.2 v1.2 labels");
        String s132 = flat(section(spec, "### 13.2 "));
        T.check(s132.contains("label `Graphics` (was `NVIDIA GPU`)") && "Graphics".equals(Texts.TILE_LABEL),
                "13.2 tile label Graphics");
        T.check(s132.contains("`com.android.settings.title=Graphics`") && "Graphics".equals(Texts.SETTINGS_TITLE),
                "13.2 Settings title Graphics");
    }
}
