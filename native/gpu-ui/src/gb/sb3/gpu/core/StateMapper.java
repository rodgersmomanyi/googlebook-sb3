package gb.sb3.gpu.core;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Maps a gb_power status to what the tiles and the Settings entry show. The Graphics tile mapping is INTERFACES 27.1
 * (normative, v1.2). A null status means "no socket / hello failed / role other", which 27.1 row 1 maps to
 * Unavailable. The v1.1 "NVIDIA GPU" tile mapping (8.3) is kept as {@link #gpuTile} for the v1.1 host tests only (8.3
 * note); the app no longer shows it.
 */
public final class StateMapper {
    private StateMapper() {
    }

    /** Mirrors {@code android.service.quicksettings.Tile} STATE_INACTIVE / STATE_ACTIVE / STATE_UNAVAILABLE. */
    public enum TileState {
        INACTIVE,
        ACTIVE,
        UNAVAILABLE
    }

    /** What a tap on the tile does. */
    public enum Tap {
        NONE,
        /** 27.1 rows 3-17: {@code set-mode <nextMode(gfx_mode)>}, never a dialog. */
        SET_MODE,
        /** Legacy 8.3 (v1.1 tests only). */
        ENABLE,
        DISABLE,
        HOLDERS,
        DETACH
    }

    public static final class TileModel {
        public final TileState state;
        public final String subtitle;
        public final Tap tap;
        /** N of busy:N, else 0. */
        public final int busy;
        /** The 27.1 row that matched (1-17; 0 for the other tiles). */
        public final int row;

        public TileModel(TileState state, String subtitle, Tap tap, int busy) {
            this(state, subtitle, tap, busy, 0);
        }

