package gb.sb3.gpu.core;

/**
 * Every user-visible string the app derives from gb_power data. The ones marked "exact" are normative
 * (INTERFACES 12.3, 13.2, 27; v1.1 8.3 for the legacy mapping) and are asserted byte for byte by the host tests.
 */
public final class Texts {
    private Texts() {
    }

    // ---- tile labels (exact, 13.2 v1.2)
    public static final String TILE_LABEL = "Graphics";
    public static final String DETACH_LABEL = "Detach screen";
    public static final String SETTINGS_TITLE = "Graphics";

    // ---- Graphics tile subtitles (exact, 27.1)
    public static final String UNAVAILABLE = "Unavailable";
    public static final String PREPARING_TO_DETACH = "Preparing to detach";
    public static final String OFF_INTEL_MODE = "Off (Intel mode)";
    public static final String RESTART_REQUIRED = "Restart required";
    public static final String FAULT = "Fault";
    public static final String DISABLED_FOR_THIS_BOOT = "Disabled for this boot";
    public static final String NVIDIA = "NVIDIA";
    public static final String NVIDIA_MANUAL = "NVIDIA (manual)";
    public static final String INTEL = "Intel";
    // row: auto subtitle, nvidia-mode subtitle
    public static final String INTEL_RECOVERED = "Intel (recovered)";
    public static final String NV_RECOVERED = "NVIDIA unavailable - recovered";
    public static final String INTEL_TEST_BOOT = "Intel (test boot)";
    public static final String UNAVAILABLE_DETACHED = "Unavailable (base detached)";
    public static final String NV_DETACHED = "NVIDIA unavailable - base detached";
    public static final String INTEL_STARTING = "Intel (starting NVIDIA)";
    public static final String NV_STARTING = "NVIDIA unavailable - starting";
    public static final String INTEL_TESTING = "Intel (testing NVIDIA)";
    public static final String NV_TESTING = "NVIDIA unavailable - testing";
    public static final String INTEL_TEST_NOT_RUN = "Intel (NVIDIA test not run)";
    public static final String NV_TEST_NOT_RUN = "NVIDIA unavailable - test not run";
    public static final String INTEL_TEST_FAILED = "Intel (NVIDIA test failed)";
    public static final String NV_TEST_FAILED = "NVIDIA unavailable - test failed";
    public static final String NV_NOT_READY = "NVIDIA unavailable - not ready";

    public static String offIntelBusy(int n) {
        return "Off (Intel mode, " + n + " on NVIDIA)";
    }

    public static String nvidiaBusy(int n) {
        return "NVIDIA (busy " + n + ")";
    }

    public static String nvidiaManualBusy(int n) {
        return "NVIDIA (manual, busy " + n + ")";
    }

    // ---- toasts for set-mode effects (exact, 27.1); "now" has none
    public static final String TAKES_EFFECT_AFTER_RESTART = "Takes effect after restart";
    public static final String TAKES_EFFECT_WHEN_ATTACHED = "Takes effect when the keyboard base is attached";
    public static final String SWITCHING_TO_INTEL_WHEN_CLOSED = "Switching to Intel when apps on NVIDIA close";

    // ---- mode labels (exact, 27.2: radios and the injected summary)
    public static final String MODE_AUTO = "Auto";
    public static final String MODE_INTEL = "Intel only";
    public static final String MODE_NVIDIA = "NVIDIA";
    public static final String RADIO_AUTO = "Auto - recommended";
    public static final String RADIO_AUTO_SUB = "NVIDIA for apps while the keyboard base is attached, Intel in tablet mode";
    public static final String RADIO_INTEL = "Intel only (battery)";
    public static final String RADIO_INTEL_SUB = "The NVIDIA GPU stays off";
    public static final String RADIO_NVIDIA = "NVIDIA";
    public static final String RADIO_NVIDIA_SUB = "Use the NVIDIA GPU for apps whenever the keyboard base is attached";

