#!/system/bin/sh
# gb_notify.sh -- delivers the dGPU notification queue (INTERFACES.md v1.2 section 12.1, D6). Service gb_notify,
# user shell (the identity "adb shell cmd notification post" uses), started by init on any vendor.gb.notify change.
#
# Producers (gb_nouveau.sh, gb_dgpu_report.sh, gb_power, and (wave 3) gb_slotsync) never run cmd/am themselves: they write
# /data/vendor/gb/notify/<epoch_ms 13 digits>-<pid>-<n>.msg (as .tmp, then renamed) and set vendor.gb.notify.
# Message: key=value lines; kind=notify (tag, title, text) | kind=broadcast | kind=force-stop (pkg, user) |
# (v1.2) kind=settings (key + value, or key + src; INTERFACES 12.1, 24.6).
# Every file is validated (fixed tag set, printable ASCII, length limits, package-name regex), delivered once,
# and deleted after the attempt whatever the outcome. Failures are logged and dropped.
# Tags: gb_dgpu, gb_dtx, gb_dgpu_test, (v1.2) gb_gfx, (wave 3, INTERFACES 12.1/12.2 N22-N23) gb_update: the
# "System update held" notice of gb_slotsync's kernel check (an OTA whose kernel KMI differs from the running one).
#
# Settings (v1.2): exactly three Settings.Global keys can ever be written through the queue, each with its one
# allowed form; anything else is logged and dropped:
#   key=updatable_driver_all_apps                + value=0 | value=3
#   key=updatable_driver_production_opt_in_apps  + src=gfx_optin   (<= 512 lines)
#   key=updatable_driver_production_opt_out_apps + src=gfx_optout  (<= 128 lines)
# A src file is /data/vendor/gb/<src>, written by gb_power (root) only: it is used only when it is a regular file
# (not a symlink) owned by uid 0, <= 64 KiB and within the line limit. Lines that are not package names are dropped
# (logged), duplicates removed, the rest joined with "," in file order; an empty result deletes the key.
#
# Test hooks: GB_ROOT (path prefix) and GB_TEST_PATH (prepended to PATH), as in gb_nouveau.sh.

LC_ALL=C
export LC_ALL
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin
export PATH
R=${GB_ROOT:-}
set -f
umask 007

GB=$R/data/vendor/gb
Q=$GB/notify
DONE=,
LIST=

logn() {                                 # /dev/kmsg is 0600 root:root on lapis: fall back to logcat for shell
  if ! { printf 'gb_notify: %s\n' "$*" >> "$R/dev/kmsg"; } 2>/dev/null; then
    log -t gb_notify -p w "$*" 2>/dev/null
  fi
  return 0
}

printable() {                            # non-empty, printable ASCII only, at most $2 bytes
  [ -n "$1" ] || return 1
  [ ${#1} -le "$2" ] || return 1
  [ -z "$(printf '%s' "$1" | tr -d ' -~')" ]
}

valid_pkg() {                            # ^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$ and <= 127 (INTERFACES 9.3),
  [ ${#1} -le 127 ] || return 1          # without a fork per call (settings lists have up to 512 lines)
  case $1 in [A-Za-z]*.*) ;; *) return 1 ;; esac
  case $1 in *[!A-Za-z0-9_.]* | *..* | *.) return 1 ;; esac
  return 0
}

settings_list() {                        # $1 = src file name, $2 = max lines -> LIST; 1 = do not write at all
  LIST=
  _sf=$GB/$1
  if [ ! -f "$_sf" ] || [ -L "$_sf" ]; then logn "settings: $1 is not a regular file"; return 1; fi
  _own=$(stat -c %u "$_sf" 2>/dev/null)
  [ "$_own" = 0 ] || { logn "settings: $1 is not owned by root (uid '$(printf '%s' "$_own" | tr -cd '0-9')')"; return 1; }
  _ssz=$(($(wc -c < "$_sf" 2>/dev/null) + 0))
  [ "$_ssz" -le 65536 ] || { logn "settings: $1 is larger than 64 KiB"; return 1; }
  _ln=0
  _seen=,
  while IFS= read -r _p || [ -n "$_p" ]; do
    _ln=$((_ln + 1))
    if [ $_ln -gt "$2" ]; then
      logn "settings: $1 has more than $2 lines"
      LIST=
      return 1
    fi
    [ -n "$_p" ] || continue
    if ! valid_pkg "$_p"; then
      logn "settings: $1 line $_ln dropped (not a package name)"
      continue
    fi
    case $_seen in *,"$_p",*) continue ;; esac
    _seen="$_seen$_p,"
    LIST=${LIST:+$LIST,}$_p
  done < "$_sf"
  return 0
}

