#!/system/bin/sh
# gb_nouveau.sh -- nouveau policy for the Surface Book 3 dGPU (NVIDIA TU117 in the keyboard base). Native images only.
#
# Contract: C:\GB-build\native\dgpu\INTERFACES.md v1.2, sections 2.2, 3, 4, 5, 6.4-6.5, 7 (this file is 7.1-7.8,
# with 7.2-v12, 7.2a and the v1.2 notes of 7.3, 7.5, 7.7), 22.2 (graphics modes) and the review fix pass 29 (R-1, R-2,
# R-7, R-10, R-11: clean shutdown, Intel switch during gb_power's rebind guard, suspend-safe windows, wakelock,
# node permissions, rmmod deadline); the ESP lock follows 5.4 as of r3 (Wave 2b: back-off while a steal is in
# progress, exclusive owner file, put-back without replacing, release waits for a put-back).
#   gb_nouveau.sh auto         service gb_nouveau: sys.boot_completed=1, the gfx_mode=auto|nvidia triggers after
#                               vendor.gb.nouveau.booted=1, gb_power hot attach (INTERFACES 23.7)
#   gb_nouveau.sh off           service gb_nouveau_off: the gfx_mode=intel trigger after booted=1, gb_power
#                               reconcile_off() (INTERFACES 23.6)
#   gb_nouveau.sh dmesg-stream  service gb_dgpu_dmesg: vendor.gb.dgpu.dmesg=1 (test boots, probation loads)
# Exit codes: 0 done or nothing to do, 1 refused or failed, 2 usage.
#
# Graphics mode (persist.vendor.gb.gfx_mode, INTERFACES 22.2): auto (also unset or any unknown value) and nvidia load
# nouveau after sys.boot_completed whenever the base with the dGPU is attached; intel never loads it, and "off"
# unbinds/unloads it. gb.nouveau=once|test on the command line load whatever the mode.
#
# Safety rails (NVIDIA_PLAN.md section 8):
# * nouveau is never in a load list; it is insmodded (or, with the module already loaded, bound: the bind path) here,
#   only after sys.boot_completed, only when renderD128 and card0 are Intel/i915, the dGPU (first 10de function of
#   class 0x03) has a BAR, and no vfat mount of the ESP exists.
# * Kill switches (boot editor, no shell needed): gb.nouveau=0, any unknown gb.nouveau= value,
#   module_blacklist=...nouveau... .
# * Crash-loop guard: /data/vendor/gb/nouveau.loading ("mode=.. t=.. boot=<boot_id> argv=..") is written + synced
#   before the first insmod or the bind write and removed only when the load stayed healthy for 120 s (or failed
#   cleanly). Finding a marker of an earlier boot (or one without boot=) = that load froze or panicked: the graphics
#   mode is set to intel and notification N01 is queued. A marker of this boot belongs to another window of this boot
#   (gb_power's rebind guard); the ESP lock serializes the windows. kernel.panic=30 on every boot that loads nouveau
#   turns an oops (PANIC_ON_OOPS=y) into a reboot instead of a permanent freeze. (R-1) A clean shutdown (SIGTERM from
#   init with sys.powerctl set) removes our own marker unless a bind write may still be inside the kernel's probe: a
#   reboot right after a load is not a crash (the boot is not counted as clean either).
# * (R-7) The health window and the clean mark are counted in seconds slept (CLOCK_MONOTONIC, as sleep is), not from
#   /proc/uptime (CLOCK_BOOTTIME, which jumps over a system suspend); a wakelock (gb_nouveau) is held from the load
#   window's start to the health verdict.
#   The crash-loop, kill-switch and unhealthy paths also create the root-only flag /dev/gb_dgpu/noload: no load for
#   the rest of the boot, whatever any (untrusted) property says. These checks run before the "already loaded"
#   branch, so the bind path never binds a driver this boot marked sick (keep, noload).
# * Only whitelisted module arguments reach insmod; "runpm=1 modeset=2" are always last.
# * No insmod and no bind while gb_power handles a detach (/dev/gb_dgpu/detaching, checked again right before the
#   nouveau insmod or the bind write together with the dGPU and its BAR); the load window marks the boot "used" from
#   its start. A bind that does not return within 20 s marks the dGPU wedged: no further dGPU write this boot.
# * "off" never unbinds while /sys/module/nouveau/refcnt > 0, never retries after a hung unbind (wedged), never
#   rmmods after nouveau buffers were shared with another device (foreign). The GPU user services (gb_gpu_compute,
#   gb_nvk_probe, gb_dgpu_test, gb_gfx_selftest) are stopped first. A module kept loaded after off creates
#   /dev/gb_dgpu/off. "off" does nothing unless the graphics mode is intel (checked again after the refcnt read).
#   (R-2) "off" also waits for gb_power's rebind guard (ESP lock owner gb_power). (R-11) rmmod runs with a 20 s
#   deadline like the unbind; a missed one marks the dGPU wedged.
# * The nouveau render node is never made more permissive than 0660 by this script; only gb_power raises it to 0666
#   (INTERFACES 7.5, 23.5). (R-10) With the driver already bound, a node at 0660 or 0666 is left as it is (gb_power
#   owns the 0660/0666 choice); the permissions are applied again right before the load window closes.
# * Root-only state: /data/vendor/gb is 0751 (group shell cannot create, rename or delete entries in it), the
#   payload directory and copy are checked to be plain root-owned entries before use, and notification files are
#   created without following or reusing a planted entry (noclobber, umask).
# * Status-only sysfs reads; DRM nodes are never opened.
#
# Test hooks: GB_ROOT prefixes every absolute path (/proc, /sys, /dev, /data, /odm, /mnt, /vendor); GB_TEST_PATH is
# prepended to PATH. GB_ROOT must not contain whitespace. See ../test/test_gb_nouveau.sh.

LC_ALL=C
export LC_ALL
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin
export PATH
R=${GB_ROOT:-}
set -f
umask 077

GB_PANIC_TIMEOUT=30          # s; plan 12.2 / INTERFACES 20 (pending user approval): change it here only
ESP_LOCK_WAIT=120            # s, then refused:esp (INTERFACES 2.3)
HEALTH_WINDOW=30             # s after the nouveau insmod returns
CLEAN_MARK=120               # s after the nouveau insmod returns
UNBIND_POLLS=40              # x 0.5 s = 20 s unbind deadline
BIND_POLLS=40                # x 0.5 s = 20 s bind write deadline (bind path, INTERFACES 7.2a)
OFF_WAIT=30                  # s: the bind path waits this long for a running gb_nouveau_off, then refused:off
LOADER_WAIT=150              # s: "off" waits this long for a load window of a running "auto"
DETACH_WAIT=15               # s: "auto" waits this long for a detach in progress to end, then refused:detach
SVC_WAIT=10                  # x 0.5 s: "off" waits this long for the GPU user services to stop
DMESG_CAP=16777216           # bytes of dmesg-live.txt, then the stream stops
RMMOD_POLLS=40               # x 0.5 s = 20 s rmmod deadline in "off" (R-11)
WAKE=gb_nouveau              # wakelock name held during the load window (R-7)
GPU_USERS="gb_dgpu_test gb_nvk_probe gb_gpu_compute gb_gfx_selftest"   # services that open the nouveau render node
                                                                       # (INTERFACES 7.3; v1.2 adds the self-test)

GB=$R/data/vendor/gb
REP=$GB/report
MARK=$GB/nouveau.loading
CLEANF=$GB/nouveau.clean_boots
FLAGS=$R/dev/gb_dgpu
LOCK=$R/dev/gb_esp.lock
PCI=$R/sys/bus/pci/devices
PAYE=$R/odm/etc/gb/dgpu_payload.sha256
PAYD=$GB/payload
PAYF=$PAYD/dgpu.erofs
PAYMNT=/odm/dgpu
FWMNT=/odm/firmware/nvidia
ESPMNT=/mnt/gb_esp_ro
DEPS="drm_exec drm_gpuvm gpu-sched drm_ttm_helper"

CMD=${1:-}
LOCKED=0
LOADING=0
NQ=0
V=
MODE=
KREL=
BACKING=
PROB_TXT=
PROBATION=0
BSEQ=0
TOKEN=none
OWNLINE=
MYUID=
MEM_BEFORE=
MEM_AFTER=
INSMOD_MS=
BIND_MS=
D3_MS=
RMV=
FBSR=
RMAP=
REFCNT0=
DM_HIT=
MARKW=insmod                 # kernel log mark word: insmod (insmod path) or bind (bind path)
REC=load                     # record prefix: report/load-<N>.txt, or report/bind-<N>.txt for the bind path
GFXM=
BOOTID=
MARKED=0                     # R-1: 1 once write_marker wrote the crash-loop marker (MARKLINE = its exact line)
MARKLINE=
BINDING=0                    # R-1: 1 while a bind write may still be inside the kernel (never remove the marker then)
HC_S=0                       # R-7: seconds slept since the probe returned (health window + clean mark)
WAKED=0
RMST=

# ---------------------------------------------------------------- small helpers

rd() {                                   # V = first line of $1 (sysfs/procfs attribute); 1 if unreadable
  V=
  [ -r "$1" ] || return 1
  IFS= read -r V < "$1" || [ -n "$V" ]
}

lsdir() {                                # LS = basenames in directory $1, sorted (C locale); the only glob in here
  LS=                                    # besides steal_busy's "$LOCK".stale.* (INTERFACES 5.4 r3)
  set +f
  for _ls_e in "$1"/*; do
    [ -e "$_ls_e" ] || [ -L "$_ls_e" ] || continue
    LS="$LS ${_ls_e##*/}"
  done
  set -f
}