    // ---- Graphics page (exact, 27.2)
    public static final String STATUS_PREFIX = "Apps you open now use: ";
    public static final String GPU_PREFIX = "NVIDIA GPU: ";
    public static final String GPU_ASLEEP = "asleep (D3cold)";
    public static final String GPU_ACTIVE = "active";
    public static final String GPU_NO_DRIVER = "no driver";
    public static final String GPU_NOT_CONNECTED = "not connected";
    public static final String SELFTEST_PREFIX = "Self-test: ";
    public static final String SELFTEST_NOT_RUN = "Not run yet";
    public static final String SELFTEST_RUNNING = "Running...";
    public static final String SELFTEST_RERUN = "Run self-test again";
    public static final String ALL_APPS = "Use NVIDIA for all apps";
    public static final String ALL_APPS_OFF = "Games and the apps marked Use NVIDIA run on NVIDIA";
    public static final String ALL_APPS_ON = "Every app you installed runs on NVIDIA, except the ones marked Always Intel";
    public static final String APPS_HEADING = "Apps";
    public static final String APPS_HINT = "Changes apply the next time an app starts. Open apps keep their graphics until "
            + "you restart them.";
    public static final String USE_NVIDIA = "Use NVIDIA";
    public static final String ALWAYS_INTEL = "Always Intel";
    public static final String GAME_BADGE = "Game";
    public static final String HOLDER_RESTART = "Using NVIDIA now - restart the app to switch";
    public static final String HOLDER_NOW = "Using NVIDIA now";
    public static final String CLOSE_APPS = "Close apps";
    public static final String COMPUTE_HEADING = "NVIDIA compute service";
    public static final String HELP_HEADING = "How graphics switching works";
    public static final String HELP_TEXT = "The screen, the system UI and the launcher always use Intel graphics. In Auto, "
            + "apps you open use NVIDIA graphics while the keyboard base is attached, and Intel graphics in tablet mode. "
            + "Apps that are already open keep their graphics until you restart them. Games use NVIDIA by default; mark "
            + "other apps with Use NVIDIA, or turn on Use NVIDIA for all apps. Always Intel keeps an app on Intel "
            + "graphics.";

    public static String selfTestPassed(String date) {
        return "Passed on " + date;
    }

    /** {@code Failed on <date>: <reason>}; the reason is left out when gb_power does not report one (UI-1B-Q1). */
    public static String selfTestFailed(String date, String reason) {
        return "Failed on " + date + (reason == null || reason.isEmpty() ? "" : ": " + reason);
    }

    public static String selfTestCouldNotRun(String reason) {
        return "Could not run" + (reason == null || reason.isEmpty() ? "" : ": " + reason);
    }

    public static String pendingLine(String labels) {
        return labels + " still use the NVIDIA GPU.";
    }

    public static String usedBy(int n, String labels) {
        return ", used by " + n + " app(s): " + labels;
    }

    // ---- reason texts (exact, 27.6)
    public static String reasonText(String reason) {
        if (reason == null) return INTEL;
        switch (reason) {
            case "ok": return "NVIDIA";
            case "nopkg": return "Intel (no NVIDIA app graphics on this system)";
            case "nodtx": return "Intel (no keyboard base support)";
            case "kill": return "Intel (NVIDIA disabled for this boot)";
            case "test": return "Intel (NVIDIA test boot)";
            case "crashloop": return "Intel (NVIDIA switched off after a problem)";
            case "wedged": return "Intel (restart required)";
            case "fault": return "Intel (NVIDIA GPU fault)";
            case "mode": return "Intel (Intel only mode)";
            case "detached": return "Intel (keyboard base detached)";
            case "detaching": return "Intel (preparing to detach)";
            case "loading": return "Intel (starting NVIDIA)";
            case "nodriver": return "Intel (NVIDIA driver not running)";
            case "untested": return "Intel (testing NVIDIA)";
            case "testerror": return "Intel (NVIDIA test could not run)";
            case "selftest": return "Intel (NVIDIA self-test failed)";
            default: return INTEL;
        }
    }

    // ---- app notifications (exact, 12.3 v1.2)
    public static final String CH_STATUS_ID = "gb_gpu_status";
    public static final String CH_STATUS_NAME = "NVIDIA GPU status";
    public static final String CH_ALERTS_ID = "gb_gpu_alerts";
    public static final String CH_ALERTS_NAME = "NVIDIA GPU alerts";
    public static final String CH_DETACH_ID = "gb_gpu_detach";
    public static final String CH_DETACH_NAME = "Detach";
    public static final int NOTIF_IN_USE = 1;
    public static final int NOTIF_ALERT = 2;
    public static final int NOTIF_DETACH = 3;
    public static final String IN_USE_TITLE = "NVIDIA GPU in use";
    public static final String DETACH_TITLE = "Can't detach yet";
    public static final String ACTION_CLOSE_APPS = "Close apps";
    public static final String ACTION_CLOSE_AND_DETACH = "Close apps and detach";
    public static final String ACTION_CANCEL = "Cancel";

    public static String inUseText(String labels) {
        return "NVIDIA in use by: " + labels;
    }

    public static String detachText(String labels) {
        return labels + " are using the NVIDIA GPU. Close them to detach the screen.";
    }

    /** v1.1 text (12.3 row 1), still used when the holder list is empty (rate-limited scan). */
    public static String apps(int n) {
        return n + " app(s)";
    }