        public TileModel(TileState state, String subtitle, Tap tap, int busy, int row) {
            this.state = state;
            this.subtitle = subtitle;
            this.tap = tap;
            this.busy = busy;
            this.row = row;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof TileModel)) return false;
            TileModel m = (TileModel) o;
            return state == m.state && tap == m.tap && busy == m.busy
                    && (subtitle == null ? m.subtitle == null : subtitle.equals(m.subtitle));
        }

        @Override
        public int hashCode() {
            return state.hashCode() * 31 + tap.hashCode();
        }

        @Override
        public String toString() {
            return state + "/" + subtitle + "/" + tap + (busy > 0 ? "/" + busy : "") + (row > 0 ? "/row" + row : "");
        }
    }

    public static final TileModel UNAVAILABLE =
            new TileModel(TileState.UNAVAILABLE, Texts.UNAVAILABLE, Tap.NONE, 0, 1);

    /** busy:N with N in 1..999 (INTERFACES 8.1 rule 8). */
    private static final Pattern BUSY = Pattern.compile("busy:([1-9][0-9]{0,2})");

    /** Returns N for a valid busy:N state, else 0. */
    public static int busyCount(String state) {
        if (state == null) return 0;
        Matcher m = BUSY.matcher(state);
        return m.matches() ? Integer.parseInt(m.group(1)) : 0;
    }

    /** INTERFACES 22.2: exactly {@code auto}, {@code intel} or {@code nvidia}; anything else (unset too) is auto. */
    public static String normMode(String m) {
        return "intel".equals(m) || "nvidia".equals(m) ? m : "auto";
    }

    /** INTERFACES 27.1 tap cycle: Auto -> Intel -> NVIDIA -> Auto (unset/unknown counts as auto). */
    public static String nextMode(String gfxMode) {
        String m = normMode(gfxMode);
        return "auto".equals(m) ? "intel" : "intel".equals(m) ? "nvidia" : "auto";
    }

    /** Mode labels of 27.2 (radios and the injected summary). */
    public static String modeLabel(String gfxMode) {
        String m = normMode(gfxMode);
        return "intel".equals(m) ? Texts.MODE_INTEL : "nvidia".equals(m) ? Texts.MODE_NVIDIA : Texts.MODE_AUTO;
    }

    /** INTERFACES 27.1 toasts for a `set-mode` effect; {@code now} (and anything unknown) has none. */
    public static String effectToast(String effect) {
        if ("next-boot".equals(effect)) return Texts.TAKES_EFFECT_AFTER_RESTART;
        if ("next-attach".equals(effect)) return Texts.TAKES_EFFECT_WHEN_ATTACHED;
        if ("pending".equals(effect)) return Texts.SWITCHING_TO_INTEL_WHEN_CLOSED;
        return null;
    }

    private static TileModel t(TileState st, String sub, int busy, int row) {
        return new TileModel(st, sub, st == TileState.UNAVAILABLE ? Tap.NONE : Tap.SET_MODE, busy, row);
    }

    /** The Graphics tile (INTERFACES 27.1): the first matching row wins. */
    public static TileModel graphicsTile(Status s) {
        // row 1: no socket / hello fails / role other (s == null), no app graphics on this image, no DTX
        if (s == null || s.gfxCap != 1 || "n/a".equals(s.dtx)) return UNAVAILABLE;
        int n = busyCount(s.state);
        boolean nv = "nvidia".equals(s.effectiveMode());
        String r = s.gfxReason;
        if ("detaching".equals(s.state) || "detach".equals(s.pending)) {
            return t(TileState.UNAVAILABLE, Texts.PREPARING_TO_DETACH, 0, 2);
        }
        if ("intel".equals(s.effectiveMode())) {
            return n > 0 ? t(TileState.INACTIVE, Texts.offIntelBusy(n), n, 3)
                    : t(TileState.INACTIVE, Texts.OFF_INTEL_MODE, 0, 4);
        }
        if ("wedged".equals(s.state)) return t(TileState.INACTIVE, Texts.RESTART_REQUIRED, 0, 5);
        if ("fault".equals(s.state)) return t(TileState.INACTIVE, Texts.FAULT, 0, 6);
        if ("kill".equals(r) || "disabled".equals(s.state)) {
            return t(TileState.INACTIVE, Texts.DISABLED_FOR_THIS_BOOT, 0, 7);
        }
        if ("crashloop".equals(r)) return t(TileState.INACTIVE, nv ? Texts.NV_RECOVERED : Texts.INTEL_RECOVERED, 0, 8);
        if ("test".equals(r)) return t(TileState.INACTIVE, Texts.INTEL_TEST_BOOT, 0, 9);
        if (!s.baseAttached()) {
            return t(TileState.INACTIVE, nv ? Texts.NV_DETACHED : Texts.UNAVAILABLE_DETACHED, 0, 10);
        }
        if (s.nvidiaActive()) {
            return n > 0 ? t(TileState.ACTIVE, nv ? Texts.nvidiaManualBusy(n) : Texts.nvidiaBusy(n), n, 11)
                    : t(TileState.ACTIVE, nv ? Texts.NVIDIA_MANUAL : Texts.NVIDIA, 0, 12);
        }
        if ("loading".equals(r)) return t(TileState.INACTIVE, nv ? Texts.NV_STARTING : Texts.INTEL_STARTING, 0, 13);
        if ("untested".equals(r)) return t(TileState.INACTIVE, nv ? Texts.NV_TESTING : Texts.INTEL_TESTING, 0, 14);
        if ("testerror".equals(r)) {
            return t(TileState.INACTIVE, nv ? Texts.NV_TEST_NOT_RUN : Texts.INTEL_TEST_NOT_RUN, 0, 15);
        }
        if ("selftest".equals(r)) {
            return t(TileState.INACTIVE, nv ? Texts.NV_TEST_FAILED : Texts.INTEL_TEST_FAILED, 0, 16);
        }
        return t(TileState.INACTIVE, nv ? Texts.NV_NOT_READY : Texts.INTEL, 0, 17);
    }

    /**
     * The injected Settings entry summary (27.2): {@code <Mode label>: <tile subtitle>}, e.g. {@code Auto: NVIDIA
     * (busy 2)}; without any status (no socket) just the subtitle {@code Unavailable}.
     */
    public static String summary(Status s) {
        TileModel m = graphicsTile(s);
        return s == null ? m.subtitle : modeLabel(s.gfxMode) + ": " + m.subtitle;
    }

    /**
     * The optional Detach tile (INTERFACES 13.2): UNAVAILABLE when the base is not attached or there is no DTX;
     * otherwise a button that sends `detach-request`. The subtitles are component-defined.
     */
    public static TileModel detachTile(Status s) {
        if (s == null) return UNAVAILABLE;
        if ("n/a".equals(s.dtx) || !"attached".equals(s.base)) return UNAVAILABLE;
        if ("detaching".equals(s.state) || "in-progress".equals(s.dtx)) {
            return new TileModel(TileState.ACTIVE, Texts.DETACH_IN_PROGRESS, Tap.NONE, 0);
        }
        if ("confirmed".equals(s.dtx)) return new TileModel(TileState.ACTIVE, Texts.DETACH_READY, Tap.NONE, 0);
        if ("ready".equals(s.dtx)) return new TileModel(TileState.INACTIVE, null, Tap.DETACH, 0);
        return UNAVAILABLE;
    }

    /** Legacy: the v1.1 "NVIDIA GPU" tile (INTERFACES 8.3, superseded by 27.1). Not used by the app. */
    public static TileModel gpuTile(Status s) {
        if (s == null) return legacyUnavailable();
        boolean persist = s.persist == 1;
        String st = s.state;
        if ("off".equals(st)) {
            return new TileModel(TileState.INACTIVE, persist ? Texts.ON_AT_NEXT_RESTART : Texts.OFF, Tap.ENABLE, 0);
        }
        if ("crashloop".equals(st)) {
            return new TileModel(TileState.INACTIVE, persist ? Texts.ON_AT_NEXT_RESTART : Texts.OFF_RECOVERED,
                    Tap.ENABLE, 0);
        }
        if ("loading".equals(st)) return new TileModel(TileState.ACTIVE, Texts.STARTING, Tap.NONE, 0);
        if ("standby".equals(st)) return new TileModel(TileState.ACTIVE, Texts.STANDBY, Tap.DISABLE, 0);
        if ("detaching".equals(st)) {
            return new TileModel(TileState.UNAVAILABLE, Texts.PREPARING_TO_DETACH, Tap.NONE, 0);
        }
        if ("disabled".equals(st)) {
            return new TileModel(TileState.UNAVAILABLE, Texts.DISABLED_FOR_THIS_BOOT, Tap.NONE, 0);
        }
        if ("unavailable".equals(st)) return legacyUnavailable();
        if ("wedged".equals(st)) return new TileModel(TileState.UNAVAILABLE, Texts.RESTART_REQUIRED, Tap.NONE, 0);
        if ("fault".equals(st)) return new TileModel(TileState.UNAVAILABLE, Texts.FAULT, Tap.NONE, 0);
        int n = busyCount(st);
        if (n > 0) return new TileModel(TileState.ACTIVE, Texts.busy(n), Tap.HOLDERS, n);
        return legacyUnavailable();
    }

    private static TileModel legacyUnavailable() {
        return new TileModel(TileState.UNAVAILABLE, Texts.UNAVAILABLE, Tap.NONE, 0);
    }
}
