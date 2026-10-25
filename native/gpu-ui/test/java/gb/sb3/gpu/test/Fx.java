package gb.sb3.gpu.test;

import gb.sb3.gpu.core.Holder;
import gb.sb3.gpu.core.Labels;
import gb.sb3.gpu.core.Reply;
import gb.sb3.gpu.core.Status;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Test fixtures: v1.2 status lines in the INTERFACES 9.5 key order with overridable fields. */
final class Fx {
    private Fx() {
    }

    /** A raw JSON value (lists, objects) for {@link #line}. */
    static final class Raw {
        final String json;

        Raw(String json) {
            this.json = json;
        }
    }

    static Raw raw(String json) {
        return new Raw(json);
    }

    static final String[] KEYS = {"state", "n", "holders", "mode", "result", "persist", "clean_boots", "probation",
        "used", "foreign", "wedged", "fault_reason", "bdf", "driver", "runtime_status", "power_state", "refcnt",
        "latch", "base", "dtx", "compute", "payload", "gfxdriver", "gfx_mode", "gfx_policy", "gfx_active",
        "gfx_reason", "gfx_ok", "gfx_test", "gfx_test_t", "gfx_cap", "gfx_seq", "pending", "detach_ref",
        "apps_sync", "t_ms"};

    /** A healthy auto-mode world with NVIDIA app graphics on (27.1 row 12). */
    static Map<String, Object> healthy() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        Object[] kv = {"state", "standby", "n", 0, "holders", raw("[]"), "mode", "auto", "result", "clean",
            "persist", 1, "clean_boots", 3, "probation", 0, "used", 1, "foreign", 1, "wedged", 0, "fault_reason", "",
            "bdf", "0000:02:00.0", "driver", "nouveau", "runtime_status", "suspended", "power_state", "D3cold",
            "refcnt", 0, "latch", "locked", "base", "attached", "dtx", "ready", "compute", "stopped",
            "payload", "mounted", "gfxdriver", 1, "gfx_mode", "auto", "gfx_policy", "games", "gfx_active", "nvidia",
            "gfx_reason", "ok", "gfx_ok", 1, "gfx_test", "pass", "gfx_test_t", 1761390000L, "gfx_cap", 1,
            "gfx_seq", 1, "pending", "", "detach_ref", 0, "apps_sync", 1, "t_ms", 1234567L};
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    static String esc(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\');
            if (c == '\n') {
                b.append("\\n");
                continue;
            }
            b.append(c);
        }
        return b.append('"').toString();
    }

    static String val(Object v) {
        if (v instanceof Raw) return ((Raw) v).json;
        if (v instanceof String) return esc((String) v);
        if (v == null) return "null";
        return String.valueOf(v);
    }

    /** The status line for {@code healthy()} with {@code kv} overrides (key, value, key, value, ...). */
    static String line(Object... kv) {
        Map<String, Object> m = healthy();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        StringBuilder b = new StringBuilder("{\"v\":1,\"ok\":true,\"cmd\":\"status\"");
        for (Map.Entry<String, Object> e : m.entrySet()) {
            b.append(',').append(esc(e.getKey())).append(':').append(val(e.getValue()));
        }
        return b.append('}').toString();
    }

    static Status st(Object... kv) {
        try {
            Status s = Reply.parse(line(kv)).status();
            if (s == null) throw new AssertionError("not a status: " + line(kv));
            return s;
        } catch (Reply.ProtocolException e) {
            throw new AssertionError(e);
        }
    }

    /** A holder object as gb_power prints it. */
    static String holder(int pid, String comm, int uid, String pkg, boolean closable) {
        return "{\"pid\":" + pid + ",\"comm\":" + esc(comm) + ",\"uid\":" + uid + ",\"user\":" + (uid / 100000)
                + ",\"pkg\":" + esc(pkg) + ",\"closable\":" + closable + "}";
    }

    static Raw holders(String... objs) {
        return raw("[" + String.join(",", objs) + "]");
    }

    static Holder h(int pid, String comm, int uid, String pkg, boolean closable) {
        return new Holder(pid, comm, uid, uid / 100000, pkg, closable);
    }

    /** A labeler that knows a few labels. */
    static Labels.Labeler labels(final String... pkgLabel) {
        final Map<String, String> m = new HashMap<String, String>();
        for (int i = 0; i < pkgLabel.length; i += 2) m.put(pkgLabel[i], pkgLabel[i + 1]);
        return new Labels.Labeler() {
            @Override
            public String label(String pkg) {
                return m.get(pkg);
            }
        };
    }

    static String join(List<String> l) {
        return String.join("|", l);
    }
}