    // ---- HoldersActivity (exact, 27.4)
    public static final String CLOSE_TITLE = "Close apps using the NVIDIA GPU?";
    public static final String CLOSE_TEXT = "These apps will be closed. Unsaved work in them may be lost:";
    public static final String INTEL_TITLE = "Close apps to switch to Intel?";
    public static final String INTEL_TEXT = "These apps still use the NVIDIA GPU. They will be closed. Unsaved work in "
            + "them may be lost:";
    public static final String DETACH_DIALOG_TITLE = "Close apps and detach?";
    public static final String DETACH_DIALOG_TEXT = "These apps are using the NVIDIA GPU. They will be closed so the "
            + "screen can be detached. Unsaved work in them may be lost:";
    public static final String HOLDERS_OK = "Close apps";
    public static final String HOLDERS_CANCEL = "Cancel";
    public static final String NOT_CLOSABLE = "Cannot be closed here:";
    public static final String PREPARING_DETACH_DOTS = "Preparing to detach...";
    public static final String SOME_NOT_CLOSED = "Some apps could not be closed";

    // ---- component-defined texts (not fixed by the spec)
    public static final String DETACH_IN_PROGRESS = "In progress";
    public static final String DETACH_READY = "Ready to detach";
    public static final String COMPUTE_SERVICE = "NVIDIA compute service";
    public static final String NO_HOLDERS = "No apps are using the NVIDIA GPU now.";
    public static final String ALERT_CLOSE_TITLE = "Some apps were not closed";
    public static final String NOT_AVAILABLE = "NVIDIA GPU control is not available";
    public static final String REPORT_REQUESTED = "Diagnostic report requested";
    public static final String SELFTEST_STARTED = "Self-test started";

    // ---- legacy v1.1 "NVIDIA GPU" tile texts (8.3; only StateMapper.gpuTile() and its host tests use them)
    public static final String LEGACY_TILE_LABEL = "NVIDIA GPU";
    public static final String OFF = "Off";
    public static final String ON_AT_NEXT_RESTART = "On at next restart";
    public static final String OFF_RECOVERED = "Off (recovered)";
    public static final String STARTING = "Starting";
    public static final String STANDBY = "Standby";

    public static String busy(int n) {
        return "Busy (" + n + ")";
    }

    /** A short, plain explanation of a failed command for a toast, a row or the alert notification. */
    public static String error(String cmd, GbPowerClient.Outcome o) {
        if (o == null || o.failure != GbPowerClient.Failure.NONE || o.reply == null) return NOT_AVAILABLE;
        String e = o.reply.err;
        if ("EPERM".equals(e)) return "Not allowed";
        if ("ENODEV".equals(e)) {
            if ("enable".equals(cmd) || "set-mode".equals(cmd)) return "This system has no NVIDIA driver";
            if ("set-app-policy".equals(cmd) || "set-app-intel".equals(cmd) || "set-app-nvidia".equals(cmd)) {
                return "No NVIDIA app graphics on this system";
            }
            if ("run-selftest".equals(cmd)) return "The NVIDIA GPU is not ready for a self-test";
            if ("detach-request".equals(cmd) || "close-holders-and-detach".equals(cmd)) {
                return "Detach control is not available";
            }
            if ("holders".equals(cmd) || "list-holders".equals(cmd)) return NO_HOLDERS;
            if ("compute-start".equals(cmd)) return "The NVIDIA GPU is not ready";
            return "No NVIDIA GPU";
        }
        if ("EBUSY".equals(e)) {
            if ("detach-request".equals(cmd) || "close-holders-and-detach".equals(cmd)) {
                return "A detach is already being prepared";
            }
            if ("enable".equals(cmd)) return RESTART_REQUIRED;
            if ("run-selftest".equals(cmd)) {
                return o.reply.msg.isEmpty() ? "The self-test cannot run now" : "The self-test cannot run now ("
                        + o.reply.msg + ")";
            }
            if ("set-app-intel".equals(cmd) || "set-app-nvidia".equals(cmd)) return "Too many app choices (128)";
            return "Busy, try again later";
        }
        if ("EAGAIN".equals(e)) {
            if ("run-selftest".equals(cmd)) return "The NVIDIA driver is starting, try again in a moment";
            return "Busy, try again in a moment";
        }
        if ("ENOENT".equals(e)) return "compute-deny".equals(cmd) ? "Not in the list" : "App not installed";
        if ("EINVAL".equals(e)) {
            if ("compute-allow".equals(cmd)) return "Not a valid package name";
            if ("apps-add".equals(cmd) || "apps-begin".equals(cmd) || "apps-commit".equals(cmd)) {
                return "The app list was refused";
            }
            return "Invalid request";
        }
        if ("ESTALE".equals(e)) return "None of these apps could be closed";
        if ("EIO".equals(e)) return "The setting could not be saved";
        return "Failed (" + e + ")";
    }
}
