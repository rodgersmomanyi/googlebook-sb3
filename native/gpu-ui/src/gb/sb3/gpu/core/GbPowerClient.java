package gb.sb3.gpu.core;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * Client for {@code /dev/socket/gb_power} (INTERFACES 9 and, v1.2, 26). Every call opens a connection, sends
 * {@code hello}, checks protocol version and role, sends one command (or, with {@link #exchangeAll}, several), reads
 * one reply per request and closes. The whole call is bounded by a deadline: when it expires the transport is closed
 * from a timer thread, which also unblocks a stuck connect or read. Thread-safe; never call it on the Android main
 * thread.
 */
public final class GbPowerClient {
    public static final String SOCKET_PATH = "/dev/socket/gb_power";
    public static final int API = 1;
    public static final int DEFAULT_TIMEOUT_MS = 2000;
    /** The catalog push (24.3) sends up to about 70 lines on one connection; it gets a longer deadline. */
    public static final int BATCH_TIMEOUT_MS = 6000;

    /** Why a call produced no usable reply. A server error reply is NONE with {@code reply.ok == false}. */
    public enum Failure {
        NONE,
        /** Socket missing or nobody listening (ENOENT / ECONNREFUSED). */
        NO_SERVER,
        /** The deadline expired. */
        TIMEOUT,
        /** The connection broke or the server closed it. */
        IO,
        /** A reply line was not a valid version-1 reply, or a request was refused before the socket. */
        PROTOCOL,
        /** {@code hello} failed or reported another API version. */
        HELLO,
        /** Our peer role is not root, shell or ui (e.g. the APK is not the system copy). */
        ROLE
    }

    /** Result of one call. */
    public static final class Outcome {
        public final Failure failure;
        public final String detail;
        public final Reply hello;
        public final Reply reply;

        Outcome(Failure f, String detail, Reply hello, Reply reply) {
            this.failure = f;
            this.detail = detail == null ? "" : detail;
            this.hello = hello;
            this.reply = reply;
        }

        public static Outcome fail(Failure f, String detail) {
            return new Outcome(f, detail, null, null);
        }

        /** A completed exchange with this reply (host tests build outcomes with it). */
        public static Outcome replied(Reply reply) {
            return new Outcome(Failure.NONE, "", null, reply);
        }

        /** True when the server answered and the command succeeded. */
        public boolean ok() {
            return failure == Failure.NONE && reply != null && reply.ok;
        }

        /** The server's error code (INTERFACES 9.4), or "" when the command succeeded or no reply arrived. */
        public String err() {
            return failure == Failure.NONE && reply != null ? reply.err : "";
        }

        /** The status for a successful `status` call, else null. */
        public Status status() {
            return ok() ? reply.status() : null;
        }

        /** A string field of a successful reply, else "". */
        public String str(String k) {
            return ok() ? reply.str(k) : "";
        }

        public String role() {
            return hello == null ? "" : hello.str("role");
        }

        @Override
        public String toString() {
            return failure == Failure.NONE ? String.valueOf(reply) : failure + " (" + detail + ")";
        }
    }

    /** Result of {@link #exchangeAll}: one reply per request line that was sent, in order. */
    public static final class Batch {
        public final Failure failure;
        public final String detail;
        public final List<Reply> replies;

        Batch(Failure f, String detail, List<Reply> replies) {
            this.failure = f;
            this.detail = detail == null ? "" : detail;
            this.replies = Collections.unmodifiableList(replies);
        }

        /** True when every line was answered with {@code ok} true. */
        public boolean ok(int lines) {
            if (failure != Failure.NONE || replies.size() != lines) return false;
            for (Reply r : replies) if (!r.ok) return false;
            return true;
        }

        /** The last reply (the first error reply when the batch stopped early), or null. */
        public Reply last() {
            return replies.isEmpty() ? null : replies.get(replies.size() - 1);
        }

        @Override
        public String toString() {
            return failure == Failure.NONE ? replies.size() + " replies, last " + last() : failure + " (" + detail + ")";
        }
    }

    /** Roles that may read status (INTERFACES 9.3); anything else shows the tile as Unavailable (27.1 row 1). */
    static final Set<String> STATUS_ROLES = Collections.unmodifiableSet(new HashSet<String>(
            Arrays.asList("root", "shell", "ui")));

    /** Commands the app may send without an argument (INTERFACES 9.3, 26.1). */
    static final Set<String> NO_ARG = Collections.unmodifiableSet(new HashSet<String>(Arrays.asList(
            "hello", "status", "watch", "holders", "enable", "disable", "detach-request", "report", "stop-compute",
            "compute-list", "compute-start", "get-mode", "run-selftest", "list-holders")));
    /** Commands that need exactly one argument. */
    static final Set<String> WITH_ARG = Collections.unmodifiableSet(new HashSet<String>(Arrays.asList(
            "close-holders", "compute-allow", "compute-deny", "set-mode", "set-app-policy", "set-app-intel",
            "set-app-nvidia", "apps-begin", "apps-add", "apps-commit", "close-holders-and-detach", "ui-caps")));
    /** Commands with an optional argument ({@code get-apps [<start>]}). */
    static final Set<String> OPT_ARG = Collections.unmodifiableSet(new HashSet<String>(Arrays.asList("get-apps")));

    /** Package-name rule shared with gb_power (INTERFACES 9.3 `compute-allow`, `pkg_name_ok()`). */
    static final Pattern PKG = Pattern.compile("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+");
    static final Pattern PIDS = Pattern.compile("[1-9][0-9]{0,6}(,[1-9][0-9]{0,6}){0,15}");
    static final Pattern DEC = Pattern.compile("0|[1-9][0-9]{0,2}");
    /** Maximum catalog size (24.3) and number of per-app choices (24.2). */
    public static final int CATALOG_MAX = 512;
    public static final int CHOICES_MAX = 128;

    private static final ScheduledExecutorService TIMER = Executors.newSingleThreadScheduledExecutor(
            new ThreadFactory() {
                @Override
                public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "gb-power-deadline");
                    t.setDaemon(true);
                    return t;
                }
            });

    private final Transport.Factory factory;
    private final int timeoutMs;

    public GbPowerClient(Transport.Factory factory, int timeoutMs) {
        this.factory = factory;
        this.timeoutMs = timeoutMs;
    }

    public Transport.Factory factory() {
        return factory;
    }

    public int timeoutMs() {
        return timeoutMs;
    }

    public static boolean validPackage(String p) {
        return p != null && p.length() <= 127 && PKG.matcher(p).matches();
    }

    static boolean decimalUpTo(String a, int max) {
        return a != null && DEC.matcher(a).matches() && Integer.parseInt(a) <= max;
    }

    /** {@code <pkg>:1} or {@code <pkg>:0} (26.1 `set-app-intel`, `set-app-nvidia`). */
    static boolean validChoiceArg(String a) {
        if (a == null || a.length() < 3) return false;
        char last = a.charAt(a.length() - 1);
        if (a.charAt(a.length() - 2) != ':' || (last != '0' && last != '1')) return false;
        return validPackage(a.substring(0, a.length() - 2));
    }

    /** {@code g:<pkg>[,<pkg>...]} or {@code a:...} (26.1 `apps-add`); the 255-byte line limit is checked by Conn. */
    static boolean validAppsAdd(String a) {
        if (a == null || a.length() < 3 || a.charAt(1) != ':' || (a.charAt(0) != 'g' && a.charAt(0) != 'a')) {
            return false;
        }
        String list = a.substring(2);
        if (list.endsWith(",")) return false;
        for (String p : list.split(",", -1)) {
            if (!validPackage(p)) return false;
        }
        return true;
    }

    /** Validates a command and its argument before anything reaches the socket. */
    public static boolean validRequest(String cmd, String arg) {
        if (cmd == null) return false;
        if (NO_ARG.contains(cmd)) return arg == null;
        if (OPT_ARG.contains(cmd)) return arg == null || decimalUpTo(arg, CHOICES_MAX);
        if (!WITH_ARG.contains(cmd) || arg == null) return false;
        switch (cmd) {
            case "close-holders":
            case "close-holders-and-detach":
                return PIDS.matcher(arg).matches();
            case "set-mode":
                return "auto".equals(arg) || "intel".equals(arg) || "nvidia".equals(arg);
            case "set-app-policy":
                return "games".equals(arg) || "all".equals(arg);
            case "set-app-intel":
            case "set-app-nvidia":
                return validChoiceArg(arg);
            case "apps-begin":
            case "apps-commit":
                return decimalUpTo(arg, CATALOG_MAX);
            case "apps-add":
                return validAppsAdd(arg);
            case "ui-caps":
                return "notify".equals(arg) || "none".equals(arg);
            default:
                return validPackage(arg); // compute-allow, compute-deny
        }
    }

    /** Splits {@code "cmd arg"} and validates it; returns false for anything {@link #validRequest} refuses. */
    public static boolean validLine(String line) {
        if (line == null) return false;
        int sp = line.indexOf(' ');
        return sp < 0 ? validRequest(line, null) : validRequest(line.substring(0, sp), line.substring(sp + 1));
    }

    /** Closes a transport when the deadline passes; {@link #fired()} tells whether it did. */
    static final class Deadline implements AutoCloseable {
        private final ScheduledFuture<?> f;
        private volatile boolean fired;

        Deadline(final Transport t, long ms) {
            f = TIMER.schedule(new Runnable() {
                @Override
                public void run() {
                    fired = true;
                    t.close();
                }
            }, ms, TimeUnit.MILLISECONDS);
        }

        boolean fired() {
            return fired;
        }

        @Override
        public void close() {
            f.cancel(false);
        }
    }

    public Outcome status() {
        return call("status", null);
    }

    public Outcome call(String cmd) {
        return call(cmd, null);
    }

    /** One complete exchange: connect, hello, command. Never throws. */
    public Outcome call(String cmd, String arg) {
        if (!validRequest(cmd, arg)) return Outcome.fail(Failure.PROTOCOL, "invalid request " + cmd);
        return exchange(arg == null ? cmd : cmd + " " + arg, !"hello".equals(cmd));
    }

    /** The open connection after a successful hello (or the failure that prevented it). */
    private static final class Opened {
        final Failure failure;
        final String detail;
        final Reply hello;

        Opened(Failure f, String detail, Reply hello) {
            this.failure = f;
            this.detail = detail;
            this.hello = hello;
        }
    }

    /** Connects and runs `hello` (role check unless {@code needRole} is false). Throws only for framing errors. */
    private Opened open(Transport t, Conn c, Deadline d, long end, boolean needRole)
            throws IOException, Reply.ProtocolException {
        try {
            t.connect();
        } catch (IOException e) {
            return new Opened(d.fired() ? Failure.TIMEOUT : Failure.NO_SERVER, String.valueOf(e.getMessage()), null);
        }
        // gb_power refuses a 9th connection with one EBUSY line and closes at once (INTERFACES 9.1), so our hello may
        // hit EPIPE; the refusal line is still readable, so read it before giving up.
        IOException sendErr = null;
        try {
            c.send("hello");
        } catch (IOException e) {
            sendErr = e;
        }
        String hl;
        try {
            hl = c.readLine(remaining(end));
        } catch (IOException e) {
            throw sendErr != null ? sendErr : e;
        }
        if (hl == null) {
            return new Opened(Failure.IO, sendErr != null ? "send failed: " + sendErr.getMessage()
                    : "closed before hello reply", null);
        }
        Reply hello = Reply.parse(hl);
        Failure hf = checkHello(hello, needRole);
        if (hf != Failure.NONE) return new Opened(hf, hello.ok ? "role " + hello.str("role") : hello.err, hello);
        return new Opened(Failure.NONE, "", hello);
    }

    private static Failure ioFailure(Deadline d, IOException e, long end) {
        // The read timeout (remaining time) and the deadline timer expire together; either one is a TIMEOUT.
        boolean late = d.fired() || e instanceof SocketTimeoutException || System.currentTimeMillis() >= end;
        return late ? Failure.TIMEOUT : Failure.IO;
    }

    /**
     * Sends an unvalidated request line after {@code hello} (tests use it for the golden transcripts). When
     * {@code needRole} is false the role check is skipped.
     */
    public Outcome exchange(String line, boolean needRole) {
        long end = System.currentTimeMillis() + timeoutMs;
        Transport t = factory.create();
        Deadline d = new Deadline(t, timeoutMs);
        Conn c = new Conn(t);
        try {
            Opened o = open(t, c, d, end, needRole);
            if (o.failure != Failure.NONE) return new Outcome(o.failure, o.detail, o.hello, null);
            if ("hello".equals(line)) return new Outcome(Failure.NONE, "", o.hello, o.hello);
            Reply r = c.request(line, remaining(end));
            if (r == null) return new Outcome(Failure.IO, "closed before reply", o.hello, null);
            return new Outcome(Failure.NONE, "", o.hello, r);
        } catch (Reply.ProtocolException e) {
            return Outcome.fail(Failure.PROTOCOL, e.getMessage());
        } catch (IOException e) {
            return Outcome.fail(ioFailure(d, e, end), String.valueOf(e.getMessage()));
        } finally {
            d.close();
            c.close();
        }
    }

    /**
     * INTERFACES 27.5: one connection, one {@code hello}, then the given request lines in order, reading one reply per
     * line (lock-step, so gb_power's 8 KiB output buffer never fills). Stops at the first error reply, which is then
     * the last element of {@link Batch#replies}; gb_power discards a staged catalog when the connection closes. Every
     * line is validated before anything is sent. Never throws.
     */
    public Batch exchangeAll(List<String> lines, int timeout) {
        for (String l : lines) {
            if (!validLine(l)) {
                return new Batch(Failure.PROTOCOL, "invalid request " + l, new ArrayList<Reply>());
            }
        }
        List<Reply> out = new ArrayList<Reply>();
        long end = System.currentTimeMillis() + timeout;
        Transport t = factory.create();
        Deadline d = new Deadline(t, timeout);
        Conn c = new Conn(t);
        try {
            Opened o = open(t, c, d, end, true);
            if (o.failure != Failure.NONE) return new Batch(o.failure, o.detail, out);
            for (String l : lines) {
                Reply r = c.request(l, remaining(end));
                if (r == null) return new Batch(Failure.IO, "closed before reply to " + l, out);
                out.add(r);
                if (!r.ok) break;
            }
            return new Batch(Failure.NONE, "", out);
        } catch (Reply.ProtocolException e) {
            return new Batch(Failure.PROTOCOL, e.getMessage(), out);
        } catch (IOException e) {
            return new Batch(ioFailure(d, e, end), String.valueOf(e.getMessage()), out);
        } finally {
            d.close();
            c.close();
        }
    }

    /**
     * All per-app choices through `get-apps` paging (26.1: {@code next} is the index of the next entry, -1 at the end).
     * Returns null when a page fails or the paging does not advance.
     */
    public List<AppRows.Choice> getAllChoices() {
        List<AppRows.Choice> all = new ArrayList<AppRows.Choice>();
        int start = 0;
        for (int page = 0; page < 40; page++) {
            Outcome o = call("get-apps", start == 0 ? null : Integer.toString(start));
            if (!o.ok()) return null;
            for (Object e : Json.list(o.reply.obj, "prefs")) {
                AppRows.Choice ch = AppRows.Choice.from(Json.obj(e));
                if (ch != null) all.add(ch);
            }
            long next = o.reply.num("next", -1);
            if (next < 0) return all;
            if (next <= start || next > CHOICES_MAX) return null;
            start = (int) next;
        }
        return null;
    }

    /** INTERFACES 9.3/27.1: hello must succeed, report api 1 and (for status-level commands) a privileged role. */
    static Failure checkHello(Reply hello, boolean needRole) {
        if (!hello.ok || !"hello".equals(hello.cmd)) return Failure.HELLO;
        if (hello.num("api", -1) != API) return Failure.HELLO;
        if (needRole && !STATUS_ROLES.contains(hello.str("role"))) return Failure.ROLE;
        return Failure.NONE;
    }

    static int remaining(long end) {
        long r = end - System.currentTimeMillis();
        return (int) Math.max(1, Math.min(Integer.MAX_VALUE, r));
    }
}
