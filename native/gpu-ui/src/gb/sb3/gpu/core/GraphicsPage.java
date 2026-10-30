package gb.sb3.gpu.core;

/**
 * The Graphics page (INTERFACES 27.2) as a pure function of the status: the checked mode radio, the status, GPU and
 * self-test lines, the {@code Run self-test again} rule, the {@code pending} line with its {@code Close apps} button,
 * the {@code Use NVIDIA for all apps} switch and which controls are enabled. The per-app list is {@link AppRows}.
 */
public final class GraphicsPage {
    /** Formats the self-test date (epoch seconds); the app passes the device's date format. */
    public interface DateFmt {
        String date(long epochSeconds);
    }

    /** True: a status arrived (socket, hello, role). */
    public final boolean connected;
    /** True: graphics controls work (a status with {@code gfx_cap} 1). */
    public final boolean graphics;
    public final String mode;
    public final String statusLine;
    public final String gpuLine;
    public final String selfTestLine;
    public final boolean rerunEnabled;
    /** {@code <labels> still use the NVIDIA GPU.} while {@code pending} is {@code intel}; else null. */
    public final String pendingLine;
    public final boolean allApps;
    public final String allAppsSecondary;
    public final String computeLine;

    private GraphicsPage(boolean connected, boolean graphics, String mode, String statusLine, String gpuLine,
            String selfTestLine, boolean rerunEnabled, String pendingLine, boolean allApps, String computeLine) {
        this.connected = connected;
        this.graphics = graphics;
        this.mode = mode;
        this.statusLine = statusLine;
        this.gpuLine = gpuLine;
        this.selfTestLine = selfTestLine;
        this.rerunEnabled = rerunEnabled;
        this.pendingLine = pendingLine;
        this.allApps = allApps;
        this.allAppsSecondary = allApps ? Texts.ALL_APPS_ON : Texts.ALL_APPS_OFF;
        this.computeLine = computeLine;
    }

    public static GraphicsPage build(Status s, Labels.Labeler l, DateFmt df) {
        if (s == null) {
            return new GraphicsPage(false, false, "auto", Texts.UNAVAILABLE, "", "", false, null, false, "");
        }
        boolean gfx = s.gfxCap == 1;
        String mode = s.effectiveMode();
        String status = gfx ? statusLine(s) : Texts.UNAVAILABLE;
        String gpu = Texts.GPU_PREFIX + gpuState(s) + usedBy(s, l);
        String test = gfx ? Texts.SELFTEST_PREFIX + selfTest(s, df) : "";
        String pend = gfx && "intel".equals(s.pending)
                ? Texts.pendingLine(Labels.holders(s.holders, l, StateMapper.busyCount(s.state))) : null;
        return new GraphicsPage(true, gfx, mode, status, gpu, test, gfx && rerunEnabled(s), pend,
                "all".equals(s.gfxPolicy), s.compute);
    }

    /** {@code Apps you open now use: NVIDIA} or {@code ...: Intel (<reason text>)} (27.6). */
    public static String statusLine(Status s) {
        if (s.nvidiaActive()) return Texts.STATUS_PREFIX + Texts.NVIDIA;
        String r = Texts.reasonText(s.gfxReason);
        return Texts.STATUS_PREFIX + ("ok".equals(s.gfxReason) ? Texts.INTEL : r);
    }

    /**
     * {@code asleep (D3cold)} / {@code active} / {@code no driver} / {@code not connected}; (wave 6, B-4)
     * {@code not supported (no driver)} for {@code gfx_reason} {@code model}.
     */
    public static String gpuState(Status s) {
        if (s.bdf.isEmpty() || "unavailable".equals(s.state)) return Texts.GPU_NOT_CONNECTED;
        if (!"nouveau".equals(s.driver)) return "model".equals(s.gfxReason) ? Texts.GPU_NOT_SUPPORTED : Texts.GPU_NO_DRIVER;
        return "D3cold".equals(s.powerState) ? Texts.GPU_ASLEEP : Texts.GPU_ACTIVE;
    }

    static String usedBy(Status s, Labels.Labeler l) {
        int n = StateMapper.busyCount(s.state);
        if (n == 0) return "";
        String j = Labels.join(Labels.names(s.holders, l));
        return j.isEmpty() ? ", used by " + n + " app(s)" : Texts.usedBy(n, j);
    }

    /** The self-test line per {@code gfx_test} (falling back to {@code gfx_ok}) and {@code gfx_test_t}. */
    public static String selfTest(Status s, DateFmt df) {
        String t = s.gfxTest;
        if (!"running".equals(t) && !"pass".equals(t) && !"fail".equals(t) && !"error".equals(t)) {
            t = s.gfxOk == 1 ? "pass" : s.gfxOk == 0 ? "fail" : s.gfxOk == -2 ? "error" : "none";
        }
        switch (t) {
            case "running":
                return Texts.SELFTEST_RUNNING;
            case "pass":
                return Texts.selfTestPassed(date(s.gfxTestT, df));
            case "fail":
                return Texts.selfTestFailed(date(s.gfxTestT, df), s.gfxTestReason);
            case "error":
                return Texts.selfTestCouldNotRun(s.gfxTestReason);
            default:
                return Texts.SELFTEST_NOT_RUN;
        }
    }

    static String date(long t, DateFmt df) {
        if (t <= 0 || df == null) return "an unknown date";
        return df.date(t);
    }

    /**
     * 27.2 item 4: enabled when {@code gfx_cap} 1, {@code gfx_mode} not {@code intel}, {@code state} {@code standby} or
     * {@code busy:N}, {@code gfx_test} not {@code running}, no {@code pending}, not wedged or fault.
     */
    public static boolean rerunEnabled(Status s) {
        if (s == null || s.gfxCap != 1) return false;
        if ("intel".equals(s.effectiveMode())) return false;
        if (!"standby".equals(s.state) && StateMapper.busyCount(s.state) == 0) return false;
        if ("running".equals(s.gfxTest)) return false;
        if (!s.pending.isEmpty()) return false;
        return s.wedged == 0 && s.faultReason.isEmpty();
    }
}
