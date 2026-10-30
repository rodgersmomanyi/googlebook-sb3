#!/system/bin/sh
# gb_gpu_ui_setup.sh: boot-time setup for the GbGpu app (INTERFACES 13.2, 15.4).
# Service gb_gpu_ui_setup (user shell, group shell, u:r:shell:s0), started at sys.boot_completed=1.
# (Wave 6, notify) lapis runs in headless system user mode (ro.fw.mu.headless_system_user=true): user 0 is the headless
# "System User" (the login screen), the person uses a FULL user (10, ...). POST_NOTIFICATIONS and the Quick Settings
# tile list are per user, and at sys.boot_completed the current user is still 0. Measured on lapis 276.R4 (wave-6
# notify evidence): a plain `pm grant` grants user 0 only; `cmd statusbar add-tile` adds to the CURRENT user's tiles.
#   1. pm grant --user <u> gb.sb3.gpu android.permission.POST_NOTIFICATIONS for every FULL user (pm list users: flag
#      FULL 0x400; never the headless system user; a failure is ignored), (wave 7, headless-settings, notify O10) ONCE
#      per user ever: /data/vendor/gb/ui/granted.<u> (0644) is created after a successful grant and no grant is made
#      while it exists, so a person who turns GbGpu's notifications off keeps them off (before wave 7 every boot
#      granted again). Without the directory the grant is made and nothing is recorded (logged).
#   2. Then wait until a FULL user is in the foreground (am get-current-user; at most 180 checks 10 s apart = 30 min):
#      the person has signed in. Grant for that user if it has no marker yet (it may have been created during this
#      boot) and, once per user ever, cmd statusbar add-tile gb.sb3.gpu/.GpuTileService, then create
#      /data/vendor/gb/ui/tile_added.<u>
#      (0644 shell:shell). The marker is written only after add-tile succeeded; while it exists nothing is added again
#      for that user, so a user who removed the tile keeps it removed. /data/vendor/gb is 0751 root:shell (shell cannot
#      create entries there, INTERFACES 5.1), so the markers live in the shell-owned /data/vendor/gb/ui (rc). The
#      pre-wave-6 marker tile_added (it recorded an add to user 0's tiles) is ignored and kept.
#   (wave 7) The markers granted.<u> and tile_added.<u> of a user id that pm list users no longer lists are removed
#   (only when the list names user 0), so a later user that gets a reused id is granted and gets the tile.
# Does nothing unless /odm/etc/gb_hw is "native" and /odm/app/GbGpu/GbGpu.apk exists.
# Never needs root. Test hooks (never set by init): GB_ROOT (prefix for /odm and /data), GB_TEST_PATH (prepended to
# PATH), GB_UI_WAIT_CHECKS (the number of foreground checks, default 180).
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin
export PATH
R=${GB_ROOT:-}
PKG=gb.sb3.gpu
TILE=$PKG/.GpuTileService
APK=$R/odm/app/GbGpu/GbGpu.apk
D=$R/data/vendor/gb/ui
WAIT=${GB_UI_WAIT_CHECKS:-180}
case $WAIT in '' | *[!0-9]*) WAIT=180 ;; esac
set -f

log_i() {
  log -p i -t gb_gpu_ui_setup "$*" > /dev/null 2>&1 || true
}