do_settings() {                          # $1 = message file name (for the log); key/value/src from handle()
  case $key in
    updatable_driver_all_apps)
      if [ "$has_value" != 1 ] || [ "$has_src" = 1 ]; then logn "dropped $1: bad settings pairing"; return 0; fi
      case $value in 0 | 3) ;; *) logn "dropped $1: bad value for $key"; return 0 ;; esac
      if timeout 20 cmd settings put global "$key" "$value" > /dev/null 2>&1; then
        logn "settings: $key=$value"
      else
        logn "cmd settings put failed for $1"
      fi
      return 0
      ;;
    updatable_driver_production_opt_in_apps) _want=gfx_optin _max=512 ;;
    updatable_driver_production_opt_out_apps) _want=gfx_optout _max=128 ;;
    *) logn "dropped $1: settings key not allowed"; return 0 ;;
  esac
  if [ "$has_value" = 1 ] || [ "$has_src" != 1 ] || [ "$src" != "$_want" ]; then
    logn "dropped $1: bad settings pairing"
    return 0
  fi
  settings_list "$_want" "$_max" || { logn "dropped $1: $_want not used"; return 0; }
  if [ -n "$LIST" ]; then
    if ! timeout 20 cmd settings put global "$key" "$LIST" > /dev/null 2>&1; then
      logn "cmd settings put failed for $1"
      return 0
    fi
    _np=0
    _ifs=$IFS
    IFS=,
    for _p in $LIST; do _np=$((_np + 1)); done
    IFS=$_ifs
    logn "settings: $key = $_np packages"
  else
    if ! timeout 20 cmd settings delete global "$key" > /dev/null 2>&1; then
      logn "cmd settings delete failed for $1"
      return 0
    fi
    logn "settings: $key deleted (empty list)"
  fi
  return 0
}

handle() {                               # $1 = message file
  _sz=$(($(wc -c < "$1" 2>/dev/null) + 0))
  if [ "$_sz" -gt 4096 ]; then logn "dropped ${1##*/}: too large"; return 0; fi
  kind=
  tag=
  title=
  text=
  pkg=
  user=
  key=
  value=
  src=
  has_value=0
  has_src=0
  while IFS= read -r _l || [ -n "$_l" ]; do
    _v=${_l#*=}
    [ ${#_v} -le 240 ] || continue
    case $_l in
      kind=*) kind=$_v ;;
      tag=*) tag=$_v ;;
      title=*) title=$_v ;;
      text=*) text=$_v ;;
      pkg=*) pkg=$_v ;;
      user=*) user=$_v ;;
      key=*) key=$_v ;;
      value=*) value=$_v; has_value=1 ;;
      src=*) src=$_v; has_src=1 ;;
    esac
  done < "$1"
  case $kind in
    notify)
      case $tag in gb_dgpu | gb_dtx | gb_dgpu_test | gb_gfx | gb_update) ;; *) logn "dropped ${1##*/}: bad tag"; return 0 ;; esac
      printable "$title" 40 || { logn "dropped ${1##*/}: bad title"; return 0; }
      printable "$text" 200 || { logn "dropped ${1##*/}: bad text"; return 0; }
      timeout 20 cmd notification post -t "$title" "$tag" "$text" > /dev/null 2>&1 ||
        logn "cmd notification post failed for ${1##*/}"
      ;;
    broadcast)
      timeout 20 am broadcast --user 0 --include-stopped-packages -a gb.sb3.gpu.action.STATE_CHANGED \
        -p gb.sb3.gpu > /dev/null 2>&1 || logn "am broadcast failed for ${1##*/}"
      ;;
    force-stop)
      valid_pkg "$pkg" || { logn "dropped ${1##*/}: bad package name"; return 0; }
      case $user in '' | *[!0-9]*) logn "dropped ${1##*/}: bad user"; return 0 ;; esac
      [ ${#user} -le 4 ] || { logn "dropped ${1##*/}: bad user"; return 0; }
      timeout 20 am force-stop --user "$user" "$pkg" > /dev/null 2>&1 || logn "am force-stop failed for ${1##*/}"
      ;;
    settings) do_settings "${1##*/}" ;;
    *) logn "dropped ${1##*/}: unknown kind" ;;
  esac
  return 0
}

[ -d "$Q" ] || exit 0
idle=0
pass=0
while [ $pass -lt 100 ]; do              # bounded; init restarts us on the next vendor.gb.notify change anyway
  pass=$((pass + 1))
  new=0
  LS=
  set +f                                 # the only glob: names are validated below; sorted = epoch ms = queue order
  for f in "$Q"/*; do [ -e "$f" ] && LS="$LS ${f##*/}"; done
  set -f
  for f in $LS; do
    case $f in
      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9]*-[0-9]*.msg) ;;
      *) continue ;;
    esac
    case $f in *[!0-9.a-z-]*) continue ;; esac
    case $DONE in *,"$f",*) continue ;; esac     # delivered once, even if the delete below fails
    DONE="$DONE$f,"
    new=1
    [ -f "$Q/$f" ] && handle "$Q/$f"
    rm -f "$Q/$f" 2>/dev/null || logn "cannot delete $f"
  done
  if [ $new = 0 ]; then
    [ $idle = 1 ] && break
    idle=1                               # one more look after 1 s: a producer may have raced our last scan
    sleep 1
  else
    idle=0
  fi
done
exit 0
