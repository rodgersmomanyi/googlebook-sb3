package gb.sb3.gpu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * HoldersActivity logic (INTERFACES 27.4): the three modes, their exact texts, which command a confirmation sends, and
 * the progress of a {@code close-holders-and-detach}.
 */
public final class HoldersModel {
    private HoldersModel() {
    }

    public enum Mode {
        /** From the in-use notification or the page: close the apps. */
        CLOSE,
        /** After an Intel switch that reported {@code pending}: close the apps, gb_power finishes the switch (23.6). */
        INTEL,
        /** From the detach notification: close the apps, then the pending detach (23.9). */
        DETACH;

        public static Mode parse(String s) {
            if ("intel".equals(s)) return INTEL;
            if ("detach".equals(s)) return DETACH;
            return CLOSE;
        }

        public String extra() {
            return name().toLowerCase(java.util.Locale.ROOT);
        }
    }

    public static String title(Mode m) {
        switch (m) {
            case INTEL: return Texts.INTEL_TITLE;
            case DETACH: return Texts.DETACH_DIALOG_TITLE;
            default: return Texts.CLOSE_TITLE;
        }
    }

    public static String text(Mode m) {
        switch (m) {
            case INTEL: return Texts.INTEL_TEXT;
            case DETACH: return Texts.DETACH_DIALOG_TEXT;
            default: return Texts.CLOSE_TEXT;
        }
    }

    public static String positive(Mode m) {
        return m == Mode.DETACH ? Texts.ACTION_CLOSE_AND_DETACH : Texts.HOLDERS_OK;
    }

    /** What the dialog lists and what "positive" sends. */
    public static final class Plan {
        public final Mode mode;
        public final List<Integer> pids;
        /** Display names of the closable holders, in {@link #pids} order. */
        public final List<String> names;
        /** Holders that cannot be closed here (root, system, probes; not the compute service). */
        public final List<String> others;
        /** The compute service runs and is stopped with `stop-compute` (it is never force-stopped). */
        public final boolean stopCompute;
        /** The main command and its argument; null when only the compute service has to go. */
        public final String cmd;
        public final String arg;
        /** Detach mode without app pids: `stop-compute`, then a plain `detach-request`. */
        public final boolean detachRequest;

        Plan(Mode mode, List<Integer> pids, List<String> names, List<String> others, boolean stopCompute) {
            this.mode = mode;
            this.pids = Collections.unmodifiableList(pids);
            this.names = Collections.unmodifiableList(names);
            this.others = Collections.unmodifiableList(others);
            this.stopCompute = stopCompute;
            if (pids.isEmpty()) {
                cmd = null;
                arg = null;
                detachRequest = mode == Mode.DETACH && stopCompute;
            } else {
                cmd = mode == Mode.DETACH ? "close-holders-and-detach" : "close-holders";
                arg = pidList(pids);
                detachRequest = false;
            }
        }

        /** Something can be closed: the positive button is shown. */
        public boolean canProceed() {
            return !pids.isEmpty() || stopCompute;
        }

        /** The dialog message: the mode text, the bullet list, then {@code Cannot be closed here:}. */
        public String message() {
            StringBuilder b = new StringBuilder(text(mode));
            for (String n : names) b.append("\n• ").append(n);
            if (stopCompute) b.append("\n• ").append(Texts.COMPUTE_SERVICE);
            if (!others.isEmpty()) b.append("\n\n").append(Texts.NOT_CLOSABLE).append(' ').append(join(others));
            return b.toString();
        }
    }

    static String join(List<String> l) {
        StringBuilder b = new StringBuilder();
        for (String s : l) {
            if (b.length() > 0) b.append(", ");
            b.append(s);
        }
        return b.toString();
    }

    static String pidList(List<Integer> pids) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < pids.size() && i < 16; i++) {
            if (i > 0) b.append(',');
            b.append(pids.get(i));
        }
        return b.toString();
    }

    /**
     * Detach mode (INTERFACES 29 R-16): a {@code refused} entry with reason {@code not-holder} means the app exited
     * between {@code list-holders} and the tap; it is no problem while gb_power accepted the request (the pending
     * detach runs). Every other reason, and every reason in the other modes, is shown.
     */
    public static boolean refusalShown(Mode m, String reason) {
        return !(m == Mode.DETACH && "not-holder".equals(reason));
    }

    /** Detach mode: every listed app exited already ({@code ESTALE}): a plain {@code detach-request} instead (R-16). */
    public static boolean detachFallback(Mode m, String cmd, String err) {
        return m == Mode.DETACH && "close-holders-and-detach".equals(cmd) && "ESTALE".equals(err);
    }

    /** Builds the plan from a status (or a `list-holders` holder list) and the compute flag. */
    public static Plan plan(Mode m, List<Holder> holders, boolean computeRunning, Labels.Labeler l) {
        List<Integer> pids = new ArrayList<Integer>();
        List<String> names = new ArrayList<String>();
        List<String> others = new ArrayList<String>();
        for (Holder h : holders) {
            if (h.closable && pids.size() < 16) {
                if (pids.contains(h.pid)) continue;
                pids.add(h.pid);
                String n = Labels.holder(h, l);
                if (!names.contains(n)) names.add(n);
            } else if (!h.closable && !Labels.COMPUTE_COMM.equals(h.comm)) {
                String n = Labels.holder(h, l);
                if (!others.contains(n)) others.add(n);
            }
        }
        return new Plan(m, pids, names, others, computeRunning);
    }

    public enum Progress {
        WAIT,
        /** {@code dtx} reports confirmed (or the base is already gone): the dialog closes. */
        DONE,
        /** {@code detach_ref} grew again: some apps could not be closed. */
        FAILED
    }

    /** 27.4: after `close-holders-and-detach` the dialog shows {@code Preparing to detach...} until one of these. */
    public static Progress progress(Status s, int detachRefAtStart) {
        if (s == null) return Progress.WAIT;
        if ("confirmed".equals(s.dtx) || "detached".equals(s.base)) return Progress.DONE;
        if (s.detachRef > detachRefAtStart) return Progress.FAILED;
        return Progress.WAIT;
    }

    /** The failure text with the remaining labels: {@code Some apps could not be closed: A, B}. */
    public static String failedText(Status s, Labels.Labeler l) {
        String j = s == null ? "" : Labels.join(Labels.names(s.holders, l));
        return j.isEmpty() ? Texts.SOME_NOT_CLOSED : Texts.SOME_NOT_CLOSED + ": " + j;
    }
}
