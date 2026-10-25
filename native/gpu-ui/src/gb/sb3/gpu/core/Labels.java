package gb.sb3.gpu.core;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Display names of the processes that hold the NVIDIA GPU (INTERFACES 12.2 `<holders>`, 12.3, 27.2): the app label,
 * else the package, else the process name; at most 5 names, then {@code and N more}.
 */
public final class Labels {
    private Labels() {
    }

    /** Resolves a package to its app label; returns null when the label is not known. */
    public interface Labeler {
        String label(String pkg);
    }

    public static final int MAX_NAMES = 5;
    static final String COMPUTE_COMM = "gb_gpu_compute";

    /** The display name of one holder. */
    public static String holder(Holder h, Labeler l) {
        if (h.pkg.isEmpty() && COMPUTE_COMM.equals(h.comm)) return Texts.COMPUTE_SERVICE;
        if (!h.pkg.isEmpty()) {
            String x = l == null ? null : l.label(h.pkg);
            return x != null && !x.trim().isEmpty() ? x.trim() : h.pkg;
        }
        if (!h.comm.isEmpty()) return h.comm;
        return "pid " + h.pid;
    }

    /** Distinct display names of the holders, in holder order (several processes of one app give one name). */
    public static List<String> names(List<Holder> holders, Labeler l) {
        Set<String> s = new LinkedHashSet<String>();
        for (Holder h : holders) s.add(holder(h, l));
        return new ArrayList<String>(s);
    }

    /** {@code A, B, C, D, E and 2 more}; "" for an empty list. */
    public static String join(List<String> names) {
        StringBuilder b = new StringBuilder();
        int n = Math.min(MAX_NAMES, names.size());
        for (int i = 0; i < n; i++) {
            if (i > 0) b.append(", ");
            b.append(names.get(i));
        }
        if (names.size() > MAX_NAMES) b.append(" and ").append(names.size() - MAX_NAMES).append(" more");
        return b.toString();
    }

    /** The joined holder names, or {@code <N> app(s)} when the (rate-limited) holder list is still empty. */
    public static String holders(List<Holder> holders, Labeler l, int busyN) {
        String j = join(names(holders, l));
        return j.isEmpty() ? Texts.apps(Math.max(1, busyN)) : j;
    }
}
