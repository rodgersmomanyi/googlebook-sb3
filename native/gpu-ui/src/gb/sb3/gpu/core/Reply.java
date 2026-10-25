package gb.sb3.gpu.core;

import java.util.Map;

/** One reply line from gb_power (INTERFACES 9.1, 9.4): {"v":1,"ok":...,"cmd":...}. */
public final class Reply {
    public final boolean ok;
    public final String cmd;
    /** Error code (INTERFACES 9.4) when {@link #ok} is false, else "". */
    public final String err;
    public final String msg;
    public final Map<String, Object> obj;
    public final String line;

    private Reply(Map<String, Object> obj, String line) {
        this.obj = obj;
        this.line = line;
        ok = Json.bool(obj, "ok", false);
        cmd = Json.str(obj, "cmd", "");
        err = ok ? "" : Json.str(obj, "err", "EIO");
        msg = Json.str(obj, "msg", "");
    }

    /** Thrown when a line is not a valid version-1 reply. */
    public static final class ProtocolException extends Exception {
        private static final long serialVersionUID = 1L;

        public ProtocolException(String msg) {
            super(msg);
        }
    }

    /** Parses and validates one reply line (without the trailing newline). */
    public static Reply parse(String line) throws ProtocolException {
        Map<String, Object> o;
        try {
            o = Json.parseObject(line);
        } catch (Json.ParseException e) {
            throw new ProtocolException("not JSON: " + e.getMessage());
        }
        if (Json.num(o, "v", -1) != 1) throw new ProtocolException("unsupported reply version");
        if (!(o.get("ok") instanceof Boolean)) throw new ProtocolException("reply without ok");
        if (!(o.get("cmd") instanceof String)) throw new ProtocolException("reply without cmd");
        return new Reply(o, line);
    }

    public String str(String k) {
        return Json.str(obj, k, "");
    }

    public long num(String k, long def) {
        return Json.num(obj, k, def);
    }

    /** The status carried by a `status` reply (also each `watch` line), or null. */
    public Status status() {
        return Status.from(obj);
    }

    @Override
    public String toString() {
        return line;
    }
}
