"""Googlebook SB3 installer - UI entry point (/opt/gb/lib/gbinstall.py, run by /opt/gb/bin/gbinstall.sh).

  gbinstall.py --console <tty>              the text UI (started by busybox init, respawned)
  gbinstall.py --shutdown cleanup|reboot|poweroff
  gbinstall.py --selftest                   pure self-tests (no device, no process); prints GB-SELFTEST OK

Refuses to run (exit 3) outside the installer initramfs, except --selftest.
"""
import os
import sys

sys.dont_write_bytecode = True
LIB = os.path.dirname(os.path.abspath(__file__))
if LIB not in sys.path:
    sys.path.insert(0, LIB)


def inside_installer():
    try:
        if not os.path.exists("/etc/gb-installer-release"):
            return False
        if os.path.realpath("/proc/1/exe") != os.path.realpath("/bin/busybox"):
            return False
        with open("/proc/cmdline") as f:
            return "gb.build=" in f.read()
    except OSError:
        return False


def main(argv):
    if argv[1:] == ["--selftest"]:
        from gb import selftest
        return selftest.main()
    if not inside_installer():
        sys.stdout.write("gbinstall: not inside the Googlebook installer environment - refusing\n")
        sys.stdout.flush()
        return 3
    os.umask(0o022)
    if len(argv) == 3 and argv[1] == "--shutdown" and argv[2] in ("cleanup", "reboot", "poweroff"):
        from gb import ui, log, journal
        log.info("shutdown action: %s" % argv[2])
        if argv[2] == "cleanup":
            ui.cleanup()
            return 0
        if journal.flow_running():
            # Ctrl+Alt+Del during a destructive run: refused (a reboot between two writes can leave the disk with
            # different primary and backup partition tables)
            log.warn("shutdown action %s refused: a disk write is in progress" % argv[2])
            log.say("Googlebook installer: a disk write is in progress - wait for its RESULT before rebooting.")
            return 0
        ui.shutdown(argv[2])
        return 0
    if len(argv) == 3 and argv[1] == "--console" and argv[2] in ("tty1", "ttyS0"):
        from gb import ui, log
        try:
            ui.main(argv[2])
        except EOFError:
            log.warn("console closed; the UI exits (init respawns it)")
            import time
            time.sleep(1)
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception:
            import traceback
            log.error("UI crashed:\n" + traceback.format_exc())
            log.say("The installer UI stopped after an internal error (see the log); it restarts in 3 s.")
            import time
            time.sleep(3)
            return 1
        return 0
    sys.stdout.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
