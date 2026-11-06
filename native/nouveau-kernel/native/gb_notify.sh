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
#                (wave 9, BACKLOG HS-O4) -u all is replaced: a UserHandle.ALL record was shown to every full user (a
#                guest too) and one dismissal removed it for all of them, but a notice is a matter for the person in
#                front of the screen. A notice is now posted ONCE, for the FOREGROUND FULL user (am get-current-user, a
#                running FULL user in pm list users, am get-started-user-state RUNNING_UNLOCKED; (review) as in gb_ui's
#                unlocked(), only a known not-ready state holds: an answer without a known state counts as unlocked,
#                so a future build's other wording cannot hold a notice that gb_ui's vendor.gb.ui.fg asked to post):
#                cmd notification post -u <that user> -S bigtext. Nothing is ever posted for the headless system user 0
#                or for all users. While no full user is in the foreground (the login screen before the sign-in, a user
#                that is still starting or locked) the notice is HELD: $Q/held/<tag> (the newest per tag, 0600, with
#                this boot's id), and posted for the first full user that comes to the foreground in this boot: gb_ui
#                publishes that user in vendor.gb.ui.fg once it is RUNNING_UNLOCKED ("none" otherwise), and init starts
#                gb_notify at every change (gb_nouveau.rc; the VM tree's gb.rc too). A held notice of another boot is
#                dropped (logged; (wave 10) unless its lasting state still holds, see "Stale messages" below). The O5
#                fallbacks below are kept with -u <user> in place of -u all; "Bad user" (the
#                user went away between the check and the post) holds the notice.
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
# (Wave 10, BACKLOG I-W9-1) Stale messages (INTERFACES 12.1b). A notice is shown only while what it says can still be
# true; a stale one is dropped (logged "dropped <file>: stale (<why>)" or "held <tag> dropped: stale (<why>)"):
#  * Boot: a queued message names no boot, so this boot's first run judges the messages it finds once (stale_first):
#    one whose epoch-ms name is more than 2 s older than this boot's start (date +%s - /proc/uptime) was queued in an
#    earlier boot, unless it is at or after (1 s) this boot's newest queue write (vendor.gb.notify, a property no
#    earlier boot can leave). Everything queued after that first look is this boot's: a later clock correction never
#    makes a message old. The run records the boot in $Q/.boot. A held notice carries its boot id (wave 9).
#  * A notice of an earlier boot is stale, except while the lasting state it reports still holds (the producers raise
#    these once, not at every boot): "Switched to Intel graphics" (N01, N19, N21) and (review) "NVIDIA GPU problem"
#    (N02: "Graphics are set to Intel only") while persist.vendor.gb.gfx_mode is not auto or nvidia; "NVIDIA app
#    graphics off" (N18) while persist.vendor.gb.gfx_ok is not 1; "Tablet mode -
#    Intel graphics" (N17) while vendor.gb.dtx.base is not attached / not-feasible. In any boot those three, the OTA
#    gate's notice (stale once /odm/etc/gb/ota_state is not unvalidated, or when it names another build than this
#    boot's) and "System update held" (stale when its "Startup stays on slot <Y>" is not this boot's slot) are
#    re-checked before they are shown (review 2: except N18 read from the queue in its own boot, which gb_power writes
#    before it mirrors that verdict to gfx_ok), and "Performance base connected" (N16) is stale while
#    vendor.gb.gfx_active is intel. Every other notice speaks of the boot that raised it ("Restart required", "until
#    restart", the holders, the detach attempt) or is raised again by its producer (the OTA gate at every unvalidated
#    boot).
#  * A held gb_dtx notice (an answer to one press of the detach button; the next press answers again) is stale after
#    300 s of uptime held (the longest detach attempt of INTERFACES 10.3 is about 130 s).
#  * kind=force-stop and kind=settings of an earlier boot are stale (gb_power queues all three settings again at every
#    boot, INTERFACES 23.4); kind=broadcast never is (GbGpu then reads the current state).
# A state that cannot be read (an empty property, no ota_state, an unparsed text) never makes a notice stale.
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
H=$Q/held                                # (wave 9, HS-O4) notices waiting for a full user in the foreground
LB=$Q/.boot                              # (wave 10, I-W9-1) the boot whose queue this script has looked at
DTX_HOLD_S=300                           # (wave 10) a held gb_dtx notice older than this (s of uptime) is stale
OLDQ=,                                   # (wave 10) queued messages of an earlier boot (stale_first)
FIRST=0
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