up_ms() {                                # UPMS / UPS = uptime in ms / s (from /proc/uptime, 1/100 s resolution)
  _um_u=0
  read -r _um_u _ < "$R/proc/uptime" 2>/dev/null
  _um_s=${_um_u%%.*}
  _um_f=${_um_u#*.}
  [ "$_um_f" = "$_um_u" ] && _um_f=0
  _um_f=${_um_f}00
  _um_f=${_um_f%"${_um_f#??}"}
  case $_um_f in 0?) _um_f=${_um_f#0} ;; esac
  case $_um_s$_um_f in '' | *[!0-9]*) _um_s=0 _um_f=0 ;; esac
  UPMS=$((_um_s * 1000 + _um_f * 10))
  UPS=$_um_s
}

san() {                                  # printable ASCII only, at most $2 (default 32) characters
  printf '%s' "$1" | tr -cd ' -~' | cut -c1-"${2:-32}"
}

repmode() {                              # report files: 0640 root:shell (INTERFACES 5.1)
  chmod 0640 "$1" 2>/dev/null
  chown root:shell "$1" 2>/dev/null
}

log() {
  printf 'gb_nouveau: %s\n' "$*" >> "$R/dev/kmsg" 2>/dev/null
  [ -d "$REP" ] || return 0
  _lg_f=$REP/gb_nouveau.log
  if [ -f "$_lg_f" ]; then
    _lg_sz=$(($(wc -c < "$_lg_f" 2>/dev/null) + 0))
    [ "$_lg_sz" -gt 1048576 ] && mv -f "$_lg_f" "$_lg_f.1"
  fi
  _lg_new=0
  [ -f "$_lg_f" ] || _lg_new=1
  up_ms
  printf '%s %s[%s] %s\n' "$UPS" "$CMD" "$$" "$*" >> "$_lg_f" 2>/dev/null
  [ $_lg_new = 1 ] && repmode "$_lg_f"
  return 0
}

gp() { getprop "$1" 2>/dev/null; }

sp() { setprop "$1" "$2" 2>/dev/null || log "setprop $1 failed"; }

gfx_mode() {                             # GFXM = the graphics mode (INTERFACES 22.2, the same rule as gb_power):
  GFXM=$(gp persist.vendor.gb.gfx_mode)  # exactly auto, intel or nvidia; anything else, unset included, is auto
  case $GFXM in auto | intel | nvidia) ;; *) GFXM=auto ;; esac
}

set_booted() {                           # INTERFACES 4 (v1.2, review E2): every set of booted=1 re-fires the rc
  [ "$(gp vendor.gb.nouveau.booted)" = 1 ] || sp vendor.gb.nouveau.booted 1   # triggers, so only when not yet 1
}

boot_id() {                              # BOOTID = /proc/sys/kernel/random/boot_id ("" if unreadable or odd)
  BOOTID=
  rd "$R/proc/sys/kernel/random/boot_id" || return 0
  case $V in '' | *[!A-Za-z0-9-]*) ;; *) BOOTID=$V ;; esac
  return 0
}

result() {
  sp vendor.gb.nouveau.result "$1"
  log "result=$1"
}

flag_set() {                             # monotonic per-boot flag file in /dev/gb_dgpu (root only, D9)
  [ -d "$FLAGS" ] || { mkdir -p "$FLAGS" && chmod 0700 "$FLAGS"; }
  : > "$FLAGS/$1"
}

foreign() { [ "$(gp vendor.gb.nouveau.foreign)" = 1 ] || [ -e "$FLAGS/foreign" ]; }

wake_lock() {                            # R-7: no system suspend inside the load window (kernel wakeup source)
  [ "$WAKED" = 1 ] && return 0
  [ -w "$R/sys/power/wake_lock" ] || return 0
  printf '%s\n' "$WAKE" > "$R/sys/power/wake_lock" 2>/dev/null && WAKED=1
  return 0
}

wake_unlock() {
  [ "$WAKED" = 1 ] || return 0
  printf '%s\n' "$WAKE" > "$R/sys/power/wake_unlock" 2>/dev/null
  WAKED=0
}

hw_native() { rd "$R/odm/etc/gb_hw" && [ "$V" = native ]; }

drv_of() {                               # DRV = basename of $1/driver ("" if unbound)
  _l=$(readlink "$1/driver" 2>/dev/null)
  DRV=${_l##*/}
}

mem_avail() {
  MEMA=
  [ -r "$R/proc/meminfo" ] || return 0
  while read -r _k _v _; do
    [ "$_k" = MemAvailable: ] && { MEMA=$_v; break; }
  done < "$R/proc/meminfo"
}

# ---------------------------------------------------------------- kernel command line (INTERFACES 3)

parse_cmdline() {
  CL_NV=
  CL_NV_SET=0
  CL_FW=
  CL_BL=
  CL_PART=EFI-SYSTEM
  _cl=
  rd "$R/proc/cmdline" && _cl=$V
  for _a in $_cl; do                     # set -f: split only, no globbing; the last occurrence wins
    case $_a in
      gb.nouveau=*) CL_NV=${_a#gb.nouveau=}; CL_NV_SET=1 ;;
      gb.nouveau.fw=*) CL_FW=${_a#gb.nouveau.fw=} ;;
      module_blacklist=*) CL_BL="$CL_BL,${_a#module_blacklist=}" ;;
      gb.logpart=*)
        _p=${_a#gb.logpart=}
        case $_p in '' | *[!A-Za-z0-9_-]*) ;; *) CL_PART=$_p ;; esac ;;
    esac
  done
  KILL=0
  CL_MODE=
  if [ $CL_NV_SET = 1 ]; then
    case $CL_NV in
      0) KILL=1 ;;
      once | test) CL_MODE=$CL_NV ;;
      *) KILL=1; log "unknown gb.nouveau value '$(san "$CL_NV")' (treated as gb.nouveau=0)" ;;
    esac
  fi
  case "$CL_BL," in *,nouveau,*) KILL=1 ;; esac     # any module_blacklist= listing nouveau (fail safe)
  [ $KILL = 1 ] && CL_MODE=
  return 0
}

# ---------------------------------------------------------------- device discovery (INTERFACES 2.2)

nv_funcs() {                             # NVF = every PCI function with vendor 0x10de, sorted
  NVF=
  lsdir "$PCI"
  for _b in $LS; do
    case $_b in *[!0-9a-f:.]*) continue ;; esac
    rd "$PCI/$_b/vendor" && [ "$V" = 0x10de ] && NVF="$NVF $_b"
  done
}

find_dgpu() {                            # DGPU = first 10de function whose class is 0x03xxxx
  DGPU=
  nv_funcs
  for _b in $NVF; do
    rd "$PCI/$_b/class"
    case $V in 0x03*) DGPU=$_b; return 0 ;; esac
  done
  return 1
}

bar_ok() {                               # first resource line has a non-zero start address
  rd "$PCI/$DGPU/resource" || return 1
  _st=${V%% *}
  case $_st in 0x*[1-9a-fA-F]*) return 0 ;; esac
  return 1
}

i915_ok() {
  for _n in renderD128 card0; do
    rd "$R/sys/class/drm/$_n/device/vendor"
    _v=$V
    drv_of "$R/sys/class/drm/$_n/device"
    if [ "$_v" != 0x8086 ] || [ "$DRV" != i915 ]; then
      log "guard i915: $_n is vendor '$(san "$_v")' driver '$(san "$DRV")'"
      return 1
    fi
  done
  return 0
}

nv_nodes() {                             # NODES = DRM nodes (card<n>, renderD<n>) driven by nouveau
  NODES=
  lsdir "$R/sys/class/drm"
  for _n in $LS; do
    case $_n in
      card*) _k=${_n#card} ;;
      renderD*) _k=${_n#renderD} ;;
      *) continue ;;
    esac
    case $_k in '' | *[!0-9]*) continue ;; esac
    drv_of "$R/sys/class/drm/$_n/device"
    [ "$DRV" = nouveau ] && NODES="$NODES $_n"
  done
}

apply_perms() {                          # INTERFACES 7.5 (v1.2): render 0660 (gb_power alone raises it to 0666 while
  nv_nodes                               # its own derivation is nvidia, 23.5); no property is read for the mode;
  _mode=0660                             # i915 nodes are never touched. $1 = bound (R-10, the driver was already
  for _n in $NODES; do                   # bound): a render node at 0660 or 0666 is gb_power's choice and is kept
    _p=$R/dev/dri/$_n
    [ -e "$_p" ] || continue
    case $_n in
      renderD*)
        chown root:graphics "$_p"
        if [ "${1:-}" = bound ]; then
          case $(stat -c %a "$_p" 2>/dev/null) in
            660 | 666) log "perms $_n root:graphics (0660/0666 left to gb_power)"; continue ;;
          esac
        fi
        chmod "$_mode" "$_p"
        log "perms $_n root:graphics $_mode" ;;
      card*) chown root:root "$_p"; chmod 0600 "$_p"; log "perms $_n root:root 0600" ;;
    esac
  done
}

# ---------------------------------------------------------------- ESP lock and ESP mount check (INTERFACES 5.4)

steal_busy() {                           # 0 = another live process is in the middle of a steal (INTERFACES 5.4 r3):
  # a directory gb_esp.lock.stale.<pid> of a live pid other than ours (it may put a moved lock back at any moment)
  _sb_r=1
  set +f
  for _sb_e in "$LOCK".stale.*; do
    _sb_p=${_sb_e##*.}
    case $_sb_p in '' | *[!0-9]*) continue ;; esac
    [ "$_sb_p" != "$$" ] && [ -d "$_sb_e" ] && [ -d "$R/proc/$_sb_p" ] && { _sb_r=0; break; }
  done
  set -f
  return $_sb_r
}

