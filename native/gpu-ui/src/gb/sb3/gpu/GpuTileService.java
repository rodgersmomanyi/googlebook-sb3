package gb.sb3.gpu;

import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

import gb.sb3.gpu.core.GbPowerClient;
import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;
import gb.sb3.gpu.core.WatchSession;

/**
 * The "Graphics" Quick Settings tile (INTERFACES 27.1, 13.2; same component name as v1.1, so a tile the user added stays
 * added). It is an active, toggleable tile: SystemUI binds it when we ask ({@link Gb#requestTileUpdate}, after gb_power's
 * state broadcast), when it is tapped, and once at start. While listening it keeps a gb_power `watch` open, capped at 5
 * minutes per listening period. A tap cycles the graphics mode Auto -> Intel -> NVIDIA -> Auto with `set-mode` (never a
 * dialog); a long-press opens the Graphics page (the QS_TILE_PREFERENCES filter on SettingsActivity).
 */
public final class GpuTileService extends TileService {
    private static final long WATCH_MAX_LIFE_MS = 5L * 60L * 1000L;

    private WatchSession watch;
    private boolean listening;
    private Status shownStatus;
    private StateMapper.TileModel shown;

    private final WatchSession.Listener listener = new WatchSession.Listener() {
        @Override
        public void onStatus(final Status s) {
            Gb.publish(GpuTileService.this, s);
            Gb.main(new Runnable() {
                @Override
                public void run() {
                    render(s);
                }
            });
        }

        @Override
        public void onUnavailable(String why) {
            Gb.publish(GpuTileService.this, null);
            Gb.main(new Runnable() {
                @Override
                public void run() {
                    render(null);
                }
            });
        }
    };

    @Override
    public void onTileAdded() {
        refresh();
    }

    @Override
    public void onStartListening() {
        listening = true;
        Status cached = Gb.lastStatus();
        if (cached != null) render(cached);
        if (watch == null) watch = new WatchSession(Gb.CLIENT, listener, Gb.watchOptions(WATCH_MAX_LIFE_MS));
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

    private void refresh() {
        Gb.async("status", null, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                render(o.status());
            }
        });
    }

    private void render(Status s) {
        StateMapper.TileModel m = StateMapper.graphicsTile(s);
        shownStatus = s;
        shown = m;
        if (!listening) return;
        Tile t = getQsTile();
        if (t == null) return;
        t.setLabel(Texts.TILE_LABEL);
        t.setState(Gb.tileState(m.state));
        t.setSubtitle(m.subtitle);
        t.updateTile();
    }

    @Override
    public void onClick() {
        final StateMapper.TileModel m = shown;
        final Status s = shownStatus;
        Runnable action = new Runnable() {
            @Override
            public void run() {
                if (m == null || s == null) {
                    // Nothing rendered by this process yet: decide on a fresh status.
                    Gb.async("status", null, GpuTileService.this, new Gb.Done() {
                        @Override
                        public void done(GbPowerClient.Outcome o) {
                            Status f = o.status();
                            render(f);
                            if (StateMapper.graphicsTile(f).tap == StateMapper.Tap.SET_MODE) cycle(f);
                        }
                    });
                } else if (m.tap == StateMapper.Tap.SET_MODE) {
                    cycle(s);
                } else {
                    refresh(); // rows 1-2 (UNAVAILABLE): SystemUI normally does not deliver the tap at all
                }
            }
        };
        if (isSecure() && isLocked()) unlockAndRun(action);
        else action.run();
    }

    /** 27.1: tap = `set-mode <next>` from the mode the status reports; toasts for next-boot/next-attach/pending. */
    private void cycle(Status s) {
        final String next = StateMapper.nextMode(s.gfxMode);
        Gb.async("set-mode", next, this, new Gb.Done() {
            @Override
            public void done(GbPowerClient.Outcome o) {
                Gb.reportMode(GpuTileService.this, "set-mode", o);
                refresh();
                SwitchProvider.notifyChanged(GpuTileService.this);
            }
        });
    }
}