fg_user() {                              # -> FG: the foreground FULL user once it is unlocked; 1 = none, WHY = the reason
  FG=
  WHY=
  _c=$(timeout 20 am get-current-user 2> /dev/null)
  case $_c in '' | *[!0-9]*) WHY="am get-current-user failed"; return 1 ;; esac
  if [ ${#_c} -gt 4 ]; then WHY="am get-current-user failed"; return 1; fi
  if ! full_users; then WHY="pm list users failed"; return 1; fi
  case " $USERS " in
    *" $_c "*) ;;
    *) WHY="no full user in the foreground (user $_c)"; return 1 ;;   # the headless user 0's login screen, a profile
  esac
  _st=$(timeout 20 am get-started-user-state "$_c" 2>&1)
  case $_st in                           # (review) the same rule as gb_ui's unlocked(), which publishes vendor.gb.ui.fg
    *RUNNING_UNLOCKED*) FG=$_c; return 0 ;;
    *BOOTING* | *RUNNING_LOCKED* | *RUNNING_UNLOCKING* | *STOPPING* | *SHUTDOWN* | *'not started'*) ;;
    *) FG=$_c; return 0 ;;               # an answer without a known state counts as unlocked (gb_ui did so too)
  esac
  WHY="user $_c is not unlocked yet ($(printf '%s' "$_st" | tr -cd 'A-Z_' | cut -c1-24))"
  return 1
}

boot_id() {                              # -> BID: this boot's id (a held notice of another boot is stale)
  BID=$(head -c 64 "$R/proc/sys/kernel/random/boot_id" 2> /dev/null | tr -cd '0-9a-f-')
}

