package gb.sb3.gpu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The per-app list of the Graphics page (INTERFACES 27.2 item 6): one row per catalog app, games first, then by label;
 * the effective graphics per the INTERFACES 24.4 formula; the two switches {@code Use NVIDIA} and {@code Always Intel},
 * of which at most one is on (one choice per package, 24.2); holder hints.
 */
public final class AppRows {
    private AppRows() {
    }

    public static final String DEFAULT = "default";
    public static final String INTEL = "intel";
    public static final String NVIDIA = "nvidia";

    /** One entry of a `get-apps` reply (26.1). */
    public static final class Choice {
        public final String pkg;
        public final String choice;

        public Choice(String pkg, String choice) {
            this.pkg = pkg;
            this.choice = choice;
        }

        /** Returns null for anything that is not {@code {"pkg": <valid>, "choice": "intel"|"nvidia"}}. */
        public static Choice from(Map<String, Object> m) {
            String p = Json.str(m, "pkg", "");
            String c = Json.str(m, "choice", "");
            if (!GbPowerClient.validPackage(p) || !(INTEL.equals(c) || NVIDIA.equals(c))) return null;
            return new Choice(p, c);
        }
    }

    /** One row. Mutable: the page updates {@link #choice} as switches change and replies arrive. */
    public static final class Row {
        public final String pkg;
        public final String label;
        public final boolean game;
        public String choice;
        public boolean holder;

        public Row(String pkg, String label, boolean game, String choice, boolean holder) {
            this.pkg = pkg;
            this.label = label;
            this.game = game;
            this.choice = norm(choice);
            this.holder = holder;
        }

        public boolean useNvidia() {
            return NVIDIA.equals(choice);
        }

        public boolean alwaysIntel() {
            return INTEL.equals(choice);
        }

        /** The effective graphics of this app under {@code policy} (24.4). */
        public boolean nvidia(String policy) {
            return effectiveNvidia(game, choice, policy);
        }

        public String effectiveText(String policy) {
            return nvidia(policy) ? Texts.NVIDIA : Texts.INTEL;
        }

        /** 27.2: a holder whose effective graphics is Intel must be restarted to switch. */
        public String hint(String policy) {
            if (!holder) return null;
            return nvidia(policy) ? Texts.HOLDER_NOW : Texts.HOLDER_RESTART;
        }
    }

    static String norm(String c) {
        return INTEL.equals(c) || NVIDIA.equals(c) ? c : DEFAULT;
    }

    /**
     * INTERFACES 24.4 for one installed catalog app: {@code base = policy all ? catalog : games}; in the opt-in list
     * when in base or marked Use NVIDIA, unless marked Always Intel.
     */
    public static boolean effectiveNvidia(boolean game, String choice, String policy) {
        String c = norm(choice);
        if (INTEL.equals(c)) return false;
        boolean base = "all".equals(policy) || game;
        return base || NVIDIA.equals(c);
    }

    /** Builds the rows: games first, then by label (case-insensitive), then by package. */
    public static List<Row> build(List<CatalogBuilder.Entry> catalog, List<Choice> choices, List<Holder> holders) {
        Map<String, String> ch = new HashMap<String, String>();
        if (choices != null) for (Choice c : choices) ch.put(c.pkg, c.choice);
        Set<String> hold = new HashSet<String>();
        if (holders != null) for (Holder h : holders) if (!h.pkg.isEmpty()) hold.add(h.pkg);
        List<Row> rows = new ArrayList<Row>();
        for (CatalogBuilder.Entry e : catalog) rows.add(new Row(e.pkg, e.label, e.game, ch.get(e.pkg),
                hold.contains(e.pkg)));
        Collections.sort(rows, new Comparator<Row>() {
            @Override
            public int compare(Row x, Row y) {
                if (x.game != y.game) return x.game ? -1 : 1;
                int c = x.label.toLowerCase(Locale.ROOT).compareTo(y.label.toLowerCase(Locale.ROOT));
                return c != 0 ? c : x.pkg.compareTo(y.pkg);
            }
        });
        return rows;
    }

    /** Marks the rows whose package holds the NVIDIA GPU now. */
    public static void setHolders(List<Row> rows, List<Holder> holders) {
        Set<String> hold = new HashSet<String>();
        if (holders != null) for (Holder h : holders) if (!h.pkg.isEmpty()) hold.add(h.pkg);
        for (Row r : rows) r.holder = hold.contains(r.pkg);
    }

    /** A switch change: the command to send and the optimistic new choice. */
    public static final class Change {
        public final String cmd;
        public final String arg;
        public final String choice;

        Change(String cmd, String arg, String choice) {
            this.cmd = cmd;
            this.arg = arg;
            this.choice = choice;
        }
    }

    /**
     * Turning {@code Use NVIDIA} ({@code which} = nvidia) or {@code Always Intel} (intel) on or off. On replaces the
     * other choice (so the other switch turns off); off clears only that choice (26.1).
     */
    public static Change change(String pkg, String which, boolean on) {
        String w = NVIDIA.equals(which) ? NVIDIA : INTEL;
        String cmd = NVIDIA.equals(w) ? "set-app-nvidia" : "set-app-intel";
        return new Change(cmd, pkg + (on ? ":1" : ":0"), on ? w : DEFAULT);
    }

    /**
     * Applies a reply: a successful one sets the choice gb_power reports; a failed one reverts the row to
     * {@code before} (27.2: a failed set-app-* reverts the switch). Returns true when the reply was a success.
     */
    public static boolean apply(Row r, String before, GbPowerClient.Outcome o) {
        if (o != null && o.ok() && r.pkg.equals(o.reply.str("pkg"))) {
            r.choice = norm(o.reply.str("choice"));
            return true;
        }
        r.choice = norm(before);
        return false;
    }
}
