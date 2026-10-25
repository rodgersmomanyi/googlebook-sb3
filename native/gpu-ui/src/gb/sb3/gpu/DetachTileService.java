package gb.sb3.gpu;

import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;
import gb.sb3.gpu.core.WatchSession;

/**
 * Optional "Detach screen" tile (INTERFACES 13.2): a tap sends `detach-request` (LATCH_REQUEST through gb_power),
 * exactly as if the detach button had been pressed; gb_power then decides and posts N07-N12. UNAVAILABLE when the
 * base is not attached or there is no DTX. Not an active tile: it listens only while Quick Settings is open.
 */
public final class DetachTileService extends TileService {
    private WatchSession watch;
    private boolean listening;
    private StateMapper.TileModel shown;

    private final WatchSession.Listener listener = new WatchSession.Listener() {
        @Override
        public void onStatus(final Status s) {
            Gb.publish(DetachTileService.this, s);
            Gb.main(new Runnable() {
                @Override
                public void run() {
                    render(s);
                }
            });
        }

        @Override
        public void onUnavailable(String why) {
            Gb.main(new Runnable() {
                @Override
                public void run() {
                    render(null);
                }
            });
        }
    };

    @Override
    public void onStartListening() {
        listening = true;
        render(Gb.lastStatus());
        if (watch == null) watch = new WatchSession(Gb.CLIENT, listener, Gb.watchOptions(0));
        watch.stop();
        watch.start();
    }

    @Override
    public void onStopListening() {
        listening = false;
        if (watch != null) watch.stop();
    }

    @Override
    public void onDestroy() {
        if (watch != null) watch.stop();
        super.onDestroy();
    }

    private void render(Status s) {
        StateMapper.TileModel m = StateMapper.detachTile(s);
        shown = m;
        if (!listening) return;
        Tile t = getQsTile();
        if (t == null) return;
        t.setLabel(Texts.DETACH_LABEL);
        t.setState(Gb.tileState(m.state));
        t.setSubtitle(m.subtitle);
        t.updateTile();
    }

    @Override
    public void onClick() {
        StateMapper.TileModel m = shown;
        if (m == null || m.tap != StateMapper.Tap.DETACH) return;
        Runnable action = new Runnable() {
            @Override
            public void run() {
                Gb.async("detach-request", null, DetachTileService.this, new Gb.Done() {
                    @Override
                    public void done(GbPowerClient.Outcome o) {
                        if (!o.ok()) Gb.toast(DetachTileService.this, Texts.error("detach-request", o));
                    }
                });
            }
        };
        if (isSecure() && isLocked()) unlockAndRun(action);
        else action.run();
    }
}