up_s() {                                 # (wave 10) -> UP: whole seconds since boot (/proc/uptime: CLOCK_BOOTTIME, counts a
  UP=                                    # suspend); '' = unreadable. At most 9 digits: mksh arithmetic is 32-bit
  { read -r UP _ < "$R/proc/uptime"; } 2> /dev/null
  UP=${UP%%.*}
  case $UP in '' | *[!0-9]*) UP= ;; esac
  [ ${#UP} -le 9 ] || UP=
}

gp() { getprop "$1" 2> /dev/null | tr -cd ' -~' | cut -c1-92; }   # (wave 10) a property; '' = unset or unreadable

ota_val() {                              # (wave 10) -> OV: the last "$1=" value of /odm/etc/gb/ota_state (gbinit writes it
  OV=                                    # at every boot; gb_otagate reads it the same way); '' = none
  _of=$R/odm/etc/gb/ota_state
  [ -f "$_of" ] || return 0
  [ "$(($(wc -c < "$_of" 2> /dev/null) + 0))" -le 4096 ] || return 0
  while IFS= read -r _l || [ -n "$_l" ]; do
    case $_l in "$1="*) OV=${_l#*=} ;; esac
  done < "$_of"
  OV=$(printf '%s' "$OV" | tr -cd ' -~' | cut -c1-120)
}

bid_of() {                               # (wave 10) the build id field of a fingerprint, cut as gb_otagate's bid() cuts it
  _b=$(printf '%s' "$1" | cut -d/ -f4 | tr -cd 'A-Za-z0-9._-' | cut -c1-24)
  printf '%s' "${_b:-unknown}"
}

stale_notice() {                         # (wave 10, BACKLOG I-W9-1, INTERFACES 12.1b) $1 tag, $2 title, $3 text, $4 1 = of an
  STALE=                                 # earlier boot, $5 s held in this boot ('' = not held or unknown), $6 queued = read
  SB=0                                   # from the queue (handle) -> 0 = stale (STALE = why; SB=1: only for being of an
  KEEP=                                  # earlier boot), 1 = show it (KEEP = the lasting state that still holds, for the log)
  case $1/$2 in
    'gb_update/System update not validated' | 'gb_update/System update still not validated')
      ota_val state                      # the OTA gate's notice (build_esp OTA_TITLE / OTA_TITLE_LOUD)
      if [ -n "$OV" ] && [ "$OV" != unvalidated ]; then STALE="this boot's ota_state is $OV, not unvalidated"; return 0; fi
      ota_val booted
      _cb=
      [ -n "$OV" ] && _cb=$(bid_of "$OV")
      _tb=
      case $3 in
        'Build '*' is not the build '*) _tb=${3#Build } ;;                # OTA_TEXT
        'Unvalidated boot '*' of build '*) _tb=${3#* of build } ;;        # OTA_TEXT_LOUD
      esac
      _tb=${_tb%% *}
      if [ -n "$_tb" ] && [ -n "$_cb" ] && [ "$_tb" != "$_cb" ]; then
        STALE="it names build $_tb, this boot runs $_cb"
        return 0
      fi
      ;;
    'gb_update/System update held')     # gb_slotsync N22/N23: "... Startup stays on slot <Y>; (slot <X>) is in the boot menu."
      _y=
      case $3 in *'Startup stays on slot '[AB]';'*) _y=${3#*Startup stays on slot }; _y=${_y%%;*} ;; esac
      case $(gp ro.boot.slot_suffix) in _a) _c=A ;; _b) _c=B ;; *) _c= ;; esac
      if [ -n "$_y" ] && [ -n "$_c" ] && [ "$_y" != "$_c" ]; then
        STALE="it keeps slot $_y, this boot runs slot $_c"
        return 0
      fi
      ;;
    'gb_dgpu/Switched to Intel graphics' | 'gb_gfx/Switched to Intel graphics' | 'gb_dgpu/NVIDIA GPU problem')
      # N01, N19, N21 and (review) N02 ("Graphics are set to Intel only"): raised once, the mode lasts
      _v=$(gp persist.vendor.gb.gfx_mode)
      case $_v in auto | nvidia) STALE="the graphics mode is $_v now"; return 0 ;; esac
      KEEP="persist.vendor.gb.gfx_mode=${_v:-(unset)}"
      return 1
      ;;
    'gb_gfx/NVIDIA app graphics off')   # N18: raised once per new fail verdict
      _v=$(gp persist.vendor.gb.gfx_ok)
      # (review 2) not for a message of this boot read from the queue: gb_power queues N18 (and writes the queue at
      # once) a few ms before st_mirror() writes that new verdict to gfx_ok, so the run it starts may still read the
      # pass before it; a held N18 and one of an earlier boot are judged
      if [ "$_v" = 1 ] && { [ "$4" = 1 ] || [ "$6" != queued ]; }; then STALE="the graphics self-test passed since"; return 0; fi
      KEEP="persist.vendor.gb.gfx_ok=${_v:-(unset)}"
      return 1
      ;;
    'gb_gfx/Tablet mode - Intel graphics')   # N17: raised at the detach, true while the base stays detached
      _v=$(gp vendor.gb.dtx.base)
      case $_v in attached | not-feasible) STALE="the keyboard base is attached now"; return 0 ;; esac
      KEEP="vendor.gb.dtx.base=${_v:-(unset)}"
      return 1
      ;;
    'gb_gfx/Performance base connected')   # N16
      if [ "$(gp vendor.gb.gfx_active)" = intel ]; then STALE="apps use Intel graphics now"; return 0; fi
      ;;
    gb_dtx/*)                            # an answer to one press of the detach button (INTERFACES 10.3)
      if [ -n "$5" ] && [ "$5" -gt "$DTX_HOLD_S" ]; then
        STALE="held $5 s; it answered one press of the detach button, the next press answers again"
        return 0
      fi
      ;;
  esac
  if [ "$4" = 1 ]; then STALE="queued before this boot"; SB=1; return 0; fi
  return 1
}

stale_first() {                          # (wave 10, BACKLOG I-W9-1) once per boot, before this boot's first pass: OLDQ = the
  boot_id                                # queued messages of an earlier boot (INTERFACES 12.1b); FIRST=1 until note_boot
  [ -n "$BID" ] || return 0              # no boot id: boots cannot be told apart, nothing is judged old
  _lb=
  if [ -f "$LB" ] && [ ! -L "$LB" ]; then { IFS= read -r _lb < "$LB"; } 2> /dev/null; fi
  [ "$_lb" = "boot=$BID" ] && return 0   # looked at already: everything queued since then is this boot's
  FIRST=1
  _ns=$(date +%s 2> /dev/null)
  case $_ns in 1[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) return 0 ;; esac   # 32-bit safe (until 2033)
  up_s
  [ -n "$UP" ] || return 0
  _cut=$((_ns - UP - 2))                 # this boot's start by the current clock, less 2 s for the rounding
  _mw=
  _m=$(gp vendor.gb.notify)              # this boot's newest queue write (epoch ms), set by its producers only
  case $_m in 1[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) _mw=$((${_m%???} - 1)) ;; esac
  _no=0
  set +f
  for _f in "$Q"/*; do
    _n=${_f##*/}
    case $_n in
      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9]*-[0-9]*.msg) ;;
      *) continue ;;
    esac
    case $_n in *[!0-9.a-z-]*) continue ;; esac
    _s=${_n%%-*}
    _s=${_s%???}
    case $_s in 1[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) continue ;; esac
    if [ -n "$_mw" ] && [ "$_s" -ge "$_mw" ]; then continue; fi
    [ "$_s" -lt "$_cut" ] || continue
    OLDQ="$OLDQ$_n,"
    _no=$((_no + 1))
  done
  set -f
  [ $_no = 0 ] || logn "this boot's first look at the queue: $_no message(s) older than this boot (epoch s < $_cut)"
  return 0
}

