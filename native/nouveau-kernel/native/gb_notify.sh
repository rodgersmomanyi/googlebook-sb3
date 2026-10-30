#!/system/bin/sh
# gb_notify.sh -- delivers the dGPU notification queue (INTERFACES.md v1.2 section 12.1, D6). Service gb_notify,
# user shell (the identity "adb shell cmd notification post" uses), started by init on any vendor.gb.notify change.
#
# (Wave 6, notify) Headless system user mode: lapis sets ro.fw.mu.headless_system_user=true, so user 0 is the headless
# "System User" (the login screen) and the person uses a FULL user (10, ...). A shell notification without a user lands
# in user 0 and SystemUI hides it from every other user (NotCurrentUserFilter, wave-6 forced OTA-gate boot), and an
# "am broadcast --user 0" reaches only user 0's GbGpu. Delivery therefore is:
#   notify    -> cmd notification post -u all -S bigtext: ONE record for UserHandle.ALL (-1), shown by SystemUI to
#                whichever user is in the foreground -- at the login screen, after the sign-in and after any user
#                switch -- so nothing is lost before the person signs in and nothing is posted twice (the same tag
#                replaces the record; nothing is posted for user 0 any more).
#                (wave 7, notify O5) `cmd notification post` exits 0 even when it posted nothing (measured on lapis
#                276.R4: an unknown option prints its usage, a bad -u value or style prints "Error occurred. ..."), so
#                its OUTPUT decides (post_notice): a usage text = this build lacks one of our options, and the notice
#                is posted again with the options that usage lists (for all users without -S bigtext when only the
#                style is missing; without -u when -u is missing: the shell's own user 0, i.e. the login screen only,
#                logged); "unrecognized notification style" = for all users without -S bigtext; "Bad user" = one
#                post per running full user (-u <id>); any other error is logged. At most 1 + 8 posts per notice.
#   broadcast -> one explicit broadcast per RUNNING FULL user (pm list users: flag FULL 0x400 and "running"), never the
#                headless system user; a user that starts later (the sign-in) refreshes itself: GbGpu's UserReceiver
#                runs the same refresh at that user's BOOT_COMPLETED. If pm list users fails: --user current.
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

full_users() {                           # -> USERS: ids of the running FULL users (pm list users); 1 = no list
  USERS=
  _ul=$(timeout 20 pm list users 2> /dev/null) || return 1
  _nu=0
  _ifs=$IFS
  IFS='
'
  for _l in $_ul; do                     # "<tab>UserInfo{<id>:<name>:<hex flags>} running" (the name may hold ':')
    case $_l in *'UserInfo{'*'} running') ;; *) continue ;; esac
    _l=${_l%'} running'}
    _f=${_l##*:}
    _i=${_l#*'UserInfo{'}
    _i=${_i%%:*}
    case $_i in '' | *[!0-9]*) continue ;; esac
    case $_f in '' | *[!0-9a-fA-F]*) continue ;; esac
    if [ ${#_i} -gt 4 ] || [ ${#_f} -gt 7 ]; then continue; fi
    [ $((0x$_f & 0x400)) -ne 0 ] || continue   # FLAG_FULL: never the headless system user (811), never a profile
    USERS="$USERS $_i"
    _nu=$((_nu + 1))
    [ $_nu -lt 8 ] || break
  done
  IFS=$_ifs
  return 0
}

cmd_post() {                             # cmd notification post "$@" -> PO = its output; 0 = no sign of a failure
  PO=$(timeout 20 cmd notification post "$@" 2>&1)
  _pr=$?
  case $PO in *'usage: cmd notification post'* | *'Error occurred'*) return 1 ;; esac
  [ $_pr -eq 0 ]
}

po_log() {                               # PO for a log line: printable ASCII, one line, at most 120 bytes
  printf '%s' "$PO" | tr -c ' -~' ' ' | cut -c1-120
}

post_notice() {                          # $1 tag, $2 title, $3 text, $4 message file name (for the log)
  cmd_post -u all -S bigtext -t "$2" "$1" "$3" && return 0
  case $PO in
    *'usage: cmd notification post'*)     # the usage lists the options this build has
      case $PO in *bigtext*) _bt=1 ;; *) _bt=0 ;; esac
      case $PO in
        *'-u|--user'*)
          if [ $_bt = 0 ] && cmd_post -u all -t "$2" "$1" "$3"; then
            logn "$4 posted without -S bigtext (this build's cmd notification post has no bigtext style)"
            return 0
          fi
          ;;
        *)
          if { [ $_bt = 1 ] && cmd_post -S bigtext -t "$2" "$1" "$3"; } || { [ $_bt = 0 ] && cmd_post -t "$2" "$1" "$3"; }; then
            logn "this build's cmd notification post has no -u: $4 posted for user 0 only (the login screen; not seen after the sign-in)"
            return 0
          fi
          ;;
      esac
      ;;
    *'notification style'*)               # this build has no bigtext style
      if cmd_post -u all -t "$2" "$1" "$3"; then
        logn "$4 posted without -S bigtext (this build's cmd notification post has no bigtext style)"
        return 0
      fi
      ;;
    *'Bad user'*)                         # -u all refused: one post per running full user
      if full_users && [ -n "$USERS" ]; then
        _np=0
        for _u in $USERS; do cmd_post -u "$_u" -S bigtext -t "$2" "$1" "$3" && _np=$((_np + 1)); done
        logn "cmd notification post -u all refused for $4: posted per running full user ($_np of$USERS)"
        return 0
      fi
      ;;
  esac
  logn "cmd notification post failed for $4 ($(po_log))"
  return 0
}

broadcast_state() {                      # $1 = message file name (for the log)
  if ! full_users; then
    logn "pm list users failed: state broadcast $1 sent to --user current"
    timeout 20 am broadcast --user current --include-stopped-packages -a gb.sb3.gpu.action.STATE_CHANGED \
      -p gb.sb3.gpu > /dev/null 2>&1 || logn "am broadcast failed for $1 (user current)"
    return 0
  fi
  if [ -z "$USERS" ]; then
    logn "no running full user: state broadcast $1 skipped (GbGpu refreshes at the user's BOOT_COMPLETED)"
    return 0
  fi
  for _u in $USERS; do
    timeout 20 am broadcast --user "$_u" --include-stopped-packages -a gb.sb3.gpu.action.STATE_CHANGED \
      -p gb.sb3.gpu > /dev/null 2>&1 || logn "am broadcast failed for $1 (user $_u)"
  done
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
  if [ ! -r "$1" ]; then                # (wave 6) e.g. a producer that forgot umask 022 (12.1 asks for 0644)
    logn "dropped ${1##*/}: not readable by gb_notify (12.1: mode 0644)"
    return 0
  fi
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
      post_notice "$tag" "$title" "$text" "${1##*/}"
      ;;
    broadcast) broadcast_state "${1##*/}" ;;
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
