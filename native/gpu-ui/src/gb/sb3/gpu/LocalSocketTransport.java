package gb.sb3.gpu;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import gb.sb3.gpu.core.Transport;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.SocketTimeoutException;

/** {@link Transport} over {@code android.net.LocalSocket} to a filesystem-namespace UNIX socket (INTERFACES 13.2). */
final class LocalSocketTransport implements Transport {
    private final String path;
    private final LocalSocket sock = new LocalSocket(LocalSocket.SOCKET_STREAM);
    private InputStream in;
    private OutputStream out;
    private int soTimeout = -1;
    private volatile boolean closed;

    LocalSocketTransport(String path) {
        this.path = path;
    }

    @Override
    public void connect() throws IOException {
        if (closed) throw new IOException("closed");
        sock.connect(new LocalSocketAddress(path, LocalSocketAddress.Namespace.FILESYSTEM));
        in = sock.getInputStream();
        out = sock.getOutputStream();
    }

    @Override
    public int read(byte[] buf, int timeoutMs) throws IOException {
        if (in == null) throw new IOException("not connected");
        if (timeoutMs != soTimeout) {
            sock.setSoTimeout(timeoutMs);
            soTimeout = timeoutMs;
        }
        try {
            return in.read(buf);
        } catch (SocketTimeoutException e) {
            throw e;
        } catch (IOException e) {
            // SO_RCVTIMEO expiry surfaces as an IOException carrying EAGAIN ("Try again").
            String m = e.getMessage();
            if (!closed && m != null && (m.contains("EAGAIN") || m.contains("Try again") || m.contains("timed out"))) {
                throw new SocketTimeoutException(m);
            }
            throw e;
        }
    }

    @Override
    public void write(byte[] data) throws IOException {
        if (out == null) throw new IOException("not connected");
        out.write(data);
        out.flush();
    }

    @Override
    public void close() {
        if (closed) return;
        closed = true;
        // shutdown() wakes a reader blocked in another thread; close() alone may not.
        try {
            sock.shutdownInput();
        } catch (IOException | RuntimeException ignored) {
            // not connected yet, or already shut down
        }
        try {
            sock.shutdownOutput();
        } catch (IOException | RuntimeException ignored) {
            // same
        }
        try {
            sock.close();
        } catch (IOException ignored) {
            // nothing left to do
        }
    }
}