note_boot() {                            # (wave 10) after this boot's first pass: record the boot in $LB (0600 shell)
  [ "$FIRST" = 1 ] || return 0
  FIRST=0
  rm -f "$LB.tmp" 2> /dev/null
  if (umask 077; set -C; printf '%s\n' "boot=$BID" > "$LB.tmp") 2> /dev/null && mv -f "$LB.tmp" "$LB" 2> /dev/null; then
    return 0
  fi
  rm -f "$LB.tmp" 2> /dev/null
  logn "cannot record this boot in $LB: the next run judges the queue again"
  return 0
}

hold_notice() {                          # $1 tag, $2 title, $3 text, $4 message name, $5 why: $H/<tag>, the newest wins
  boot_id
  up_s                                   # (wave 10) the held notice's age in this boot (gb_dtx: DTX_HOLD_S)
  if [ -L "$H" ] || { [ -e "$H" ] && [ ! -d "$H" ]; }; then logn "cannot hold $4 ($5; $H is not a directory): dropped"; return 0; fi
  [ -d "$H" ] || mkdir -m 0700 "$H" 2> /dev/null
  rm -f "$H/$1.tmp" 2> /dev/null
  if (umask 077; set -C; printf 'boot=%s\nup=%s\ntag=%s\ntitle=%s\ntext=%s\n' "$BID" "$UP" "$1" "$2" "$3" > "$H/$1.tmp") 2> /dev/null &&
    mv -f "$H/$1.tmp" "$H/$1" 2> /dev/null; then
    logn "$4 held until a full user is in the foreground ($5)"
  else
    rm -f "$H/$1.tmp" 2> /dev/null
    logn "cannot hold $4 ($5): dropped"
  fi
  return 0
}

post_notice() {                          # $1 tag, $2 title, $3 text, $4 message file name (for the log)
  if ! fg_user; then hold_notice "$1" "$2" "$3" "$4" "$WHY"; return 0; fi
  rm -f "$H/$1" 2> /dev/null             # an older held notice of this tag is superseded
  cmd_post -u "$FG" -S bigtext -t "$2" "$1" "$3" && return 0
  case $PO in
    *'usage: cmd notification post'*)     # the usage lists the options this build has
      case $PO in *bigtext*) _bt=1 ;; *) _bt=0 ;; esac
      case $PO in
        *'-u|--user'*)
          if [ $_bt = 0 ] && cmd_post -u "$FG" -t "$2" "$1" "$3"; then
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
      if cmd_post -u "$FG" -t "$2" "$1" "$3"; then
        logn "$4 posted without -S bigtext (this build's cmd notification post has no bigtext style)"
        return 0
      fi
      ;;
    *'Bad user'*)                         # the foreground user went away between the check and the post
      hold_notice "$1" "$2" "$3" "$4" "user $FG refused: $(po_log)"
      return 0
      ;;
  esac
  logn "cmd notification post failed for $4 ($(po_log))"
  return 0
}

