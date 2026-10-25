package gb.sb3.gpu.core;

import java.util.List;

/**
 * What GbGpu's own notifications show (INTERFACES 12.3 v1.2, 27.3), as pure functions of the status:
 * <ul>
 * <li>id 1 (channel {@code gb_gpu_status}): ongoing while {@code state} is {@code busy:N}: {@code NVIDIA in use by:
 * <labels>} with the action <b>Close apps</b>;</li>
 * <li>id 3 (channel {@code gb_gpu_detach}): {@code Can't detach yet} when {@code detach_ref} grows beyond the value the
 * app saw last, with <b>Close apps and detach</b> and <b>Cancel</b>; cancelled when the base is not attached or the
 * dGPU is no longer busy;</li>
 * <li>{@code ui-caps notify|none} on every handled broadcast.</li>
 * </ul>
 */
public final class NotifModel {
    private NotifModel() {
    }

    /** Title and text of the in-use notification, or null when it must be cancelled. */
    public static String[] inUse(Status s, Labels.Labeler l) {
        int n = s == null ? 0 : StateMapper.busyCount(s.state);
        if (n == 0) return null;
        return new String[] {Texts.IN_USE_TITLE, Texts.inUseText(Labels.holders(s.holders, l, n))};
    }

    /** The detach notification decision for one status. */
    public static final class Detach {
        /** Post (or replace) notification id 3 now, after fetching `list-holders`. */
        public final boolean post;
        /** Cancel notification id 3 (base gone or no longer busy). */
        public final boolean cancel;
        /** The value to store as "seen" (SharedPreferences). */
        public final int seen;

        Detach(boolean post, boolean cancel, int seen) {
            this.post = post;
            this.cancel = cancel;
            this.seen = seen;
        }

        @Override
        public String toString() {
            return (post ? "post" : cancel ? "cancel" : "keep") + " seen=" + seen;
        }
    }

    /**
     * @param s        the status (null: unknown, nothing changes)
     * @param seen     the {@code detach_ref} the app saw last
     * @param sameBoot false when {@code seen} was stored in an earlier boot ({@code detach_ref} is a per-boot counter)
     */
    public static Detach detach(Status s, int seen, boolean sameBoot) {
        if (s == null) return new Detach(false, false, seen);
        int base = sameBoot ? seen : 0;
        // detach_ref went backwards: gb_power restarted in this boot, so its counter started again at 0
        if (s.detachRef < base) base = 0;
        boolean busy = StateMapper.busyCount(s.state) > 0;
        boolean attached = "attached".equals(s.base) || "not-feasible".equals(s.base);
        if (!attached || !busy) return new Detach(false, true, s.detachRef);
        return new Detach(s.detachRef > base, false, s.detachRef);
    }

    /** Title and text of the detach notification for the holder names from `list-holders`. */
    public static String[] detachText(List<Holder> holders, Labels.Labeler l, int busyN) {
        return new String[] {Texts.DETACH_TITLE, Texts.detachText(Labels.holders(holders, l, busyN))};
    }

    /**
     * 27.3: {@code ui-caps notify} while notifications are enabled for the app and the {@code gb_gpu_detach} channel is
     * not blocked (importance NONE); otherwise {@code none}, so that gb_power posts N08 itself.
     */
    public static String uiCaps(boolean notificationsEnabled, boolean detachChannelBlocked) {
        return notificationsEnabled && !detachChannelBlocked ? "notify" : "none";
    }
}