steal_lock() {                           # $1 = stale owner line seen, $2 = inode seen; 0 = removed, retry mkdir
  # INTERFACES 5.4: never "rm owner; rmdir" a lock in place (two stealers would both win). Rename it away first:
  # only one waiter can rename a given directory. Then check that what was moved is the lock judged stale (same
  # inode, same owner line); anything else is put back (r3): never over an entry in the way (toybox mv -T refuses
  # anyway; the -e check makes GNU mv do the same), (r4) retried every 0.05 s for up to 5 s while newcomers do not
  # mkdir (they see our .stale. directory); a moved directory without an owner file that cannot go back is removed.
  _st=$LOCK.stale.$$
  rm -rf "$_st" 2>/dev/null
  mv -T "$LOCK" "$_st" 2>/dev/null || return 1      # another waiter won, or the lock is gone: retry mkdir
  _ol2=
  [ -r "$_st/owner" ] && IFS= read -r _ol2 < "$_st/owner"
  if [ "$(stat -c %i "$_st" 2>/dev/null)" = "$2" ] && [ "$_ol2" = "$1" ]; then
    log "ESP lock: stealing a stale lock (owner '$(san "$1" 60)')"
    rm -f "$_st/owner"
    rmdir "$_st" 2>/dev/null
    return 0
  fi
  _sl_n=0
  while :; do
    if [ ! -e "$LOCK" ] && mv -T "$_st" "$LOCK" 2>/dev/null; then
      log "ESP lock: the lock changed while stealing it (owner '$(san "$_ol2" 60)'): put back"
      return 1
    fi
    if [ ! -e "$_st/owner" ] && rmdir "$_st" 2>/dev/null; then
      log "ESP lock: the lock changed while stealing it: the moved directory had no owner and another is in place: removed"
      return 1
    fi
    [ $_sl_n -ge 100 ] && break          # r4 (wave 2b review L1): every 0.05 s for up to 5 s, not once per poll
    sleep 0.05
    _sl_n=$((_sl_n + 1))
  done
  log "ESP lock: the lock changed while stealing it and could not be put back (left as ${_st##*/})"
  return 1
}

lock_esp() {                             # $1 = max wait (s); INTERFACES 5.4 (r4)
  _w=0
  _miss=0
  _lk_b=0
  while :; do
    if [ $_lk_b -lt 5 ] && steal_busy; then   # r4 (L1): a steal is in progress: no mkdir at all (our directory
      _lk_b=$((_lk_b + 1))                    # would block its put-back); only consecutive back-off polls count
      [ $_lk_b = 1 ] && log "ESP lock: a steal is in progress: backing off"
    elif mkdir "$LOCK" 2>/dev/null; then
      if [ $_lk_b -lt 5 ] && steal_busy; then
        rmdir "$LOCK" 2>/dev/null        # r3: a steal is in progress; rmdir only removes an empty directory (ours)
        _lk_b=$((_lk_b + 1))
        [ $_lk_b = 1 ] && log "ESP lock: a steal is in progress: backing off"
      else
        up_ms
        OWNLINE="$$ gb_nouveau $UPS"
        # r3: the owner file is created exclusively (noclobber): if a stealer put a lock back in place of our new
        # directory, its owner file is there and stays; that lock is not ours
        if (set -C; printf '%s\n' "$OWNLINE" > "$LOCK/owner") 2>/dev/null; then
          LOCKED=1
          return 0
        fi
        log "ESP lock: another lock took the place of ours before its owner was written: waiting"
        OWNLINE=
      fi
    else
      _lk_b=0                            # r4 (L1): a poll that finds the lock held resets the back-off count
      _ino=$(stat -c %i "$LOCK" 2>/dev/null)
      _ol=
      [ -r "$LOCK/owner" ] && IFS= read -r _ol < "$LOCK/owner"
      _o=${_ol%% *}
      _stale=0
      if [ -n "$_ol" ]; then
        _miss=0
        # r4 (L2): our own pid while we hold nothing = a put-back that landed after a release of ours gave up
        case $_o in '' | *[!0-9]*) _stale=1 ;; "$$") _stale=1 ;; *) [ -d "$R/proc/$_o" ] || _stale=1 ;; esac
      else
        _miss=$((_miss + 1))
        [ $_miss -ge 5 ] && _stale=1   # the owner file is written right after mkdir; 5 s without it = dead holder
      fi
      if [ $_stale = 1 ] && [ -n "$_ino" ]; then
        _miss=0
        steal_lock "$_ol" "$_ino" && continue
      fi
    fi
    [ $_w -ge "$1" ] && return 1
    sleep 1
    _w=$((_w + 1))
  done
}

unlock_esp() {                           # only our own lock (a lock stolen from us is somebody else's now)
  [ "$LOCKED" = 1 ] || return 0
  _ul_n=0
  while :; do
    _ol=
    [ -r "$LOCK/owner" ] && IFS= read -r _ol < "$LOCK/owner"
    if [ "$_ol" = "$OWNLINE" ]; then
      rm -f "$LOCK/owner"
      rmdir "$LOCK" 2>/dev/null
      break
    fi
    if [ $_ul_n -lt 7 ] && steal_busy; then   # r3: a stealer moved our lock away; it puts it back within moments (r4: 7 polls > its 5 s budget)
      sleep 1
      _ul_n=$((_ul_n + 1))
      continue
    fi
    log "ESP lock: not ours any more at release (owner '$(san "$_ol" 60)'): left alone"
    break
  done
  LOCKED=0
}

esp_mounted() {                          # 0 if a vfat mount of the ESP block device exists
  _esp=$(readlink -f "$R/dev/block/by-name/$CL_PART" 2>/dev/null)
  _emm=
  [ -n "$_esp" ] && [ -b "$_esp" ] && _emm=$(stat -L -c '%t:%T' "$_esp" 2>/dev/null)
  [ -r "$R/proc/mounts" ] || return 1
  while read -r _src _mp _type _; do
    [ "$_type" = vfat ] || continue
    [ -n "$_esp" ] || { log "ESP device unknown: treating vfat mount $_mp as the ESP"; return 0; }
    _s=$(readlink -f "$R$_src" 2>/dev/null)
    [ "$_s" = "$_esp" ] && { log "ESP is mounted at $_mp"; return 0; }
    if [ -n "$_emm" ] && [ -b "$_s" ] && [ "$(stat -L -c '%t:%T' "$_s" 2>/dev/null)" = "$_emm" ]; then
      log "ESP is mounted at $_mp (via $_src)"
      return 0
    fi
  done < "$R/proc/mounts"
  return 1
}

is_mounted() {                           # $1 = mount point as the kernel shows it (no GB_ROOT)
  [ -r "$R/proc/mounts" ] || return 1
  while read -r _s _m _; do
    [ "$_m" = "$1" ] && return 0
  done < "$R/proc/mounts"
  return 1
}

mount_src() {
  MSRC=
  [ -r "$R/proc/mounts" ] || return 0
  while read -r _s _m _; do
    [ "$_m" = "$1" ] && MSRC=$_s
  done < "$R/proc/mounts"
}

# ---------------------------------------------------------------- persistent counters, report files, queue

read_clean() {
  CLEAN=0
  rd "$CLEANF" && CLEAN=$V
  case $CLEAN in '' | *[!0-9]*) CLEAN=0 ;; esac
  while :; do case $CLEAN in 0?*) CLEAN=${CLEAN#0} ;; *) break ;; esac; done
  [ ${#CLEAN} -gt 6 ] && CLEAN=0
  return 0
}

write_clean() {
  if printf '%s\n' "$1" > "$CLEANF.tmp" 2>/dev/null; then
    chmod 0644 "$CLEANF.tmp"
    mv -f "$CLEANF.tmp" "$CLEANF" || log "cannot write $CLEANF"
  else
    log "cannot write $CLEANF"
  fi
  sp vendor.gb.nouveau.clean "$1"
}

bootseq() {                              # BSEQ = report number of this boot (INTERFACES 5.1, D22)
  _f=$GB/bootseq
  _l=$FLAGS/bootseq.lock
  rd "$R/proc/sys/kernel/random/boot_id"
  _id=$V
  _i=0
  while ! mkdir "$_l" 2>/dev/null; do
    _i=$((_i + 1))
    [ $_i -ge 10 ] && break
    sleep 0.5
  done
  _n=0
  _bid=
  [ -r "$_f" ] && read -r _n _bid < "$_f"
  case $_n in '' | *[!0-9]*) _n=0 ;; esac
  while :; do case $_n in 0?*) _n=${_n#0} ;; *) break ;; esac; done
  if [ "$_bid" != "$_id" ]; then
    _n=$((_n + 1))
    if printf '%s %s\n' "$_n" "$_id" > "$_f.tmp" 2>/dev/null; then
      chmod 0644 "$_f.tmp"
      mv -f "$_f.tmp" "$_f"
    fi
  fi
  rmdir "$_l" 2>/dev/null
  BSEQ=$_n
}

prune() {                                # keep the newest 20 report/<prefix>-<N>.txt (by N)
  lsdir "$REP"
  _all=
  for _x in $LS; do
    case $_x in "$1"-*.txt) _x=${_x#"$1"-}; _x=${_x%.txt} ;; *) continue ;; esac
    case $_x in '' | *[!0-9]*) continue ;; esac
    _all="$_all $_x"
  done
  # shellcheck disable=SC2086  # digits only (filtered above); one per line for sort
  _all=$(printf '%s\n' $_all | sort -n)
  _cnt=0
  for _x in $_all; do _cnt=$((_cnt + 1)); done
  [ $_cnt -gt 20 ] || return 0
  _del=$((_cnt - 20))
  for _x in $_all; do
    [ $_del -gt 0 ] || break
    rm -f "$REP/$1-$_x.txt"
    _del=$((_del - 1))
  done
}

qwrite() {                               # stdin -> $1 (.msg in notify/, which group shell can write: INTERFACES 12.1)
  # The temp file is created 0644 by umask and with noclobber (O_EXCL: a planted file or symlink is never followed,
  # truncated or chmodded), then renamed; no chmod/chown by path in that directory.
  _qt=$1.tmp
  if ! (umask 022 && set -C && cat > "$_qt") 2>/dev/null; then
    return 1
  fi
  mv -f -T "$_qt" "$1" 2>/dev/null || { rm -f "$_qt"; return 1; }
}

