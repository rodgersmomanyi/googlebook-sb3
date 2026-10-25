#!/system/bin/sh
# gb_gpu_ui_setup.sh: boot-time setup for the GbGpu app (INTERFACES 13.2, 15.4).
# Service gb_gpu_ui_setup (user shell, group shell, u:r:shell:s0), started at sys.boot_completed=1.
#   1. Every boot: pm grant gb.sb3.gpu android.permission.POST_NOTIFICATIONS (a failure is ignored).
#   2. Once ever: cmd statusbar add-tile gb.sb3.gpu/.GpuTileService, then create /data/vendor/gb/ui/tile_added
#      (0644 shell:shell). The marker is written only after add-tile succeeded; while it exists nothing is added
#      again, so a user who removed the tile keeps it removed. /data/vendor/gb is 0751 root:shell (shell cannot
#      create entries there, INTERFACES 5.1), so the marker lives in the shell-owned /data/vendor/gb/ui (rc).
# Does nothing unless /odm/etc/gb_hw is "native" and /odm/app/GbGpu/GbGpu.apk exists.
# Never needs root. Test hooks: GB_ROOT (prefix for /odm and /data), GB_TEST_PATH (prepended to PATH).
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin
export PATH
R=${GB_ROOT:-}
PKG=gb.sb3.gpu
TILE=$PKG/.GpuTileService
APK=$R/odm/app/GbGpu/GbGpu.apk
D=$R/data/vendor/gb/ui
M=$D/tile_added

log_i() {
  log -p i -t gb_gpu_ui_setup "$*" > /dev/null 2>&1 || true
}

hw=$(cat "$R/odm/etc/gb_hw" 2> /dev/null)
if [ "$hw" != native ]; then
  log_i "not a native boot ($hw): nothing to do"
  exit 0
fi
if [ ! -f "$APK" ]; then
  log_i "no $APK: nothing to do"
  exit 0
fi

# PackageManager can still be busy right after boot_completed: wait up to 60 s for the package.
n=0
while [ -z "$(pm path "$PKG" 2> /dev/null)" ]; do
  n=$((n + 1))
  if [ "$n" -ge 12 ]; then
    log_i "package $PKG is not installed"
    exit 1
  fi
  sleep 5
done

if pm grant "$PKG" android.permission.POST_NOTIFICATIONS > /dev/null 2>&1; then
  log_i "POST_NOTIFICATIONS granted"
else
  log_i "pm grant POST_NOTIFICATIONS failed (ignored)"
fi

[ -e "$M" ] && exit 0
if [ ! -d "$D" ]; then
  log_i "$D is missing: the tile is not added this boot"
  exit 0
fi
n=0
while :; do
  if cmd statusbar add-tile "$TILE" > /dev/null 2>&1; then
    if (umask 022 && : > "$M"); then
      log_i "Quick Settings tile added"
    else
      log_i "tile added, but $M could not be written"
    fi
    exit 0
  fi
  n=$((n + 1))
  [ "$n" -ge 3 ] && break
  sleep 10
done
log_i "cmd statusbar add-tile failed; trying again next boot"
exit 1
