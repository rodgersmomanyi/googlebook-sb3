package gb.sb3.gpu.core;

import java.util.Map;

/** One process holding the nouveau DRM nodes (INTERFACES 9.3 `holders`, 9.5 `holders`). */
public final class Holder {
    public final int pid;
    public final String comm;
    public final int uid;
    public final int user;
    public final String pkg;
    public final boolean closable;

    public Holder(int pid, String comm, int uid, int user, String pkg, boolean closable) {
        this.pid = pid;
        this.comm = comm == null ? "" : comm;
        this.uid = uid;
        this.user = user;
        this.pkg = pkg == null ? "" : pkg;
        this.closable = closable;
    }

    /** Returns null when the entry is not a usable holder object (no positive pid). */
    public static Holder from(Object o) {
        Map<String, Object> m = Json.obj(o);
        if (m == null) return null;
        int pid = Json.int32(m, "pid", -1);
        if (pid <= 0) return null;
        return new Holder(pid, Json.str(m, "comm", ""), Json.int32(m, "uid", -1), Json.int32(m, "user", -1),
                Json.str(m, "pkg", ""), Json.bool(m, "closable", false));
    }

    /** A short display name: the package when known, else the process name. */
    public String displayName() {
        if (!pkg.isEmpty()) return pkg;
        if (!comm.isEmpty()) return comm;
        return "pid " + pid;
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Holder)) return false;
        Holder h = (Holder) o;
        return pid == h.pid && uid == h.uid && user == h.user && closable == h.closable && comm.equals(h.comm)
                && pkg.equals(h.pkg);
    }

    @Override
    public int hashCode() {
        return pid * 31 + comm.hashCode();
    }

    @Override
    public String toString() {
        return comm + ":" + pid + (pkg.isEmpty() ? "" : "(" + pkg + ")") + (closable ? "+" : "");
    }
}
