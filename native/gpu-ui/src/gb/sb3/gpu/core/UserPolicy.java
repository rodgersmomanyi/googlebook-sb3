package gb.sb3.gpu.core;

/**
 * (Wave 6, notify) Which instance of the app acts on gb_power's state. lapis runs in headless system user mode: user 0
 * is the headless "System User" (the login screen) and the person uses a full user (10, ...). An app instance in the
 * headless system user must not act: nobody sees its notifications, tile or Settings summary, its {@code ui-caps notify}
 * would make gb_power drop N08 in favour of a detach notification nobody sees, and its app catalog would describe a
 * user nobody uses. Every other instance (a full user, or user 0 of a device that is not in headless mode) acts.
 */
public final class UserPolicy {
    private UserPolicy() {
    }

    /**
     * @param headlessMode {@code UserManager.isHeadlessSystemUserMode()}
     * @param systemUser   {@code UserManager.isSystemUser()} of the calling user
     */
    public static boolean actsOnState(boolean headlessMode, boolean systemUser) {
        return !(headlessMode && systemUser);
    }
}
