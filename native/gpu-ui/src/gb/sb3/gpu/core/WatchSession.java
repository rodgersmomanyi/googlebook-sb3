package gb.sb3.gpu.core;

import java.io.IOException;
import java.net.SocketTimeoutException;

/**
 * Keeps a `watch` connection to gb_power open (INTERFACES 9.3) and reports each changed status. When the connection
 * fails it reports "unavailable" once, then reconnects with backoff. If gb_power refuses the watch (EBUSY: four
 * watchers already), it polls `status` instead. Callbacks run on the session's own thread. Each {@link #start()}
 * creates a fresh runner, so a stop followed by a quick start never leaves two runners delivering.
 */
public final class WatchSession {
    public interface Listener {
        void onStatus(Status s);

        /** No usable status (no socket, hello failed, wrong role, connection lost). */
        void onUnavailable(String why);
    }

    /** Tunables; tests shorten them. */
    public static final class Options {
        /** No line (status or the 30 s ping) within this time means a dead connection. */
        public int lineTimeoutMs = 45000;
        public int callTimeoutMs = GbPowerClient.DEFAULT_TIMEOUT_MS;
        public int pollMs = 5000;
        public int[] backoffMs = {1000, 2000, 4000, 8000, 10000};
        /** Stop on our own after this long (0 = never); the owner restarts it when it needs updates again. */
        public long maxLifeMs;
    }

    private final GbPowerClient client;
    private final Listener listener;
    private final Options opt;
    private Runner runner;

    public WatchSession(GbPowerClient client, Listener listener, Options opt) {
        this.client = client;
        this.listener = listener;
        this.opt = opt == null ? new Options() : opt;
    }

    public synchronized void start() {
        if (runner != null && runner.alive()) return;
        runner = new Runner();
        Thread t = new Thread(runner, "gb-power-watch");
        t.setDaemon(true);
        runner.thread = t;
        t.start();
    }

    public synchronized void stop() {
        if (runner != null) runner.cancel();
        runner = null;
    }

    public synchronized boolean running() {
        return runner != null && runner.alive();
    }

    private final class Runner implements Runnable {
        private volatile boolean cancelled;
        private volatile boolean finished;
        private volatile Conn conn;
        volatile Thread thread;
        private Status last;
        private boolean announcedDown;

        boolean alive() {
            return !cancelled && !finished;
        }

        void cancel() {
            cancelled = true;
            Conn c = conn;
            if (c != null) c.close();
            Thread t = thread;
            if (t != null) t.interrupt();
        }

        @Override
        public void run() {
            try {
                loop();
            } finally {
                finished = true;
            }
        }

        private void loop() {
            long born = System.currentTimeMillis();
            int attempt = 0;
            while (!cancelled) {
                boolean delivered = session();
                if (cancelled) break;
                if (delivered) attempt = 0;
                if (opt.maxLifeMs > 0 && System.currentTimeMillis() - born > opt.maxLifeMs) break;
                int wait = opt.backoffMs[Math.min(attempt, opt.backoffMs.length - 1)];
                attempt++;
                try {
                    Thread.sleep(wait);
                } catch (InterruptedException e) {
                    break;
                }
            }
        }

        private void deliver(Status s) {
            if (cancelled) return;
            if (last != null && last.sameAs(s)) return;
            last = s;
            announcedDown = false;
            listener.onStatus(s);
        }

        private void down(String why) {
            if (cancelled || announcedDown) return;
            announcedDown = true;
            last = null;
            listener.onUnavailable(why);
        }

        /** Runs one connection; returns true if at least one status was delivered. */
        private boolean session() {
            Transport t = client.factory().create();
            Conn c = new Conn(t);
            conn = c;
            if (cancelled) {
                c.close();
                return false;
            }
            boolean delivered = false;
            GbPowerClient.Deadline d = new GbPowerClient.Deadline(t, opt.callTimeoutMs);
            try {
                try {
                    t.connect();
                } catch (IOException e) {
                    down(d.fired() ? "timeout" : "no server");
                    return false;
                }
                Reply hello = c.request("hello", opt.callTimeoutMs);
                if (hello == null) {
                    down("closed");
                    return false;
                }
                GbPowerClient.Failure f = GbPowerClient.checkHello(hello, true);
                if (f != GbPowerClient.Failure.NONE) {
                    down(f == GbPowerClient.Failure.ROLE ? "role " + hello.str("role") : "hello failed");
                    return false;
                }
                Reply first = c.request("watch", opt.callTimeoutMs);
                d.close();
                if (first == null) {
                    down("closed");
                    return false;
                }
                if (!first.ok) {
                    c.close();
                    if ("EBUSY".equals(first.err)) return poll();
                    down("watch refused: " + first.err);
                    return false;
                }
                Status s = first.status();
                if (s == null) {
                    down("watch: no status");
                    return false;
                }
                deliver(s);
                delivered = true;
                for (;;) {
                    String line;
                    try {
                        line = c.readLine(opt.lineTimeoutMs);
                    } catch (SocketTimeoutException e) {
                        down("no ping");
                        return true;
                    }
                    if (line == null) {
                        down("closed");
                        return true;
                    }
                    Reply r = Reply.parse(line);
                    if ("ping".equals(r.cmd)) continue;
                    Status u = r.status();
                    if (u != null) deliver(u);
                }
            } catch (Reply.ProtocolException e) {
                down("protocol: " + e.getMessage());
                return delivered;
            } catch (IOException e) {
                down(d.fired() || e instanceof SocketTimeoutException ? "timeout" : "io: " + e.getMessage());
                return delivered;
            } finally {
                d.close();
                c.close();
                conn = null;
            }
        }

        /** Watch refused (EBUSY): poll `status` for a while, then let the loop try a real watch again. */
        private boolean poll() {
            boolean delivered = false;
            long born = System.currentTimeMillis();
            while (!cancelled) {
                GbPowerClient.Outcome o = client.status();
                Status s = o.status();
                if (s == null) {
                    down("poll: " + o);
                    return delivered;
                }
                deliver(s);
                delivered = true;
                if (System.currentTimeMillis() - born > 6L * opt.pollMs) return true;
                try {
                    Thread.sleep(opt.pollMs);
                } catch (InterruptedException e) {
                    return delivered;
                }
            }
            return delivered;
        }
    }
}