notify() {                               # $1 title, $2 text, $3 tag (default gb_dgpu): INTERFACES 12.1 queue
  _q=$GB/notify
  [ -d "$_q" ] || { log "notify queue missing, dropped: $1"; return 1; }
  _ms=$(date +%s%N 2>/dev/null | cut -c1-13)
  case $_ms in
    [1-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) _ms=$(($(date +%s) * 1000)) ;;
  esac
  NQ=$((NQ + 1))
  _qf=$_q/$_ms-$$-$NQ
  _ti=$(san "$1" 40)
  _tx=$(san "$2" 200)
  if printf 'kind=notify\ntag=%s\ntitle=%s\ntext=%s\n' "${3:-gb_dgpu}" "$_ti" "$_tx" | qwrite "$_qf.msg"; then
    sp vendor.gb.notify "$_ms"
    log "notification queued: $_ti"
  else
    log "notification not queued: $_ti"
  fi
}

holders() {                              # HN, HOLDERS (comm:pid,...), HNAMES (for N03) of the nouveau nodes
  HN=0
  HOLDERS=
  HNAMES=
  _hn_list=
  _alt=
  for _n in $NODES; do _alt=${_alt:+$_alt|}$_n; done
  [ -n "$_alt" ] || return 0
  _pids=
  _dirs=
  _maps=
  _nd=0
  lsdir "$R/proc"
  for _p in $LS; do
    case $_p in *[!0-9]*) continue ;; esac
    [ "$_p" = "$$" ] && continue
    _pids="$_pids $_p"
    _dirs="$_dirs $R/proc/$_p/fd"
    _maps="$_maps $R/proc/$_p/maps"
    _nd=$((_nd + 1))
  done
  [ $_nd -gt 0 ] || return 0
  _only=
  [ $_nd = 1 ] && _only=${_pids# }       # ls prints no "dir:" header for a single operand
  # One "ls -l" over every fd directory: a readlink per fd would fork tens of thousands of times. The paths are
  # /proc/<digits>/fd built above, and only the "-> target" text of the symlink lines is used.
  # shellcheck disable=SC2086,SC2012
  _fdp=$(ls -l $_dirs 2>/dev/null | awk -v re="-> /dev/dri/($_alt)\$" -v only="$_only" '
    /:$/ { d = $0; sub(/\/fd:$/, "", d); sub(/.*\//, "", d); next }
    $0 ~ re { print (only != "" ? only : d) }')
  # shellcheck disable=SC2086
  _mpp=$(grep -lE "/dev/dri/($_alt)( |\$)" $_maps 2>/dev/null | sed -n 's#.*/proc/\([0-9][0-9]*\)/maps$#\1#p')
  _seen=,
  for _p in $(printf '%s\n%s\n' "$_fdp" "$_mpp" | sort -un); do
    case $_p in '' | *[!0-9]*) continue ;; esac
    case $_seen in *,"$_p",*) continue ;; esac
    _seen="$_seen$_p,"
    rd "$R/proc/$_p/comm"
    _c=$(printf '%s' "$V" | tr -cd ' -~' | tr ',:' '__' | cut -c1-16)
    [ -n "$_c" ] || _c=pid$_p
    HN=$((HN + 1))
    HOLDERS=${HOLDERS:+$HOLDERS,}$_c:$_p
    case ",$_hn_list," in *,"$_c",*) ;; *) _hn_list=${_hn_list:+$_hn_list,}$_c ;; esac
  done
  _k=0
  _more=0
  _ifs=$IFS
  IFS=,
  for _c in $_hn_list; do
    _k=$((_k + 1))
    if [ $_k -le 5 ]; then HNAMES=${HNAMES:+$HNAMES, }$_c; else _more=$((_more + 1)); fi
  done
  IFS=$_ifs
  [ $_more -gt 0 ] && HNAMES="$HNAMES and $_more more"
  return 0
}

ctl_auto() {                             # power/control=auto on every 10de function and its upstream bridges
  nv_funcs
  _done=,
  for _b in $NVF; do
    [ -w "$PCI/$_b/power/control" ] && printf 'auto\n' > "$PCI/$_b/power/control" 2>/dev/null
    _p=$(readlink -f "$PCI/$_b" 2>/dev/null)
    while [ -n "$_p" ]; do
      _p=${_p%/*}
      _bn=${_p##*/}
      case $_bn in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f].[0-7]) ;;
        *) break ;;
      esac
      case $_done in *,"$_bn",*) continue ;; esac
      _done="$_done$_bn,"
      [ -w "$_p/power/control" ] && printf 'auto\n' > "$_p/power/control" 2>/dev/null
    done
  done
  log "power/control=auto on the NVIDIA functions and their bridges"
}

# ---------------------------------------------------------------- payload (INTERFACES 6.4-6.5)

pay_mode() { [ -f "$PAYE" ]; }

myuid() { [ -n "$MYUID" ] || MYUID=$(id -u 2>/dev/null); }

dir_ok() {                               # a real directory (no symlink) owned by this script's uid (root), mode 0700
  myuid
  [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -c '%u %a' "$1" 2>/dev/null)" = "$MYUID 700" ]
}

file_ok() {                              # a plain file (no symlink, one link) owned by this script's uid (root)
  myuid
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$(stat -c '%u %h' "$1" 2>/dev/null)" = "$MYUID 1" ]
}

payload_dir() {                          # D8 / INTERFACES 6.5: payload/ must be ours before anything is read from it
  if ! dir_ok "$PAYD"; then
    if [ -e "$PAYD" ] || [ -L "$PAYD" ]; then
      log "payload: $PAYD is not a root-only directory: recreated"
      rm -rf "$PAYD"                     # a symlink is removed itself, never followed
    fi
    mkdir -m 0700 "$PAYD" 2>/dev/null
    chown root:root "$PAYD" 2>/dev/null
    dir_ok "$PAYD" || { payerr error:sha "cannot create a root-only $PAYD"; return 1; }
  fi
  if { [ -e "$PAYF" ] || [ -L "$PAYF" ]; } && ! file_ok "$PAYF"; then
    log "payload: the /data copy is not a plain root-owned file: removed"
    rm -f "$PAYF"
  fi
  return 0
}

sha_of() {
  SHA=$(sha256sum "$1" 2>/dev/null)
  SHA=${SHA%% *}
  case $SHA in '' | *[!0-9a-f]*) SHA= ;; esac
  [ ${#SHA} = 64 ] || SHA=
  return 0
}

payerr() {                               # $1 vendor.gb.dgpu.payload value, $2 detail
  sp vendor.gb.dgpu.payload "$1"
  log "payload: $1${2:+ ($2)}"
  return 1
}

version_ok() {                           # payload VERSION names this krel and the module set is there
  _v1=0
  _v2=0
  [ -r "$R$PAYMNT/VERSION" ] || return 1
  while IFS= read -r _l; do
    [ "$_l" = "gb-dgpu-payload 1" ] && _v1=1
    [ "$_l" = "krel $1" ] && _v2=1
  done < "$R$PAYMNT/VERSION"
  [ $_v1 = 1 ] && [ $_v2 = 1 ] || return 1
  for _m in $DEPS nouveau; do [ -f "$R$PAYMNT/lib/modules/$_m.ko" ] || return 1; done
  return 0
}

copy_from_esp() {                        # $1 expected sha256, $2 krel; runs inside the ESP lock
  _dev=$R/dev/block/by-name/$CL_PART
  mkdir -p "$R$ESPMNT" 2>/dev/null
  if ! mount -t vfat -o ro,nodev,nosuid,noexec "$_dev" "$R$ESPMNT"; then
    payerr error:esp-mount "cannot mount $CL_PART read-only"
    return 1
  fi
  _src=$R$ESPMNT/gb/dgpu-$2.erofs
  _rc=0
  if [ ! -f "$_src" ]; then
    payerr absent "gb/dgpu-$2.erofs is not on the ESP"
    _rc=1
  else
    rm -f "$PAYF.tmp"
    log "payload: copying gb/dgpu-$2.erofs from the ESP"
    if cp "$_src" "$PAYF.tmp" 2>/dev/null; then
      sync
      sha_of "$PAYF.tmp"
      if [ "$SHA" = "$1" ]; then
        chmod 0640 "$PAYF.tmp"
        mv -f "$PAYF.tmp" "$PAYF" || { rm -f "$PAYF.tmp"; payerr error:sha "rename failed"; _rc=1; }
      else
        rm -f "$PAYF.tmp"
        payerr error:sha "copy from the ESP does not match the image's sha256"
        _rc=1
      fi
    else
      rm -f "$PAYF.tmp"
      payerr error:sha "copy failed"
      _rc=1
    fi
  fi
  if ! umount "$R$ESPMNT"; then              # always unmount; a leftover mount also blocks the insmod (esp_mounted)
    log "umount $ESPMNT failed"
    [ $_rc = 0 ] && payerr error:esp-mount "umount failed"
    _rc=1
  fi
  return $_rc
}

stage_payload() {                        # 0 = payload mounted at /odm/dgpu and firmware bound (fail closed)
  _want=
  _name=
  read -r _want _name _ < "$PAYE" 2>/dev/null
  _name=${_name#\*}
  case $_want in '' | *[!0-9a-f]*) payerr error:sha "malformed $PAYE"; return 1 ;; esac
  [ ${#_want} = 64 ] || { payerr error:sha "malformed $PAYE"; return 1; }
  [ "$_name" = "dgpu-$KREL.erofs" ] || { payerr error:krel "descriptor names '$(san "$_name" 80)'"; return 1; }
  if is_mounted "$PAYMNT"; then          # second auto run in this boot: verify and keep
    if ! is_mounted "$FWMNT"; then
      mkdir -p "$R$FWMNT" 2>/dev/null
      mount -o bind "$R$PAYMNT/firmware/nvidia" "$R$FWMNT" || { payerr error:bind; return 1; }
    fi
    version_ok "$KREL" || { payerr error:version "mounted payload is not for $KREL"; return 1; }
    mount_src "$PAYMNT"
    case $MSRC in /dev/block/loop* | /dev/loop*) BACKING='loop' ;; *) BACKING='file' ;; esac
    sp vendor.gb.dgpu.payload mounted
    log "payload already mounted"
    return 0
  fi
  is_mounted "$FWMNT" && { payerr error:mount "$FWMNT is mounted without the payload"; return 1; }
  payload_dir || return 1
  _have=
  if [ -f "$PAYF" ]; then sha_of "$PAYF"; _have=$SHA; fi
  if [ "$_have" != "$_want" ]; then
    [ -n "$_have" ] && log "payload: /data copy differs from the image (refreshing)"
    copy_from_esp "$_want" "$KREL" || return 1
  fi
  payload_dir || return 1                # checked again after the copy, right before the verify + mount
  file_ok "$PAYF" || { payerr error:sha "/data copy missing"; return 1; }
  sha_of "$PAYF"                         # re-verified right before every mount
  [ "$SHA" = "$_want" ] || { rm -f "$PAYF"; payerr error:sha "/data copy does not match"; return 1; }
  mkdir -p "$R$PAYMNT" "$R$FWMNT" 2>/dev/null
  if ! mount -t erofs -o ro,nodev,nosuid,context=u:object_r:vendor_file:s0 "$PAYF" "$R$PAYMNT"; then
    payerr error:mount
    return 1
  fi
  mount_src "$PAYMNT"
  case $MSRC in /dev/block/loop* | /dev/loop*) BACKING='loop' ;; *) BACKING='file' ;; esac
  if ! mount -o bind "$R$PAYMNT/firmware/nvidia" "$R$FWMNT"; then
    umount "$R$PAYMNT"
    payerr error:bind
    return 1
  fi
  if ! version_ok "$KREL"; then
    umount "$R$FWMNT"
    umount "$R$PAYMNT"
    payerr error:version "VERSION or module set does not match $KREL"
    return 1
  fi
  sp vendor.gb.dgpu.payload mounted
  log "payload mounted ($BACKING-backed)"
  return 0
}

