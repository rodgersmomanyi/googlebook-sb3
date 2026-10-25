package gb.sb3.gpu.core;

import java.io.Closeable;
import java.io.IOException;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;

/**
 * One connection to gb_power with the INTERFACES 9.1 framing: request lines of at most 255 bytes including the
 * newline, reply lines of at most 4096 bytes including the newline.
 */
public final class Conn implements Closeable {
    public static final int MAX_REQUEST = 255;
    public static final int MAX_REPLY = 4096;

    private final Transport t;
    private final byte[] rbuf = new byte[1024];
    private final byte[] line = new byte[MAX_REPLY];
    private int rpos;
    private int rlen;
    private int llen;

    public Conn(Transport t) {
        this.t = t;
    }

    public Transport transport() {
        return t;
    }

    /** Sends one request line; {@code req} must not contain the newline. Refuses lines over the limit. */
    public void send(String req) throws IOException {
        byte[] b = (req + "\n").getBytes(StandardCharsets.US_ASCII);
        if (b.length > MAX_REQUEST) throw new IOException("request too long");
        for (int k = 0; k < b.length - 1; k++) {
            if (b[k] < 0x20 || b[k] > 0x7e) throw new IOException("request has a non-printable byte");
        }
        t.write(b);
    }

    /**
     * Returns the next line without its newline, or null at end of stream.
     *
     * @param timeoutMs wait at most this long for each read (0 = no limit)
     * @throws SocketTimeoutException when nothing arrives in time
     * @throws Reply.ProtocolException when a line exceeds 4096 bytes
     */
    public String readLine(int timeoutMs) throws IOException, Reply.ProtocolException {
        for (;;) {
            while (rpos < rlen) {
                byte c = rbuf[rpos++];
                if (c == '\n') {
                    String s = new String(line, 0, llen, StandardCharsets.UTF_8);
                    llen = 0;
                    return s;
                }
                if (llen >= MAX_REPLY - 1) throw new Reply.ProtocolException("reply longer than 4096 bytes");
                line[llen++] = c;
            }
            int r = t.read(rbuf, timeoutMs);
            if (r < 0) return null;
            rpos = 0;
            rlen = r;
        }
    }

    /** Sends a request and returns the next reply (null at end of stream). */
    public Reply request(String req, int timeoutMs) throws IOException, Reply.ProtocolException {
        send(req);
        String l = readLine(timeoutMs);
        return l == null ? null : Reply.parse(l);
    }

    @Override
    public void close() {
        t.close();
    }
}