flush_held() {                           # post the held notices once a full user is in the foreground (this boot's only)
  { [ -d "$H" ] && [ ! -L "$H" ]; } || return 0
  _hl=
  set +f
  for _h in "$H"/*; do [ -f "$_h" ] && [ ! -L "$_h" ] && _hl="$_hl ${_h##*/}"; done
  set -f
  [ -n "$_hl" ] || return 0
  boot_id
  up_s
  _fg=0
  fg_user && _fg=1
  for _h in $_hl; do
    case $_h in gb_dgpu | gb_dtx | gb_dgpu_test | gb_gfx | gb_update) ;; *) rm -f "$H/$_h"; continue ;; esac
    _hb=''; _hu=''; _ht=''; _hti=''; _htx=''
    _hsz=$(($(wc -c < "$H/$_h" 2> /dev/null) + 0))
    if [ "$_hsz" -le 4096 ]; then
      while IFS= read -r _l || [ -n "$_l" ]; do
        _v=${_l#*=}
        [ ${#_v} -le 240 ] || continue
        case $_l in boot=*) _hb=$_v ;; up=*) _hu=$_v ;; tag=*) _ht=$_v ;; title=*) _hti=$_v ;; text=*) _htx=$_v ;; esac
      done < "$H/$_h"
    fi
    _old=0                               # (wave 10) of another boot: stale unless its lasting state still holds (12.1b)
    if [ -z "$BID" ] || [ "$_hb" != "$BID" ]; then
      _old=1
    fi
    if [ "$_ht" != "$_h" ] || ! printable "$_hti" 40 || ! printable "$_htx" 200; then
      rm -f "$H/$_h"; logn "held $_h is malformed: dropped"; continue
    fi
    _age=                                # (wave 10) seconds of uptime held in this boot (up= of hold_notice)
    if [ $_old = 0 ] && [ -n "$UP" ]; then
      case $_hu in '' | *[!0-9]*) ;; *) [ ${#_hu} -gt 9 ] || _age=$((UP - _hu)) ;; esac
    fi
    if stale_notice "$_h" "$_hti" "$_htx" "$_old" "$_age"; then
      rm -f "$H/$_h"
      if [ "$SB" = 1 ]; then logn "held $_h of another boot dropped"; else logn "held $_h dropped: stale ($STALE)"; fi
      continue
    fi
    if [ $_old = 1 ]; then               # (wave 10) still true: carried into this boot (held again with this boot's id)
      logn "held $_h of another boot kept: still true ($KEEP)"
      if [ $_fg = 0 ]; then hold_notice "$_h" "$_hti" "$_htx" "held $_h" "carried into this boot"; continue; fi
    fi
    [ $_fg = 1 ] || continue
    logn "held $_h posted for user $FG"
    post_notice "$_h" "$_hti" "$_htx" "held $_h"
  done
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
  _old=0                                 # (wave 10, I-W9-1) queued in an earlier boot (stale_first)
  case $OLDQ in *,"${1##*/}",*) _old=1 ;; esac
  case $kind in
    notify)
      case $tag in gb_dgpu | gb_dtx | gb_dgpu_test | gb_gfx | gb_update) ;; *) logn "dropped ${1##*/}: bad tag"; return 0 ;; esac
      printable "$title" 40 || { logn "dropped ${1##*/}: bad title"; return 0; }
      printable "$text" 200 || { logn "dropped ${1##*/}: bad text"; return 0; }
      if stale_notice "$tag" "$title" "$text" "$_old" '' queued; then logn "dropped ${1##*/}: stale ($STALE)"; return 0; fi
      [ $_old = 0 ] || logn "${1##*/} is older than this boot, still true ($KEEP): kept"
      post_notice "$tag" "$title" "$text" "${1##*/}"
      ;;
    broadcast) broadcast_state "${1##*/}" ;;   # (wave 10) never stale: GbGpu then reads the current state
    force-stop)
      if [ $_old = 1 ]; then logn "dropped ${1##*/}: stale (a force-stop queued before this boot)"; return 0; fi
      valid_pkg "$pkg" || { logn "dropped ${1##*/}: bad package name"; return 0; }
      case $user in '' | *[!0-9]*) logn "dropped ${1##*/}: bad user"; return 0 ;; esac
      [ ${#user} -le 4 ] || { logn "dropped ${1##*/}: bad user"; return 0; }
      timeout 20 am force-stop --user "$user" "$pkg" > /dev/null 2>&1 || logn "am force-stop failed for ${1##*/}"
      ;;
    settings)
      if [ $_old = 1 ]; then
        logn "dropped ${1##*/}: stale (settings queued before this boot; gb_power queues them again at every boot)"
        return 0
      fi
      do_settings "${1##*/}"
      ;;
    *) logn "dropped ${1##*/}: unknown kind" ;;
  esac
  return 0
}

[ -d "$Q" ] || exit 0
stale_first                              # (wave 10, I-W9-1) once per boot: the queued messages of an earlier boot
flush_held                               # (wave 9) e.g. started by vendor.gb.ui.fg: a full user came to the foreground
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
  note_boot                              # (wave 10) this boot's queue is looked at: later messages are this boot's
  if [ $new = 0 ]; then
    [ $idle = 1 ] && { flush_held; break; }   # (wave 9) a foreground change while this run was busy
    idle=1                               # one more look after 1 s: a producer may have raced our last scan
    sleep 1
  else
    idle=0
  fi
done
exit 0
