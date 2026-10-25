package gb.sb3.gpu.core;

import java.io.Closeable;
import java.io.IOException;

/**
 * A byte stream to gb_power's control socket. The app uses {@code android.net.LocalSocket}
 * ({@code gb.sb3.gpu.LocalSocketTransport}); host tests use a {@code java.nio} UNIX-domain channel.
 */
public interface Transport extends Closeable {
    /** Creates unconnected transports; one per connection. */
    interface Factory {
        Transport create();
    }

    /** Connects; may block. {@link #close()} from another thread aborts it. */
    void connect() throws IOException;

    /**
     * Reads at most {@code buf.length} bytes. Returns -1 at end of stream. Waits at most {@code timeoutMs}
     * milliseconds (0 = no limit) and then throws {@link java.net.SocketTimeoutException}.
     */
    int read(byte[] buf, int timeoutMs) throws IOException;

    /** Writes all bytes. */
    void write(byte[] data) throws IOException;

    /** Idempotent; also wakes a thread blocked in {@link #connect()} or {@link #read}. Never throws. */
    @Override
    void close();
}