unmount_payload() {                      # only after a successful rmmod nouveau and with gb_gpu_compute stopped
  pay_mode || return 0
  _ok=1
  if is_mounted "$FWMNT"; then umount "$R$FWMNT" || _ok=0; fi
  if [ $_ok = 1 ] && is_mounted "$PAYMNT"; then umount "$R$PAYMNT" || _ok=0; fi
  if [ $_ok = 1 ]; then
    sp vendor.gb.dgpu.payload ready
    log "payload unmounted"
  else
    log "payload unmount failed (stays mounted)"
  fi
}

# ---------------------------------------------------------------- arguments (INTERFACES 7.4)

build_args() {
  _rm=0
  _fw=0
  _dbg=
  _raw=$(gp persist.vendor.gb.nouveau.args)
  _ifs=$IFS
  IFS=' '
  for _t in $_raw; do                    # set -f: no globbing, nothing is ever evaluated
    case $_t in
      config=NvGspRm=0) _rm=1 ;;
      config=NvGspFw=0) _fw=1 ;;
      debug=gsp=debug) [ "$_dbg" = trace ] || _dbg=debug ;;
      debug=gsp=trace) _dbg=trace ;;
      *) log "dropped nouveau argument '$(san "$_t")' (not whitelisted)" ;;
    esac
  done
  IFS=$_ifs
  case $CL_FW in
    '' | 570) ;;
    535) _fw=1 ;;
    *) log "ignored gb.nouveau.fw value '$(san "$CL_FW")'" ;;
  esac
  [ "$MODE" = test ] && [ -z "$_dbg" ] && _dbg=debug
  if [ $_rm = 1 ] && [ $_fw = 1 ]; then
    _fw=0
    log "dropped NvGspFw=0: NvGspRm=0 disables GSP-RM and a forced GSP firmware would fail the probe"
  fi
  _cfg=
  [ $_rm = 1 ] && _cfg=NvGspRm=0
  [ $_fw = 1 ] && _cfg=${_cfg:+$_cfg,}NvGspFw=0
  ARGV=
  [ -n "$_cfg" ] && ARGV="config=$_cfg "
  [ -n "$_dbg" ] && ARGV="${ARGV}debug=gsp=$_dbg "
  ARGV="${ARGV}runpm=1 modeset=2"
}

# ---------------------------------------------------------------- dmesg helpers

kernel_lines() {                         # KLINES = kernel messages since the insmod (or bind) mark (all if it scrolled
  _all=$(dmesg 2>/dev/null)              # out of the ring)
  _since=$(printf '%s\n' "$_all" | sed -n "/gb_nouveau: $MARKW-mark $TOKEN\$/,\$p")
  [ -n "$_since" ] || _since=$_all
  KLINES=$(printf '%s\n' "$_since" | grep -vE '(gb_[a-z_]+|init): ')
}

OOPS_RE='WARNING:|Oops|BUG:|general protection|Call Trace:|blocked for more than'

dmesg_hit() {                            # DM_HIT = first line matching the health patterns (INTERFACES 7.7)
  DM_HIT=$(printf '%s\n' "$KLINES" | grep -iE 'nouveau.*(fail|error|timeout|fault)' | head -n 1)
  [ -n "$DM_HIT" ] || DM_HIT=$(printf '%s\n' "$KLINES" | grep -E "$OOPS_RE" | head -n 1)
  [ -n "$DM_HIT" ]
}

# ---------------------------------------------------------------- auto (INTERFACES 7.2)

finish_window() {                        # close the load window: loading=0, release the ESP lock and the wakelock
  if [ "$LOADING" = 1 ]; then
    sp vendor.gb.nouveau.loading 0
    LOADING=0
  fi
  unlock_esp
  wake_unlock
}

# shellcheck disable=SC2317  # invoked from the HUP/INT/TERM trap of "auto"
on_signal() {                            # R-1 (INTERFACES 5.1, 7.2-v12): at shutdown init sets sys.powerctl, then
  trap '' HUP INT TERM                   # SIGTERMs the service's whole process group (sleep children included)
  if [ "$MARKED" = 1 ] && [ -n "$(gp sys.powerctl)" ]; then
    _sg_why=
    if [ "$BINDING" = 1 ]; then          # a bind write that has not returned may be inside the kernel's probe
      drv_of "$PCI/$DGPU"
      [ "$DRV" = nouveau ] || _sg_why="a bind write is still pending"
    fi
    _sg_m=
    rd "$MARK" && _sg_m=$V
    [ -n "$_sg_why" ] || [ "$_sg_m" = "$MARKLINE" ] || _sg_why="the marker is not ours"
    if [ -z "$_sg_why" ]; then
      rm -f "$MARK"
      sync
      log "clean shutdown (sys.powerctl=$(san "$(gp sys.powerctl)" 40)) in the load window or the clean-mark wait: crash-loop marker removed (boot not counted)"
      exit 0
    fi
    log "shutdown signal: crash-loop marker kept ($_sg_why)"
  fi
  exit 1
}

refuse() {                               # $1 = result; arms the runtime triggers (booted=1)
  result "$1"
  finish_window
  set_booted
  exit 1
}

write_marker() {                         # $1 = mode (auto|once|test|bind): the v1.2 crash-loop marker (INTERFACES
  up_ms                                  # 5.1), written + synced right before the probe; 1 = cannot be written
  boot_id
  MARKLINE="mode=$1 t=$UPS boot=$BOOTID argv=$ARGV"
  if ! printf '%s\n' "$MARKLINE" > "$MARK" 2>/dev/null; then
    rm -f "$MARK"
    log "cannot write the crash-loop marker $MARK: refusing to load"
    return 1
  fi
  chmod 0600 "$MARK"
  sync
  MARKED=1
}

marker_of_this_boot() {                  # 0 = the marker names this boot (another window of this boot owns it);
  _mk=                                   # 1 = an earlier boot, no boot= field (v1.1 format) or no boot id: crash-loop
  rd "$MARK" && _mk=$V
  MARKTXT=$(san "$_mk" 120)
  _mb=
  for _t in $_mk; do                     # set -f: split only; the first boot= token (argv atoms never contain one)
    case $_t in boot=*) _mb=${_t#boot=}; break ;; esac
  done
  boot_id
  [ -n "$BOOTID" ] && [ "$_mb" = "$BOOTID" ]
}

render_map() {
  RMAP=
  lsdir "$R/sys/class/drm"
  for _n in $LS; do
    case $_n in renderD*) _k=${_n#renderD} ;; *) continue ;; esac
    case $_k in '' | *[!0-9]*) continue ;; esac
    rd "$R/sys/class/drm/$_n/device/vendor"
    _v=$V
    drv_of "$R/sys/class/drm/$_n/device"
    RMAP=${RMAP:+$RMAP,}$_n=$_v:$DRV
  done
}

record_load() {                          # report/load-<N>.txt (INTERFACES 7.7); the bind path writes bind-<N>.txt
  [ -d "$REP" ] || return 0              # (same keys, mode=bind), so the boot's insmod record is kept
  _rf=$REP/$REC-$BSEQ.txt
  _rmode=$MODE
  [ "$REC" = bind ] && _rmode=bind
  if {
    printf 'mode=%s\nargv=%s\nkrel=%s\npayload=%s\npayload_backing=%s\nprobation=%s\n' \
      "$_rmode" "$ARGV" "$KREL" "$(gp vendor.gb.dgpu.payload)" "$BACKING" "$PROB_TXT"
    printf 'mem_available_before_kb=%s\nmem_available_after_kb=%s\ninsmod_ms=%s\nbind_ms=%s\nd3cold_ms=%s\n' \
      "$MEM_BEFORE" "$MEM_AFTER" "$INSMOD_MS" "$BIND_MS" "$D3_MS"
    printf 'rm_version=%s\nfbsr_line=%s\nrender_map=%s\nrefcnt_baseline=%s\ndmesg_hit=%s\nhealth=%s\n' \
      "$RMV" "$FBSR" "$RMAP" "$REFCNT0" "$(san "$DM_HIT" 200)" "$1"
  } > "$_rf.tmp" 2>/dev/null; then
    repmode "$_rf.tmp"
    mv -f "$_rf.tmp" "$_rf"
  fi
  prune "$REC"
}