full_users() { # -> USERS: ids of the FULL users (pm list users, running or not), ULIST: the raw list; 1 = no list
  USERS=
  ULIST=
  _ul=$(timeout 20 pm list users 2> /dev/null) || return 1
  ULIST=$_ul
  _nu=0
  _ifs=$IFS
  IFS='
'
  for _l in $_ul; do # "<tab>UserInfo{<id>:<name>:<hex flags>}[ running]" (the name may hold ':')
    _l=${_l%' running'}
    case $_l in *'UserInfo{'*'}') ;; *) continue ;; esac
    _l=${_l%'}'}
    _f=${_l##*:}
    _i=${_l#*'UserInfo{'}
    _i=${_i%%:*}
    case $_i in '' | *[!0-9]*) continue ;; esac
    case $_f in '' | *[!0-9a-fA-F]*) continue ;; esac
    if [ ${#_i} -gt 4 ] || [ ${#_f} -gt 7 ]; then continue; fi
    [ $((0x$_f & 0x400)) -ne 0 ] || continue # FLAG_FULL: never the headless system user (811), never a profile
    USERS="$USERS $_i"
    _nu=$((_nu + 1))
    [ $_nu -lt 16 ] || break
  done
  IFS=$_ifs
  return 0
}

grant() { # $1 = user id: once per user ever (marker granted.<u>, written after a successful grant)
  [ -e "$D/granted.$1" ] && return 0
  if pm grant --user "$1" "$PKG" android.permission.POST_NOTIFICATIONS > /dev/null 2>&1; then
    if [ ! -d "$D" ]; then
      log_i "POST_NOTIFICATIONS granted for user $1 ($D is missing: not recorded)"
    elif (umask 022 && : > "$D/granted.$1"); then
      log_i "POST_NOTIFICATIONS granted for user $1 (once)"
    else
      log_i "POST_NOTIFICATIONS granted for user $1, but $D/granted.$1 could not be written"
    fi
  else
    log_i "pm grant POST_NOTIFICATIONS failed for user $1 (ignored)"
  fi
}

prune() { # markers of user ids that ULIST no longer lists (only when it names user 0)
  case $ULIST in *'UserInfo{0:'*) ;; *) return 0 ;; esac
  set +f
  for _m in "$D"/granted.* "$D"/tile_added.*; do
    [ -e "$_m" ] || continue
    _x=${_m##*.}
    case $_x in '' | *[!0-9]*) continue ;; esac
    case $ULIST in *"UserInfo{$_x:"*) ;; *) rm -f "$_m" && log_i "user $_x no longer exists: ${_m##*/} removed" ;; esac
  done
  set -f
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

if full_users; then
  [ -n "$USERS" ] || log_i "no full user yet"
  prune
  for u in $USERS; do grant "$u"; done
else
  log_i "pm list users failed: no grant before the sign-in"
fi

# Wait for the person: a FULL user in the foreground (the headless system user 0 shows the login screen). The user list
# is read again only when the foreground user changes to one it does not hold (a user created during this boot).
cur=
prev=
n=0
while :; do
  c=$(timeout 20 am get-current-user 2> /dev/null)
  case $c in '' | *[!0-9]*) c= ;; esac
  if [ -n "$c" ]; then
    case " $USERS " in
      *" $c "*) cur=$c ;;
      *) if [ "$c" != "$prev" ] && full_users; then case " $USERS " in *" $c "*) cur=$c ;; esac; fi ;;
    esac
    prev=$c
  fi
  [ -n "$cur" ] && break
  n=$((n + 1))
  if [ "$n" -ge "$WAIT" ]; then
    log_i "no full user in the foreground after $WAIT checks: the tile is added at a later boot"
    exit 0
  fi
  sleep 10
done
grant "$cur"

M=$D/tile_added.$cur
[ -e "$M" ] && exit 0
if [ ! -d "$D" ]; then
  log_i "$D is missing: the tile is not added this boot"
  exit 0
fi
n=0
while :; do
  if [ "$(timeout 20 am get-current-user 2> /dev/null)" != "$cur" ]; then
    log_i "user $cur left the foreground: the tile is added at a later boot"
    exit 0
  fi
  if cmd statusbar add-tile "$TILE" > /dev/null 2>&1; then
    if (umask 022 && : > "$M"); then
      log_i "Quick Settings tile added for user $cur"
    else
      log_i "tile added for user $cur, but $M could not be written"
    fi
    exit 0
  fi
  n=$((n + 1))
  [ "$n" -ge 3 ] && break
  sleep 10
done
log_i "cmd statusbar add-tile failed for user $cur; trying again next boot"
exit 1
