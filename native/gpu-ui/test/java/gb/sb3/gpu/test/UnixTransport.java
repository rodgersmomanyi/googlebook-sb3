package gb.sb3.gpu.test;

import gb.sb3.gpu.core.Transport;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedSelectorException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

/** Host-test {@link Transport}: a java.nio UNIX-domain stream channel with selector-based read timeouts. */
final class UnixTransport implements Transport {
    static Transport.Factory factory(final String path) {
        return new Transport.Factory() {
            @Override
            public Transport create() {
                return new UnixTransport(path);
            }
        };
    }

    private final String path;
    private SocketChannel ch;
    private Selector sel;
    private volatile boolean closed;

    UnixTransport(String path) {
        this.path = path;
    }

    @Override
    public void connect() throws IOException {
        SocketChannel c;
        synchronized (this) {
            if (closed) throw new IOException("closed");
            c = SocketChannel.open(StandardProtocolFamily.UNIX);
            ch = c;
        }
        c.connect(UnixDomainSocketAddress.of(path));
        c.configureBlocking(false);
        Selector s = Selector.open();
        c.register(s, SelectionKey.OP_READ);
        synchronized (this) {
            sel = s;
            if (closed) {
                s.close();
                throw new IOException("closed");
            }
        }
    }

    @Override
    public int read(byte[] buf, int timeoutMs) throws IOException {
        ByteBuffer bb = ByteBuffer.wrap(buf);
        long end = System.currentTimeMillis() + timeoutMs;
        try {
            for (;;) {
                if (closed) throw new IOException("closed");
                int r = ch.read(bb);
                if (r != 0) return r;
                long left = end - System.currentTimeMillis();
                if (timeoutMs > 0 && left <= 0) throw new SocketTimeoutException("read timeout");
                sel.select(timeoutMs > 0 ? left : 0);
                sel.selectedKeys().clear();
            }
        } catch (ClosedSelectorException e) {
            throw new IOException("closed");
        }
    }

    @Override
    public void write(byte[] data) throws IOException {
        ByteBuffer bb = ByteBuffer.wrap(data);
        while (bb.hasRemaining()) {
            if (closed) throw new IOException("closed");
            if (ch.write(bb) == 0) {
                try {
                    Thread.sleep(1);
                } catch (InterruptedException e) {
                    throw new IOException("interrupted");
                }
            }
        }
    }

    @Override
    public void close() {
        SocketChannel c;
        Selector s;
        synchronized (this) {
            if (closed) return;
            closed = true;
            c = ch;
            s = sel;
        }
        if (s != null) {
            s.wakeup();
            try {
                s.close();
            } catch (IOException ignored) {
                // nothing to do
            }
        }
        if (c != null) {
            try {
                c.close();
            } catch (IOException ignored) {
                // nothing to do
            }
        }
    }
}