unhealthy() {                            # $1 = bind|d3cold|dmesg (INTERFACES 7.7, D20): never unloads the driver;
  log "unhealthy load: $1${DM_HIT:+ (dmesg: $(san "$DM_HIT" 160))}"   # the same handling for the bind path (7.2a)
  flag_set keep                          # before gfx_mode=intel: the rc then starts gb_nouveau_off, which must not
  flag_set noload                        # unload (keep), and no later auto run may load or bind again (noload)
  sp persist.vendor.gb.gfx_mode intel
  write_clean 0
  rm -f "$MARK"
  sync
  notify "NVIDIA GPU problem" "The NVIDIA driver started with errors ($1). Graphics are set to Intel only."
  record_load "unhealthy:$1"
  result "unhealthy:$1"
  apply_perms                            # R-10: nodes ueventd created during the window (0666) are 0660 now
  finish_window
  set_booted
  exit 1
}

insmod_failed() {                        # $1 = module, $2 = insmod exit status
  _st=${2:-1}
  _oops=
  if [ "$1" = nouveau ]; then
    kernel_lines
    _oops=$(printf '%s\n' "$KLINES" | grep -E "$OOPS_RE" | head -n 1)
  fi
  if [ "$_st" -ge 128 ] || [ -n "$_oops" ]; then
    # killed by a signal (an oops in its own context with panic_on_oops=0) or an oops during the probe: a crash
    # happened, so the marker stays (the next boot takes the crash-loop path) and nothing loads again this boot
    log "insmod $1.ko killed (status $_st)${_oops:+ after an oops: $(san "$_oops" 120)}: crash-loop marker kept"
    flag_set keep
    flag_set noload
    sp persist.vendor.gb.gfx_mode intel  # after keep and noload (INTERFACES 7.2-v12 step 13)
    sync
    refuse "insmod-failed:$1"
  fi
  log "insmod $1.ko failed (status $_st)"
  rm -f "$MARK"                          # no crash happened
  sync
  refuse "insmod-failed:$1"
}

late_refuse() {                          # $1 result, $2 log text: refusal after the marker was written
  log "$2"
  rm -f "$MARK"                          # nothing was probed
  sync
  refuse "$1"
}

health_check() {                         # sets WHY ("" = healthy) and the load record fields. R-7: the 30 s window is
  BIND_MS=                               # counted in seconds slept (HC_S, monotonic) from the probe's return; the
  D3_MS=                                 # *_ms record fields stay uptime differences (they include a suspend)
  WHY=
  HC_S=0
  while :; do
    drv_of "$PCI/$DGPU"
    if [ "$DRV" = nouveau ]; then
      up_ms
      [ -n "$BIND_MS" ] || BIND_MS=$((UPMS - T0))
      rd "$PCI/$DGPU/power/runtime_status"
      _rs=$V
      rd "$PCI/$DGPU/power_state"
      _ps=$V
      if [ "$_rs" = suspended ] && [ "$_ps" = D3cold ]; then
        D3_MS=$((UPMS - T0))
        break
      fi
    fi
    [ "$HC_S" -ge "$HEALTH_WINDOW" ] && break
    sleep 1
    HC_S=$((HC_S + 1))
  done
  kernel_lines
  dmesg_hit
  if [ -z "$BIND_MS" ]; then WHY=bind
  elif [ -z "$D3_MS" ]; then WHY=d3cold
  elif [ -n "$DM_HIT" ]; then WHY=dmesg
  fi
  mem_avail
  MEM_AFTER=$MEMA
  RMV=$(printf '%s\n' "$KLINES" | sed -n 's/.*RM version: *//p' | head -n 1 | tr -cd ' -~' | cut -c1-40)
  FBSR=$(printf '%s\n' "$KLINES" | grep 'fbsr' | head -n 1 | tr -cd ' -~' | cut -c1-200)
  render_map
  rd "$R/sys/module/nouveau/refcnt"
  REFCNT0=$V
}

detach_wait() {                          # gb_power is handling a detach request: let it finish (INTERFACES 5.2)
  _dw=0
  while [ -e "$FLAGS/detaching" ]; do
    if [ $_dw -ge $DETACH_WAIT ]; then
      log "a detach is under way (/dev/gb_dgpu/detaching): not loading"
      refuse refused:detach
    fi
    sleep 1
    _dw=$((_dw + 1))
  done
}

open_window() {                          # ESP lock (owner gb_nouveau), loading=1, result=loading, used (7.2 step 8);
  lock_esp "$ESP_LOCK_WAIT" || { log "ESP lock busy for ${ESP_LOCK_WAIT} s"; refuse refused:esp; }
  # NP-1B-D7: the root-only "never load" flags again after the lock wait: gb_power's rebind guard holds the lock
  # while the kernel re-probes, and a failed guard creates keep + noload (INTERFACES 23.8) before it releases it
  if [ -e "$FLAGS/wedged" ]; then
    unlock_esp
    log "auto: the dGPU was marked wedged while waiting for the ESP lock"
    result refused:wedged
    exit 1
  fi
  if [ -e "$FLAGS/noload" ]; then
    unlock_esp
    log "auto: no load for the rest of this boot (noload appeared while waiting for the ESP lock)"
    result refused:noload
    exit 1
  fi
  if [ "${1:-}" = bind ]; then           # $1 = bind: the bind path's own state, re-read inside the lock
    [ -e "$FLAGS/keep" ] && { log "bind: keep appeared while waiting for the ESP lock"; refuse refused:keep; }
    drv_of "$PCI/$DGPU"
    if [ "$DRV" = nouveau ]; then        # the kernel's own re-probe finished while we waited (gb_power's guard)
      log "bind: $DGPU got bound to nouveau while waiting for the ESP lock: permissions only"
      apply_perms
      unlock_esp
      set_booted
      exit 0
    fi
  fi
  wake_lock                              # R-7: held until the health verdict (finish_window)
  sp vendor.gb.nouveau.loading 1
  LOADING=1
  result loading
  sp vendor.gb.nouveau.used 1            # from the start of the load window: a detach now takes the checked slow
  flag_set used                          # path (and gb_power refuses while this window is open)
}

clean_mark() {                           # 120 s after the probe returned (T1): INTERFACES 7.2 step 18 / 7.2a step 8.
  while [ "$HC_S" -lt "$CLEAN_MARK" ]; do   # $1 = 1: count a clean boot (insmod path); 0: not (bind path, review C8).
    sleep 5                              # R-7: seconds slept since the probe returned (the health window included),
    HC_S=$((HC_S + 5))                   # so a system suspend does not shorten the 120 s
  done
  if [ ! -d "$R/sys/module/nouveau" ]; then
    rm -f "$MARK"
    sync
    log "clean mark: nouveau was unloaded meanwhile (not counted)"
    exit 0
  fi
  drv_of "$PCI/$DGPU"
  if [ "$DRV" != nouveau ]; then         # detached, or switched to Intel, within the 120 s (NP-1B-D2)
    rm -f "$MARK"
    sync
    log "clean mark: $DGPU is no longer bound to nouveau (detached or switched off meanwhile; not counted)"
    exit 0
  fi
  kernel_lines
  dmesg_hit && unhealthy dmesg           # errors that appeared after the health window
  rm -f "$MARK"
  sync
  if [ "$1" = 1 ]; then
    CLEAN=$((CLEAN + 1))
    write_clean "$CLEAN"
    record_load clean
    result clean
    log "clean load ($CLEAN clean boots)"
  else
    record_load clean
    result clean
    log "clean bind (clean boots stay $CLEAN: probation counts boots, not binds)"
  fi
  exit 0
}

