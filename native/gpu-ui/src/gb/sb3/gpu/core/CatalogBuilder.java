package gb.sb3.gpu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The app catalog GbGpu pushes to gb_power (INTERFACES 24.3, 27.5). Input: every launchable activity of user 0
 * (ACTION_MAIN + CATEGORY_LAUNCHER, visible through QUERY_ALL_PACKAGES). Kept: apps whose {@code ApplicationInfo.flags}
 * have no FLAG_SYSTEM (neither stock nor updated system apps, review A4), not {@code gb.sb3.*}, with a valid package
 * name. Class {@code g} when {@code category == CATEGORY_GAME} (the platform's own test,
 * {@code a17 GraphicsEnvironment.java:171}) or the legacy {@code FLAG_IS_GAME} is set, else {@code a}. At most 512
 * packages, games first. Pure Java: the Android layer passes the raw {@code ApplicationInfo} values.
 */
public final class CatalogBuilder {
    private CatalogBuilder() {
    }

    /** {@code ApplicationInfo.FLAG_SYSTEM}. */
    public static final int FLAG_SYSTEM = 0x00000001;
    /** {@code ApplicationInfo.FLAG_IS_GAME} (deprecated in the SDK, still set by older apps; the legacy isGame). */
    public static final int FLAG_IS_GAME = 0x02000000;
    /** {@code ApplicationInfo.CATEGORY_GAME}. */
    public static final int CATEGORY_GAME = 0;
    /** Request lines are at most 255 bytes including the newline (INTERFACES 9.1). */
    public static final int MAX_LINE = 254;

    /** One launchable activity's application, as PackageManager reports it. */
    public static final class App {
        public final String pkg;
        public final String label;
        public final int flags;
        public final int category;

        public App(String pkg, String label, int flags, int category) {
            this.pkg = pkg;
            this.label = label == null ? "" : label;
            this.flags = flags;
            this.category = category;
        }
    }

    /** One catalog entry. */
    public static final class Entry {
        public final String pkg;
        public final String label;
        public final boolean game;

        public Entry(String pkg, String label, boolean game) {
            this.pkg = pkg;
            this.label = label == null || label.isEmpty() ? pkg : label;
            this.game = game;
        }

        public char cls() {
            return game ? 'g' : 'a';
        }

        @Override
        public String toString() {
            return cls() + ":" + pkg;
        }
    }

    public static boolean isGame(int flags, int category) {
        return category == CATEGORY_GAME || (flags & FLAG_IS_GAME) != 0;
    }

    /** INTERFACES 24.3 filter and classes; sorted games first, then by package; at most 512 entries. */
    public static List<Entry> build(List<App> launchable) {
        Map<String, Entry> by = new LinkedHashMap<String, Entry>();
        for (App a : launchable) {
            if (a == null || a.pkg == null) continue;
            if ((a.flags & FLAG_SYSTEM) != 0) continue;
            if (a.pkg.startsWith("gb.sb3.")) continue;
            if (!GbPowerClient.validPackage(a.pkg)) continue;
            Entry old = by.get(a.pkg);
            boolean game = isGame(a.flags, a.category) || (old != null && old.game);
            by.put(a.pkg, new Entry(a.pkg, old != null && !old.label.equals(old.pkg) ? old.label : a.label, game));
        }
        List<Entry> out = new ArrayList<Entry>(by.values());
        Collections.sort(out, new Comparator<Entry>() {
            @Override
            public int compare(Entry x, Entry y) {
                if (x.game != y.game) return x.game ? -1 : 1;
                return x.pkg.compareTo(y.pkg);
            }
        });
        return out.size() > GbPowerClient.CATALOG_MAX ? new ArrayList<Entry>(out.subList(0,
                GbPowerClient.CATALOG_MAX)) : out;
    }

    /**
     * The request lines of one push (27.5): {@code apps-begin <n>}, {@code apps-add g:...} lines, {@code apps-add a:...}
     * lines (each at most 254 characters plus the newline), {@code apps-commit <n>}.
     */
    public static List<String> lines(List<Entry> entries) {
        List<String> out = new ArrayList<String>();
        out.add("apps-begin " + entries.size());
        for (char c : new char[] {'g', 'a'}) {
            StringBuilder b = null;
            for (Entry e : entries) {
                if (e.cls() != c) continue;
                if (b != null && b.length() + 1 + e.pkg.length() > MAX_LINE) {
                    out.add(b.toString());
                    b = null;
                }
                if (b == null) b = new StringBuilder("apps-add ").append(c).append(':').append(e.pkg);
                else b.append(',').append(e.pkg);
            }
            if (b != null) out.add(b.toString());
        }
        out.add("apps-commit " + entries.size());
        return out;
    }
}