do_bind() {                              # INTERFACES 7.2a: nouveau loaded, the dGPU present and driverless, the mode
  log "auto: nouveau is loaded and $DGPU is driverless: bind path"   # wants a driver; a load window like an insmod
  REC=bind
  MARKW=bind
  _ow=0                                  # step 1: a running "off" (the property only makes this wait)
  while [ "$(gp init.svc.gb_nouveau_off)" = running ]; do
    if [ $_ow -ge $OFF_WAIT ]; then
      log "bind: gb_nouveau_off still running after ${OFF_WAIT} s"
      refuse refused:off
    fi
    sleep 1
    _ow=$((_ow + 1))
  done
  i915_ok || refuse refused:i915         # step 2: guards
  find_dgpu || { log "guard: no NVIDIA display-class function (base detached?)"; refuse refused:nodgpu; }
  drv_of "$PCI/$DGPU"
  if [ "$DRV" = nouveau ]; then          # bound meanwhile (kernel re-probe, or a finished "off" wait)
    log "bind: $DGPU is bound to nouveau now: permissions only"
    apply_perms
    set_booted
    exit 0
  fi
  [ -z "$DRV" ] || { log "guard: $DGPU is bound to '$(san "$DRV")'"; refuse refused:nodgpu; }
  bar_ok || { log "guard: $DGPU has no BAR assigned"; refuse refused:bar; }
  detach_wait                            # step 3
  open_window bind
  KREL=$(uname -r 2>/dev/null)
  if pay_mode; then                      # firmware comes from the payload: verify it is still mounted (NP-1B-D3)
    stage_payload || refuse refused:payload
  fi
  esp_mounted && refuse refused:esp      # step 4
  bootseq
  mem_avail
  MEM_BEFORE=$MEMA
  ARGV=-
  write_marker bind || refuse refused:marker
  printf '%s\n' "$GB_PANIC_TIMEOUT" > "$R/proc/sys/kernel/panic" 2>/dev/null || log "cannot set kernel.panic"
  # step 5: right before the write: no detach under way, the dGPU still there, driverless, with its BAR
  [ -e "$FLAGS/detaching" ] && late_refuse refused:detach "a detach started during the bind window: not binding"
  find_dgpu || late_refuse refused:nodgpu "the NVIDIA function disappeared during the bind window"
  drv_of "$PCI/$DGPU"
  [ -z "$DRV" ] || late_refuse refused:nodgpu "$DGPU got bound to '$(san "$DRV")' during the bind window"
  bar_ok || late_refuse refused:bar "$DGPU lost its BAR during the bind window"
  up_ms                                  # step 6
  TOKEN=$$-$UPMS
  log "bind-mark $TOKEN"
  T0=$UPMS
  _bd=$FLAGS/bind.$$                     # the background writer leaves its exit status here when the write returns
  rm -f "$_bd"
  BINDING=1                              # R-1: from here until the write is known to have returned (or bound)
  (
    printf '%s\n' "$DGPU" > "$R/sys/bus/pci/drivers/nouveau/bind"
    printf '%s\n' "$?" > "$_bd"
  ) > /dev/null 2>&1 &
  _bst=hang
  _i=0
  while [ $_i -lt $BIND_POLLS ]; do
    sleep 0.5
    _i=$((_i + 1))
    drv_of "$PCI/$DGPU"
    [ "$DRV" = nouveau ] && { _bst=bound; break; }
    if [ -e "$_bd" ]; then               # the write returned: one more look, then decide (the driver link of a
      sleep 0.5                          # probe that finished as the write returned)
      drv_of "$PCI/$DGPU"
      if [ "$DRV" = nouveau ]; then _bst=bound; else _bst=returned; fi
      break
    fi
  done
  up_ms
  T1=$UPMS
  _wst=
  [ -r "$_bd" ] && read -r _wst < "$_bd"
  rm -f "$_bd"
  [ $_bst = hang ] || BINDING=0
  if [ $_bst = hang ]; then              # the probe hangs in the kernel: no further dGPU write this boot
    flag_set wedged
    flag_set keep
    flag_set noload
    sp persist.vendor.gb.gfx_mode intel
    log "bind: the bind write did not return within 20 s: dGPU wedged; the marker stays (a later freeze ends in the crash-loop path)"
    result bind-failed
    notify "Restart required" "The NVIDIA GPU stopped responding. Restart before detaching the screen."
    finish_window
    set_booted
    exit 1
  fi
  if [ $_bst = returned ]; then
    log "bind: the bind write returned (status $(san "$_wst" 8)) and $DGPU is not bound"
    kernel_lines
    dmesg_hit
    unhealthy bind
  fi
  log "bind: $DGPU bound in $((T1 - T0)) ms"
  apply_perms                            # step 7
  health_check
  [ -n "$WHY" ] && unhealthy "$WHY"
  apply_perms                            # R-10: again right before the window closes (nodes created meanwhile)
  record_load healthy
  result healthy
  finish_window
  set_booted
  clean_mark 0                           # step 8
}

do_auto() {
  hw_native || { result refused:hw; exit 1; }                       # 1 (no booted)
  [ -e "$FLAGS/wedged" ] && { result refused:wedged; exit 1; }     # 2
  parse_cmdline                                                     # 3
  if [ $KILL = 1 ]; then
    log "kill switch on the kernel command line: nouveau stays unloaded this boot"
    flag_set noload
    sp vendor.gb.nouveau.mode disabled
    result disabled
    exit 0
  fi
  if [ -e "$FLAGS/noload" ]; then        # 4: crash-loop / kill switch / unhealthy earlier this boot (root-only flag)
    log "auto: no load for the rest of this boot (noload)"
    result refused:noload
    exit 1
  fi
  if [ "$(gp sys.boot_completed)" != 1 ]; then   # 5, invariant 3: never before boot_completed (an early ctl.start)
    log "auto: refused before sys.boot_completed"
    result refused:boot
    exit 1
  fi
  read_clean
  if [ -e "$MARK" ] || [ -L "$MARK" ]; then      # 6: crash-loop marker (INTERFACES 5.1 v1.2)
    if marker_of_this_boot; then
      log "auto: the load marker belongs to another window of this boot ($MARKTXT): left alone"
    else
      log "crash-loop: the load marker survived an earlier boot ($MARKTXT): graphics set to Intel only"
      flag_set noload                    # a spoofed gfx_mode + booted=1 cannot start a load this boot
      rm -f "$MARK"
      sync
      sp persist.vendor.gb.gfx_mode intel
      write_clean 0
      sp vendor.gb.nouveau.mode "${CL_MODE:-default}"
      result crashloop
      notify "Switched to Intel graphics" "The NVIDIA driver did not start cleanly last time, so graphics were set to Intel only. To try again, choose Auto in Settings > System > Graphics."
      exit 0
    fi
  fi
  gfx_mode                                                          # 7
  if [ -n "$CL_MODE" ]; then MODE=$CL_MODE
  elif [ "$GFXM" = intel ]; then MODE=default
  else MODE=auto
  fi
  sp vendor.gb.nouveau.mode "$MODE"
  sp vendor.gb.nouveau.clean "$CLEAN"
  if [ "$MODE" = default ]; then
    log "auto: graphics mode intel: nouveau is not loaded"
    result idle
    set_booted
    exit 0
  fi
  log "auto: mode=$MODE gfx_mode=$GFXM clean_boots=$CLEAN"
  if [ -d "$R/sys/module/nouveau" ]; then   # 8: already loaded (review A2: every check above has run)
    DRV=
    if find_dgpu; then drv_of "$PCI/$DGPU"; fi
    if [ -n "$DGPU" ] && [ "$DRV" = nouveau ]; then
      log "auto: nouveau already loaded and bound to $DGPU"
      apply_perms bound                  # R-10: gb_power's 0666 is not lowered by an Auto <-> NVIDIA switch
      set_booted
      exit 0
    fi
    if [ -e "$FLAGS/keep" ]; then        # a driver this boot marked sick is never bound again (D20)
      log "auto: the driver started unhealthy this boot (keep): not binding it again"
      refuse refused:keep
    fi
    [ -n "$DGPU" ] && [ -z "$DRV" ] && do_bind   # the bind path exits
    if [ -n "$DGPU" ]; then
      log "auto: $DGPU is bound to '$(san "$DRV")', not nouveau"
    else
      log "auto: nouveau is loaded, no NVIDIA display-class function (base detached?)"
    fi
    refuse refused:nodgpu
  fi
  if [ "$MODE" = auto ] && [ "$CLEAN" -lt 3 ]; then
    PROBATION=1
    PROB_TXT="$((CLEAN + 1))/3"
    log "probation $PROB_TXT"
  fi

  i915_ok || refuse refused:i915                                    # 9
  find_dgpu || { log "guard: no NVIDIA display-class function (base detached?)"; refuse refused:nodgpu; }
  bar_ok || { log "guard: $DGPU has no BAR assigned"; refuse refused:bar; }
  if pay_mode; then
    MODDIR=$R$PAYMNT/lib/modules
  else
    MODDIR=$R/odm/lib/modules
    sp vendor.gb.dgpu.payload legacy
    [ -f "$MODDIR/nouveau.ko" ] || { log "guard: $MODDIR/nouveau.ko missing"; refuse refused:module; }
  fi

  detach_wait                                                       # 10
  open_window
  KREL=$(uname -r 2>/dev/null)                                      # 11
  if pay_mode; then
    stage_payload || refuse refused:payload
  fi
  build_args
  sp vendor.gb.nouveau.argv "$ARGV"
  log "nouveau arguments: $ARGV"

  printf '%s\n' "$GB_PANIC_TIMEOUT" > "$R/proc/sys/kernel/panic" 2>/dev/null || log "cannot set kernel.panic"
  if [ "$MODE" = test ]; then
    printf '0\n' > "$R/proc/sys/kernel/panic_on_oops" 2>/dev/null || log "cannot clear kernel.panic_on_oops"
  fi
  if [ "$MODE" = test ] || [ "$PROBATION" = 1 ]; then sp vendor.gb.dgpu.dmesg 1; fi

  esp_mounted && refuse refused:esp                                 # 12
  bootseq
  mem_avail
  MEM_BEFORE=$MEMA
  write_marker "$MODE" || refuse refused:marker   # no foreign here (v1.2: gb_power sets it before nvidia, 23.3)

  [ -e "$FLAGS/detaching" ] && late_refuse refused:detach "a detach started during the load window: not loading"
  for _m in $DEPS; do
    _n=$_m
    [ "$_m" = gpu-sched ] && _n=gpu_sched
    [ -d "$R/sys/module/$_n" ] && continue
    insmod "$MODDIR/$_m.ko" || insmod_failed "$_m" "$?"
    printf '%s\n' "$_n" >> "$FLAGS/helpers"
  done
  # right before the probe: no detach under way, the dGPU still there with its BAR (it may have been pulled)
  [ -e "$FLAGS/detaching" ] && late_refuse refused:detach "a detach started during the load window: not loading"
  find_dgpu || late_refuse refused:nodgpu "the NVIDIA function disappeared during the load window"
  bar_ok || late_refuse refused:bar "$DGPU lost its BAR during the load window"
  up_ms
  TOKEN=$$-$UPMS
  log "insmod-mark $TOKEN"
  T0=$UPMS
  # shellcheck disable=SC2086  # ARGV holds only whitelisted atoms (build_args); word splitting intended, set -f on
  insmod "$MODDIR/nouveau.ko" $ARGV || insmod_failed nouveau "$?"
  up_ms
  T1=$UPMS
  INSMOD_MS=$((T1 - T0))
  log "nouveau.ko loaded in $INSMOD_MS ms"
  apply_perms

  health_check
  [ -n "$WHY" ] && unhealthy "$WHY"
  apply_perms                            # R-10: again right before the window closes (nodes created meanwhile)
  record_load healthy
  result healthy
  finish_window
  set_booted
  [ "$MODE" = test ] && sp vendor.gb.dgpu_report 2
  clean_mark 1                           # 18: +120 s after the insmod returned
}

# ---------------------------------------------------------------- off (INTERFACES 7.3)

wait_for_loader() {                      # do not race a running load window: ESP lock held by a live gb_nouveau, or (R-2)
  _w=0                                   # by gb_power's rebind guard while the kernel re-probes (INTERFACES 23.8)
  while [ -r "$LOCK/owner" ]; do
    _o=
    _nm=
    read -r _o _nm _ < "$LOCK/owner"
    case $_o in '' | *[!0-9]*) break ;; esac
    case $_nm in gb_nouveau | gb_power) ;; *) break ;; esac
    [ "$_o" != "$$" ] || break
    [ -d "$R/proc/$_o" ] || break
    if [ $_w -ge $LOADER_WAIT ]; then
      log "off: load window still open after ${LOADER_WAIT} s, continuing"
      break
    fi
    sleep 1
    _w=$((_w + 1))
  done
}

stop_gpu_users() {                       # INTERFACES 7.3 step 3: services that open the render node (root ones too:
  for _s in $GPU_USERS; do sp ctl.stop "$_s"; done   # chmod 000 does not stop root)
  _i=0
  while [ $_i -lt $SVC_WAIT ]; do
    _left=
    for _s in $GPU_USERS; do
      case $(gp "init.svc.$_s") in '' | stopped) ;; *) _left="$_left $_s" ;; esac
    done
    [ -n "$_left" ] || return 0
    sleep 0.5
    _i=$((_i + 1))
  done
  log "off:$_left did not stop within 5 s"
}

unbind_all() {                           # background unbind of every bound 10de function; 1 = deadline missed
  nv_funcs
  _list=
  for _b in $NVF; do [ -e "$PCI/$_b/driver" ] && _list="$_list $_b"; done
  [ -n "$_list" ] || return 0
  log "off: unbinding$_list"
  (
    for _b in $_list; do printf '%s\n' "$_b" > "$PCI/$_b/driver/unbind"; done
  ) > /dev/null 2>&1 &
  _bg=$!
  _i=0
  while [ $_i -lt $UNBIND_POLLS ]; do
    sleep 0.5
    _i=$((_i + 1))
    _left=0
    for _b in $_list; do [ -e "$PCI/$_b/driver" ] && _left=1; done
    if [ $_left = 0 ]; then
      wait "$_bg" 2>/dev/null
      return 0
    fi
  done
  return 1
}

rmmod_nouveau() {                        # R-11: rmmod in the background with a 20 s deadline; RMST = ok|failed|hang
  _rs=$FLAGS/rmmod.$$
  rm -f "$_rs"
  (
    rmmod nouveau
    printf '%s\n' "$?" > "$_rs"
  ) > /dev/null 2>&1 &
  _rbg=$!
  RMST=hang
  _i=0
  while [ $_i -lt $RMMOD_POLLS ]; do
    if [ -s "$_rs" ]; then               # (one short write: non-empty = complete)
      _st=
      read -r _st < "$_rs"
      rm -f "$_rs"
      wait "$_rbg" 2>/dev/null
      if [ "$_st" = 0 ]; then RMST=ok; return 0; fi
      RMST=failed
      return 1
    fi
    sleep 0.5
    _i=$((_i + 1))
  done
  return 1
}

do_off() {
  hw_native || { result refused:hw; exit 1; }
  [ -e "$FLAGS/wedged" ] && { result off-wedged; exit 1; }
  parse_cmdline
  if [ "$CL_MODE" = once ] || [ "$CL_MODE" = test ]; then
    if [ "$(gp init.svc.gb_nouveau)" = running ]; then
      log "off ignored (gb.nouveau=$CL_MODE boot; the load is still running)"
    else
      result off-ignored
    fi
    exit 0
  fi
  gfx_mode                               # v1.2 (INTERFACES 7.3): off acts only in Intel mode; the user may have
  if [ "$GFXM" != intel ]; then          # switched back before this run started
    log "off skipped: graphics mode is $GFXM"
    result off-skipped
    exit 0
  fi
  wait_for_loader
  if [ ! -d "$R/sys/module/nouveau" ]; then
    ctl_auto
    result off-done
    exit 0
  fi
  if [ -e "$FLAGS/keep" ]; then
    log "off skipped: the driver started unhealthy this boot and stays loaded until restart (D20)"
    exit 0
  fi
  stop_gpu_users
  nv_nodes
  for _n in $NODES; do chmod 000 "$R/dev/dri/$_n" 2>/dev/null; done
  sleep 1
  rd "$R/sys/module/nouveau/refcnt"
  _rc=$V
  case $_rc in '' | *[!0-9]*) _rc=unknown ;; esac
  gfx_mode                               # the same check again right after the refcnt read (INTERFACES 7.3)
  if [ "$GFXM" != intel ]; then
    apply_perms
    log "off skipped: graphics mode changed to $GFXM during the off window"
    result off-skipped
    exit 0
  fi
  if [ "$_rc" != 0 ]; then
    apply_perms
    holders
    _nb=$HN
    [ "$_nb" -lt 1 ] && _nb=1
    [ "$_nb" -gt 999 ] && _nb=999
    log "off refused: refcnt=$_rc holders=${HOLDERS:-none found}"
    result "off-busy:$_nb"
    notify "NVIDIA GPU is in use" "Close ${HNAMES:-the apps using it} to finish switching to Intel graphics."
    exit 1
  fi
  if ! unbind_all; then
    flag_set wedged
    log "off: unbind did not finish within 20 s: dGPU wedged, no further dGPU writes this boot"
    result off-wedged
    notify "Restart required" "The NVIDIA GPU stopped responding. Restart before detaching the screen."
    exit 1
  fi
  if foreign; then                       # v1.2: no N04 (off runs only in Intel mode; the dGPU ends unbound in D3cold)
    log "off: nouveau buffers were shared with another device this boot: module stays loaded until restart"
    flag_set off                         # informational (gb_power's reconcile_off() keys on the mode, 23.6)
    result off-unbound
  elif rmmod_nouveau; then
    if [ -r "$FLAGS/helpers" ]; then     # only the helpers this script loaded (never i915's or the VM's)
      for _n in drm_ttm_helper gpu_sched drm_gpuvm drm_exec; do
        grep -qx "$_n" "$FLAGS/helpers" 2>/dev/null || continue
        [ -d "$R/sys/module/$_n" ] || continue
        rmmod "$_n" 2>/dev/null || log "off: $_n in use, kept"
      done
    fi
    unmount_payload
    log "off: nouveau unloaded"
    result off-done
  elif [ "$RMST" = hang ]; then
    flag_set wedged
    log "off: rmmod nouveau did not return within 20 s: dGPU wedged, no further dGPU writes this boot"
    result off-wedged
    notify "Restart required" "The NVIDIA GPU stopped responding. Restart before detaching the screen."
    exit 1
  else
    log "off: rmmod nouveau failed; the driver is unbound, the module stays loaded"
    flag_set off
    result off-unbound
  fi
  ctl_auto
  exit 0
}

# ---------------------------------------------------------------- dmesg-stream (INTERFACES 7.6, D21)

do_dmesg_stream() {
  hw_native || exit 1
  [ -d "$REP" ] || exit 1
  _lf=$REP/dmesg-live.txt
  rd "$R/proc/sys/kernel/random/boot_id"
  _hdr="== gb_dgpu_dmesg boot_id=$V"
  if [ -f "$_lf" ]; then
    rd "$_lf"
    if [ "$V" != "$_hdr" ]; then         # a file from an earlier boot: keep it as .prev (evidence of a freeze)
      mv -f "$_lf" "$REP/dmesg-live.prev.txt"
      repmode "$REP/dmesg-live.prev.txt"
    else
      log "dmesg-stream: restarted in the same boot, appending"
    fi
  fi
  if [ ! -f "$_lf" ]; then
    printf '%s\n' "$_hdr" > "$_lf"
    repmode "$_lf"
  fi
  _sz=$(($(wc -c < "$_lf" 2>/dev/null) + 0))
  if [ "$_sz" -ge $DMESG_CAP ]; then
    log "dmesg-stream: dmesg-live.txt is already at the cap"
    exit 0
  fi
  dmesg -w >> "$_lf" 2>/dev/null &
  _dp=$!
  trap 'kill "$_dp" 2>/dev/null; sync; exit 0' HUP INT TERM
  log "dmesg-stream: started"
  while :; do
    sleep 5
    sync
    _sz=$(($(wc -c < "$_lf" 2>/dev/null) + 0))
    if [ "$_sz" -ge $DMESG_CAP ]; then
      kill "$_dp" 2>/dev/null
      log "dmesg-stream: 16 MiB cap reached, stream stopped"
      break
    fi
    kill -0 "$_dp" 2>/dev/null || { log "dmesg-stream: dmesg -w exited"; break; }
  done
  sync
  exit 0
}

# ---------------------------------------------------------------- main

case $CMD in
  auto)
    trap '_x=$?; finish_window; exit $_x' EXIT   # (mksh: keep the exit status of an exit inside a signal trap)
    trap 'on_signal' HUP INT TERM
    do_auto
    ;;
  off) do_off ;;
  dmesg-stream) do_dmesg_stream ;;
  *)
    printf 'usage: gb_nouveau.sh auto|off|dmesg-stream\n' >&2
    exit 2
    ;;
esac
