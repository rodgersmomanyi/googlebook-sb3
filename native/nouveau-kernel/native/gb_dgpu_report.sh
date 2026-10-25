#!/system/bin/sh
# gb_dgpu_report.sh -- dGPU report (INTERFACES.md v1.2 sections 7.10 and 25.3, plan Phase 2A/2B). Runs as root in
# u:r:shell:s0.
#   gb_dgpu_report.sh report    service gb_dgpu_report (vendor.gb.dgpu_report=1: gb_bootlog at t=40, gb_power
#                               "report", the user). Read-only: never writes sysfs, never opens a DRM node. Writes
#                               /data/vendor/gb/report/dgpu-<N>.txt (N = bootseq), keeps the newest 20.
#   gb_dgpu_report.sh test      service gb_dgpu_test (vendor.gb.dgpu_report=2, set by gb_nouveau.sh in
#                               gb.nouveau=test boots). Refused (exit 1, nothing done) unless the kernel command line
#                               says gb.nouveau=test and nouveau drives the dGPU. Phase 2B steps 2-7 ->
#                               report/test-<N>.txt.
#   gb_dgpu_report.sh selftest  service gb_gfx_selftest (v1.2, started only by gb_power, INTERFACES 25.2): the
#                               automatic NVIDIA app-graphics self-test. nvk_probe -t ahb-i915-readback and
#                               -t a17-swapchain-model inside the ESP lock, with the crash marker selftest/running;
#                               verdict -> /data/vendor/gb/selftest/result (the record gb_power validates, 25.4),
#                               full output -> report/selftest-<N>.txt. Never writes sysfs (the wakelock it
#                               holds for the whole run, R-7, is /sys/power/wake_lock, not a dGPU attribute).
# Note: "gb_power -o" (power samples) refreshes the test_power battery bridge exactly like the gb_power daemon does;
# that is gb_power's own write, not one made by this script.
#
# Test hooks: GB_ROOT (path prefix) and GB_TEST_PATH (prepended to PATH), as in gb_nouveau.sh.

LC_ALL=C
export LC_ALL
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin
export PATH
R=${GB_ROOT:-}
set -f
umask 077

GB=$R/data/vendor/gb
REP=$GB/report
FLAGS=$R/dev/gb_dgpu
LOCK=$R/dev/gb_esp.lock
PCI=$R/sys/bus/pci/devices
CMD=${1:-}
V=
LOCKED=0
OWNLINE=
BUSY=0
ASD=
NQ=0
OUT=/dev/null
ST=$GB/selftest              # v1.2 self-test state (0700 root, INTERFACES 5.1)
STMARK=$ST/running
STREC=$ST/result
GFXID=$R/odm/etc/gb/gfx_build_id
ST_PROBE_T=150               # s: timeout around each nvk_probe run (INTERFACES 2.3)
ST_PROBE_TT=60               # s: nvk_probe -T
PROBE_PID=
PROBE_WAIT=12                # x 0.5 s: after a stop signal, wait this long for nvk_probe to exit (R-11)
WAKE=gb_gfx_selftest         # wakelock held for the whole self-test (R-7)
WAKED=0
STMARKED=0
ST_DONE=0
BSEQ=0
P_ATT=0
P_CRASH=0

# ---------------------------------------------------------------- helpers (same conventions as gb_nouveau.sh)

rd() {
  V=
  [ -r "$1" ] || return 1
  IFS= read -r V < "$1" || [ -n "$V" ]
}

lsdir() {                                # LS = basenames in directory $1, sorted (C locale); the only glob
  LS=
  set +f
  for _ls_e in "$1"/*; do
    [ -e "$_ls_e" ] || [ -L "$_ls_e" ] || continue
    LS="$LS ${_ls_e##*/}"
  done
  set -f
}

up_ms() {
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

san() { printf '%s' "$1" | tr -cd ' -~' | cut -c1-"${2:-32}"; }

log() { printf 'gb_dgpu_report: %s\n' "$*" >> "$R/dev/kmsg" 2>/dev/null; return 0; }

out() { printf '%s\n' "$*" >> "$OUT"; }

gp() { getprop "$1" 2>/dev/null; }

sp() { setprop "$1" "$2" 2>/dev/null || log "setprop $1 failed"; }

repmode() {
  chmod 0640 "$1" 2>/dev/null
  chown root:shell "$1" 2>/dev/null
}

hw_native() { rd "$R/odm/etc/gb_hw" && [ "$V" = native ]; }

drv_of() {
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

cl_nouveau() {                           # CL_NV = last gb.nouveau= value of the kernel command line
  CL_NV=
  _cl=
  rd "$R/proc/cmdline" && _cl=$V
  for _a in $_cl; do
    case $_a in gb.nouveau=*) CL_NV=${_a#gb.nouveau=} ;; esac
  done
}

nv_funcs() {
  NVF=
  lsdir "$PCI"
  for _b in $LS; do
    case $_b in *[!0-9a-f:.]*) continue ;; esac
    rd "$PCI/$_b/vendor" && [ "$V" = 0x10de ] && NVF="$NVF $_b"
  done
}

find_dgpu() {
  DGPU=
  nv_funcs
  for _b in $NVF; do
    rd "$PCI/$_b/class"
    case $V in 0x03*) DGPU=$_b; return 0 ;; esac
  done
  return 1
}

bootseq() {                              # identical to gb_nouveau.sh (INTERFACES 5.1)
  _f=$GB/bootseq
  _lk=$FLAGS/bootseq.lock
  rd "$R/proc/sys/kernel/random/boot_id"
  _id=$V
  _i=0
  while ! mkdir "$_lk" 2>/dev/null; do
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
  rmdir "$_lk" 2>/dev/null
  BSEQ=$_n
}

prune() {
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

qwrite() {                               # stdin -> $1 in notify/ (group-shell writable): see gb_nouveau.sh qwrite
  _qt=$1.tmp
  if ! (umask 022 && set -C && cat > "$_qt") 2>/dev/null; then
    return 1
  fi
  mv -f -T "$_qt" "$1" 2>/dev/null || { rm -f "$_qt"; return 1; }
}

notify() {                               # $1 title, $2 text, $3 tag (INTERFACES 12.1)
  _q=$GB/notify
  [ -d "$_q" ] || { log "notify queue missing, dropped: $1"; return 1; }
  _ms=$(date +%s%N 2>/dev/null | cut -c1-13)
  case $_ms in
    [1-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) _ms=$(($(date +%s) * 1000)) ;;
  esac
  NQ=$((NQ + 1))
  _qf=$_q/$_ms-$$-$NQ
  if printf 'kind=notify\ntag=%s\ntitle=%s\ntext=%s\n' "${3:-gb_dgpu}" "$(san "$1" 40)" "$(san "$2" 200)" |
    qwrite "$_qf.msg"; then
    sp vendor.gb.notify "$_ms"
  fi
}

steal_lock() {                           # INTERFACES 5.4 (same algorithm as gb_nouveau.sh steal_lock)
  _st=$LOCK.stale.$$
  rm -rf "$_st" 2>/dev/null
  mv -T "$LOCK" "$_st" 2>/dev/null || return 1
  _ol2=
  [ -r "$_st/owner" ] && IFS= read -r _ol2 < "$_st/owner"
  if [ "$(stat -c %i "$_st" 2>/dev/null)" = "$2" ] && [ "$_ol2" = "$1" ]; then
    log "ESP lock: stealing a stale lock (owner '$(san "$1" 60)')"
    rm -f "$_st/owner"
    rmdir "$_st" 2>/dev/null
    return 0
  fi
  mv -T "$_st" "$LOCK" 2>/dev/null || log "ESP lock: the lock changed while stealing it and could not be put back"
  return 1
}

lock_esp() {                             # INTERFACES 5.4 (same algorithm as gb_nouveau.sh)
  _w=0
  _miss=0
  while :; do
    mkdir "$LOCK" 2>/dev/null && break
    _ino=$(stat -c %i "$LOCK" 2>/dev/null)
    _ol=
    [ -r "$LOCK/owner" ] && IFS= read -r _ol < "$LOCK/owner"
    _o=${_ol%% *}
    _stale=0
    if [ -n "$_ol" ]; then
      _miss=0
      case $_o in '' | *[!0-9]*) _stale=1 ;; *) [ -d "$R/proc/$_o" ] || _stale=1 ;; esac
    else
      _miss=$((_miss + 1))
      [ $_miss -ge 5 ] && _stale=1
    fi
    if [ $_stale = 1 ] && [ -n "$_ino" ]; then
      _miss=0
      steal_lock "$_ol" "$_ino" && continue
    fi
    [ $_w -ge "$1" ] && return 1
    sleep 1
    _w=$((_w + 1))
  done
  up_ms
  OWNLINE="$$ gb_dgpu_report $UPS"
  printf '%s\n' "$OWNLINE" > "$LOCK/owner"
  LOCKED=1
  return 0
}

unlock_esp() {                           # only our own lock
  [ "$LOCKED" = 1 ] || return 0
  _ol=
  [ -r "$LOCK/owner" ] && IFS= read -r _ol < "$LOCK/owner"
  if [ "$_ol" = "$OWNLINE" ]; then
    rm -f "$LOCK/owner"
    rmdir "$LOCK" 2>/dev/null
  fi
  LOCKED=0
}

detaching() {                            # gb_power handles a detach: no new nvk_probe run (gb_power also stops us)
  [ -e "$FLAGS/detaching" ]
}

kmark() {                                # writes a kmsg marker; KMARK = its text
  up_ms
  KMARK="$1-mark $$-$UPMS"
  log "$KMARK"
}

klines_since() {                         # kernel lines after the marker $1 (all if it scrolled out)
  _all=$(dmesg 2>/dev/null)
  _since=$(printf '%s\n' "$_all" | sed -n "/gb_dgpu_report: $1\$/,\$p")
  [ -n "$_since" ] || _since=$_all
  printf '%s\n' "$_since" | grep -vE '(gb_[a-z_]+|init): '
}

power_sample() {                         # one "gb_power -o" reading
  _p=$(timeout 15 gb_power -o 2>/dev/null | sed -n 's/.*\(ac=[0-9-]*\).*\(net_uW=[0-9-]*\).*/\1 \2/p' | head -n 1)
  up_ms
  printf 't=%s %s\n' "$UPS" "${_p:-no-reading}"
}

pci_line() {                             # cached status attributes only (invariant 12)
  _d=$PCI/$1
  [ -e "$_d" ] || { printf '%s absent\n' "$1"; return 0; }
  rd "$_d/vendor"
  _ve=$V
  rd "$_d/device"
  _de=$V
  rd "$_d/class"
  _cl=$V
  drv_of "$_d"
  rd "$_d/power/control"
  _co=$V
  rd "$_d/power/runtime_status"
  _rs=$V
  rd "$_d/power_state"
  _ps=$V
  rd "$_d/power/runtime_active_time"
  _ra=$V
  rd "$_d/power/runtime_suspended_time"
  _rsu=$V
  _ig=$(readlink "$_d/iommu_group" 2>/dev/null)
  _ig=${_ig##*/}
  lsdir "$_d/iommu_group/devices"
  _im=${LS# }
  printf '%s vendor=%s device=%s class=%s driver=%s control=%s runtime_status=%s power_state=%s' \
    "$1" "$_ve" "$_de" "$_cl" "${DRV:-none}" "$_co" "$_rs" "$_ps"
  printf ' active_ms=%s suspended_ms=%s iommu_group=%s members=%s\n' "$_ra" "$_rsu" "${_ig:--}" "$_im"
}

# ---------------------------------------------------------------- report (read-only)

report_body() {
  up_ms
  printf '== gb_dgpu_report report n=%s uptime_s=%s\n' "$BSEQ" "$UPS"
  printf '== build\n'
  printf 'ro.build.id=%s\n' "$(gp ro.build.id)"
  printf 'uname_r=%s\n' "$(uname -r 2>/dev/null)"
  rd "$R/proc/cmdline"
  printf 'cmdline=%s\n' "$V"
  printf 'getenforce=%s\n' "$(getenforce 2>/dev/null)"

  printf '== drm\n'
  lsdir "$R/sys/class/drm"
  for _n in $LS; do
    case $_n in card*) _k=${_n#card} ;; renderD*) _k=${_n#renderD} ;; *) continue ;; esac
    case $_k in '' | *[!0-9]*) continue ;; esac
    rd "$R/sys/class/drm/$_n/device/vendor"
    drv_of "$R/sys/class/drm/$_n/device"
    printf '%s -> %s %s\n' "$_n" "${V:-?}" "${DRV:-none}"
  done

  printf '== vulkan\n'
  timeout 30 cmd gpu vkjson 2>/dev/null | grep -o '"deviceName": *"[^"]*"' | sed 's/.*: *"\(.*\)"/\1/' | head -n 8
  printf '== renderengine\n'
  timeout 30 dumpsys SurfaceFlinger 2>/dev/null | grep -iE 'renderengine|vulkan|gles' | head -n 10

  printf '== pci\n'
  find_dgpu
  _port=
  if [ -n "$DGPU" ]; then
    _rp=$(readlink -f "$PCI/$DGPU" 2>/dev/null)
    _rp=${_rp%/*}
    _port=${_rp##*/}
  fi
  case $_port in [0-9a-f][0-9a-f][0-9a-f][0-9a-f]:*) ;; *) _port=0000:00:1d.4 ;; esac
  [ -n "$NVF" ] || printf 'no NVIDIA function present (base detached, or no NVIDIA hardware)\n'
  for _b in $NVF $_port; do pci_line "$_b"; done
  rd "$R/sys/module/nouveau/refcnt" || V=-
  printf 'nouveau refcnt=%s\n' "$V"

  printf '== acpi\n'
  lsdir "$R/sys/firmware/acpi/tables"
  printf 'tables:%s\n' "$LS"
  lsdir "$R/sys/firmware/acpi/tables/dynamic"
  printf 'dynamic:%s\n' "$LS"

  printf '== usb\n'
  lsdir "$R/sys/bus/usb/devices"
  for _u in $LS; do
    _ud=$R/sys/bus/usb/devices/$_u
    rd "$_ud/idVendor" || continue
    _iv=$V
    rd "$_ud/idProduct"
    _ip=$V
    rd "$_ud/speed"
    _usp=$V
    rd "$_ud/product"
    printf '%s %s:%s speed=%s product=%s\n' "$_u" "$_iv" "$_ip" "$_usp" "$(san "$V" 40)"
  done
  printf -- '-- mounted block devices (sysfs path)\n'
  if [ -r "$R/proc/mounts" ]; then
    while read -r _src _mp _ty _; do
      case $_src in /dev/block/*) ;; *) continue ;; esac
      _dv=$(readlink -f "$R$_src" 2>/dev/null)
      _sy=$(readlink -f "$R/sys/class/block/${_dv##*/}" 2>/dev/null)
      printf '%s %s %s -> %s\n' "$_mp" "$_src" "$_ty" "${_sy#"$R"}"
    done < "$R/proc/mounts"
  fi

  printf '== asound\n'
  cat "$R/proc/asound/cards" 2>/dev/null

  printf '== firmware\n'
  _man=$R/odm/etc/gb/tu117.sha256
  if [ -r "$_man" ]; then
    _ok=0
    _bad=0
    _mis=0
    while read -r _h _p; do
      _p=${_p#\*}
      case $_p in nvidia/tu117/*) ;; *) continue ;; esac
      case $_p in *..*) continue ;; esac
      if [ ! -f "$R/odm/firmware/$_p" ]; then
        _mis=$((_mis + 1))
        continue
      fi
      _s=$(sha256sum "$R/odm/firmware/$_p" 2>/dev/null)
      if [ "${_s%% *}" = "$_h" ]; then _ok=$((_ok + 1)); else _bad=$((_bad + 1)); printf 'MISMATCH %s\n' "$_p"; fi
    done < "$_man"
    printf 'firmware /odm/firmware/nvidia payload=%s ok=%s mismatch=%s missing=%s\n' \
      "$(gp vendor.gb.dgpu.payload)" "$_ok" "$_bad" "$_mis"
  else
    printf 'manifest /odm/etc/gb/tu117.sha256 missing\n'
  fi

  printf '== power\n'
  cl_nouveau
  if [ "$CL_NV" = test ]; then
    printf 'skipped (gb.nouveau=test boot: the test sequence samples power)\n'
  else
    _i=0
    while [ $_i -lt 30 ]; do
      power_sample
      _i=$((_i + 1))
      [ $_i -lt 30 ] && sleep 10
    done
  fi

  printf '== dtx\n'
  for _p in vendor.gb.dtx.mode vendor.gb.dtx.base vendor.gb.dtx.latch vendor.gb.dgpu.latch; do
    printf '%s=%s\n' "$_p" "$(gp "$_p")"
  done

  printf '== props\n'
  getprop 2>/dev/null | grep 'vendor\.gb\.'
  printf '== end\n'
}

do_report() {
  hw_native || { log "report: not a native image"; exit 1; }
  [ -d "$REP" ] || { log "report: $REP missing"; exit 1; }
  bootseq
  _rf=$REP/dgpu-$BSEQ.txt
  report_body > "$_rf.tmp" 2>&1
  repmode "$_rf.tmp"
  mv -f "$_rf.tmp" "$_rf"
  prune dgpu
  log "report written: dgpu-$BSEQ.txt"
  exit 0
}

# ---------------------------------------------------------------- test sequence (Phase 2B steps 2-7)

# shellcheck disable=SC2317  # invoked from the EXIT trap
test_cleanup() {
  if [ -n "$ASD" ]; then printf '5000\n' > "$ASD" 2>/dev/null; ASD=; fi
  if [ "$BUSY" = 1 ]; then sp vendor.gb.dgpu.busy 0; BUSY=0; fi
  unlock_esp
}

phase_d3cold() {                         # step 2: D3cold for every 10de function within 30 s
  out "== d3cold (every NVIDIA function within 30 s)"
  for _b in $NVF; do
    _t=0
    while :; do
      rd "$PCI/$_b/power_state"
      [ "$V" = D3cold ] && break
      [ $_t -ge 30 ] && break
      sleep 1
      _t=$((_t + 1))
    done
    if [ "$V" = D3cold ]; then out "$_b d3cold_s=$_t"; else out "$_b d3cold=timeout power_state=$V"; fi
  done
}

phase_power() {                          # step 3: bound-idle power
  out "== bound-idle power (30 x gb_power -o, 10 s apart)"
  _i=0
  while [ $_i -lt 30 ]; do
    out "$(power_sample)"
    _i=$((_i + 1))
    [ $_i -lt 30 ] && sleep 10
  done
}

phase_probe() {                          # step 4: nvk_probe default run (anb modes import i915 buffers: foreign)
  out "== nvk_probe -t all -n 200"
  if detaching; then out "nvk_probe=skipped:detach"; return 0; fi
  sp vendor.gb.nouveau.foreign 1
  [ -d "$FLAGS" ] || { mkdir -p "$FLAGS" && chmod 0700 "$FLAGS"; }
  : > "$FLAGS/foreign"
  _tmp=$FLAGS/probe.$$
  timeout 300 nvk_probe -t all -n 200 > "$_tmp" 2>&1
  _rc=$?
  tail -n 80 "$_tmp" | tr -cd ' -~\n' >> "$OUT"
  rm -f "$_tmp"
  out "nvk_probe_exit=$_rc"
}

phase_stress() {                         # step 5: runtime-PM stress inside the ESP lock
  out "== pm stress (30 x nvk_probe -t offscreen -n 1, 3 s apart, autosuspend 500 ms)"
  if ! lock_esp 120; then out "pm_stress=skipped:esp"; return 0; fi
  sp vendor.gb.dgpu.busy 1
  BUSY=1
  _a=$PCI/$DGPU/power/autosuspend_delay_ms
  ASD=$_a
  printf '500\n' > "$_a" 2>/dev/null || out "cannot set autosuspend_delay_ms"
  kmark stress
  _mk=$KMARK
  _err=0
  _fail=0
  _i=0
  while [ $_i -lt 30 ]; do
    if detaching; then out "pm_stress=stopped:detach after $_i runs"; break; fi
    timeout 60 nvk_probe -t offscreen -n 1 > /dev/null 2>&1 || _fail=$((_fail + 1))
    sleep 3
    rd "$PCI/$DGPU/power/runtime_status"
    [ "$V" = error ] && _err=$((_err + 1))
    _i=$((_i + 1))
  done
  _k=$(klines_since "$_mk")
  _warn=$(printf '%s\n' "$_k" | grep -c 'WARNING:')
  _t110=$(printf '%s\n' "$_k" | grep -c -- '-110')
  printf '5000\n' > "$_a" 2>/dev/null
  ASD=
  sp vendor.gb.dgpu.busy 0
  BUSY=0
  unlock_esp
  out "pm_stress runs=30 probe_failures=$_fail runtime_status_error=$_err warnings=$_warn etimedout=$_t110"
}

phase_suspend() {                        # step 6: user-driven suspend test, 5 minutes
  out "== suspend test (5 min; N13 asks for 3 lid closes)"
  if ! lock_esp 120; then out "suspend_test=skipped:esp"; return 0; fi
  sp vendor.gb.dgpu.busy 1
  BUSY=1
  _ss=$R/sys/power/suspend_stats
  notify "NVIDIA test: suspend" "Close the lid for 30 s, then open it. Do this 3 times within 5 minutes." gb_dgpu_test
  rd "$_ss/success"
  _s0=${V:-0}
  _last=$_s0
  rd "$_ss/fail"
  out "suspend_stats before: success=$_s0 fail=${V:-?}"
  rd "$PCI/$DGPU/power/runtime_active_time"
  _a0=${V:-0}
  mem_avail
  out "mem_available_kb start=$MEMA"
  up_ms
  _end=$((UPMS + 300000))
  _min=$MEMA
  while :; do
    up_ms
    [ "$UPMS" -ge "$_end" ] && break
    sleep 2
    mem_avail
    case $MEMA in '' | *[!0-9]*) ;; *) [ -n "$_min" ] && [ "$MEMA" -lt "$_min" ] && _min=$MEMA ;; esac
    rd "$_ss/success"
    if [ -n "$V" ] && [ "$V" != "$_last" ]; then
      _last=$V
      _sv=$V
      rd "$PCI/$DGPU/power/runtime_active_time"
      up_ms
      out "resume success=$_sv t=$UPS mem_available_kb=$MEMA runtime_active_ms=$V"
    fi
  done
  rd "$_ss/success"
  _s1=${V:-0}
  rd "$_ss/fail"
  _f1=${V:-?}
  rd "$_ss/last_failed_dev"
  out "suspend_stats after: success=$_s1 fail=$_f1 last_failed_dev=$(san "$V" 60)"
  rd "$PCI/$DGPU/power/runtime_active_time"
  case $V$_a0 in '' | *[!0-9]*) _da='?' ;; *) _da=$((V - _a0)) ;; esac
  out "suspends_seen=$((_s1 - _s0)) runtime_active_ms_delta=$_da mem_available_kb_min=$_min"
  sp vendor.gb.dgpu.busy 0
  BUSY=0
  unlock_esp
}

do_test() {
  hw_native || { log "test: not a native image"; exit 1; }
  cl_nouveau
  if [ "$CL_NV" != test ]; then
    log "test: refused (not a gb.nouveau=test boot)"
    exit 1
  fi
  if ! find_dgpu; then log "test: refused (no NVIDIA function)"; exit 1; fi
  drv_of "$PCI/$DGPU"
  if [ "$DRV" != nouveau ] || [ ! -d "$R/sys/module/nouveau" ]; then
    log "test: refused (nouveau does not drive $DGPU)"
    exit 1
  fi
  [ -d "$REP" ] || { log "test: $REP missing"; exit 1; }
  trap 'test_cleanup' EXIT
  trap 'exit 1' HUP INT TERM
  bootseq
  OUT=$REP/test-$BSEQ.txt
  : > "$OUT"
  repmode "$OUT"
  prune test
  up_ms
  out "== gb_dgpu_report test n=$BSEQ uptime_s=$UPS dgpu=$DGPU krel=$(uname -r 2>/dev/null)"
  out "argv=$(gp vendor.gb.nouveau.argv) result=$(gp vendor.gb.nouveau.result)"
  log "test sequence started (report/test-$BSEQ.txt)"
  phase_d3cold
  sync
  phase_power
  sync
  phase_probe
  sync
  phase_stress
  sync
  phase_suspend
  sync
  notify "NVIDIA test finished" "Wait until 15 minutes after start-up, then restart. Results are in the gblog folder on the boot partition." gb_dgpu_test
  up_ms
  out "== end uptime_s=$UPS"
  sync
  log "test sequence finished"
  exit 0
}

# ---------------------------------------------------------------- selftest (v1.2, INTERFACES 25.3-25.5)

st_dir_ok() {                            # a real directory (no symlink) owned by this script's uid (root), mode 0700
  [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -c '%u %a' "$1" 2>/dev/null)" = "$(id -u 2>/dev/null) 700" ]
}

st_identity() {                          # ID_BUILD, ID_KREL, ID_GFX (INTERFACES 25.1)
  ID_BUILD="$(gp ro.build.id)/$(gp ro.build.version.incremental)"
  ID_KREL=$(uname -r 2>/dev/null)
  ID_GFX=
  rd "$GFXID" && ID_GFX=$V
}

st_prev() {                              # P_ATT, P_CRASH = attempts and crashes of the stored record when it is a
  P_ATT=0                                # plain root-owned file for this exact identity; 0 otherwise
  P_CRASH=0
  [ -f "$STREC" ] && [ ! -L "$STREC" ] || return 0
  [ "$(stat -c '%u %h' "$STREC" 2>/dev/null)" = "$(id -u 2>/dev/null) 1" ] || return 0
  [ "$(($(wc -c < "$STREC" 2>/dev/null) + 0))" -le 2048 ] || return 0
  _pb=
  _pk=
  _pg=
  _pa=
  _pc=
  while IFS= read -r _l || [ -n "$_l" ]; do
    case $_l in
      build=*) _pb=${_l#build=} ;;
      krel=*) _pk=${_l#krel=} ;;
      gfx=*) _pg=${_l#gfx=} ;;
      attempts=*) _pa=${_l#attempts=} ;;
      crashes=*) _pc=${_l#crashes=} ;;
    esac
  done < "$STREC"
  [ "$_pb" = "$ID_BUILD" ] && [ "$_pk" = "$ID_KREL" ] && [ "$_pg" = "$ID_GFX" ] || return 0
  case $_pa in [0-9] | [0-9][0-9] | [0-9][0-9][0-9]) P_ATT=$((_pa + 0)) ;; esac
  case $_pc in [0-9] | [0-9][0-9] | [0-9][0-9][0-9]) P_CRASH=$((_pc + 0)) ;; esac
  return 0
}

st_record() {                            # $1 verdict, $2 reason, $3 attempts: the record (INTERFACES 25.4) as .tmp
  _rr=$(printf '%s' "$2" | tr -cd 'a-z0-9 :._-' | cut -c1-40)   # (noclobber, 0644), sync, rename
  _rt=$STREC.tmp
  rm -f "$_rt"                           # selftest/ is root-only (checked before use): a leftover .tmp is ours
  if (umask 022 && set -C && printf 'verdict=%s\nreason=%s\nbuild=%s\nkrel=%s\ngfx=%s\nt=%s\nattempts=%s\ncrashes=%s\nreport=%s\n' \
    "$1" "$_rr" "$ID_BUILD" "$ID_KREL" "$ID_GFX" "$(date +%s 2>/dev/null)" "$3" "$P_CRASH" "selftest-$BSEQ.txt" \
    > "$_rt") 2>/dev/null; then
    sync
    if mv -f "$_rt" "$STREC"; then
      log "selftest: verdict=$1 reason=$_rr attempts=$3"
      out "== record verdict=$1 reason=$_rr attempts=$3 crashes=$P_CRASH"
      return 0
    fi
  fi
  rm -f "$_rt"
  log "selftest: cannot write $STREC"
  return 1
}

wake_lock() {                            # R-7: no system suspend in the middle of a probe (kernel wakeup source)
  [ "$WAKED" = 1 ] && return 0
  [ -w "$R/sys/power/wake_lock" ] || return 0
  printf '%s\n' "$WAKE" > "$R/sys/power/wake_lock" 2>/dev/null && WAKED=1
  return 0
}

# shellcheck disable=SC2317  # invoked from the EXIT and TERM traps (st_exit)
wake_unlock() {
  [ "$WAKED" = 1 ] || return 0
  printf '%s\n' "$WAKE" > "$R/sys/power/wake_unlock" 2>/dev/null
  WAKED=0
}

# shellcheck disable=SC2317  # invoked from the EXIT trap
st_exit() { unlock_esp; wake_unlock; }

st_unmark() {                            # remove our own crash marker (never one we did not create)
  [ "$STMARKED" = 1 ] || return 0
  rm -f "$STMARK"
  sync
  STMARKED=0
}

# shellcheck disable=SC2317  # invoked from the TERM/INT/HUP trap
st_interrupted() {                       # 25.3 step 9: ctl.stop with a gentle kill, shutdown
  trap '' HUP INT TERM
  [ -n "$PROBE_PID" ] && kill "$PROBE_PID" 2>/dev/null
  if [ "$ST_DONE" = 0 ]; then            # the record first: a SIGKILL during the wait below leaves no false crash
    out "== interrupted"
    st_record error interrupted "$P_ATT"
  fi
  if [ -n "$PROBE_PID" ]; then           # R-11: nvk_probe may still have nouveau open; wait (bounded) for it to go
    _pw=0                                # before the crash marker and the ESP lock are released. Shorter during a
    _pmax=$PROBE_WAIT                    # shutdown (init SIGKILLs the group after its grace time)
    [ -n "$(gp sys.powerctl)" ] && _pmax=2
    while [ "$_pw" -lt "$_pmax" ] && pgrep -x nvk_probe > /dev/null 2>&1; do
      sleep 0.5
      _pw=$((_pw + 1))
    done
    if [ "$_pw" -ge "$_pmax" ]; then log "selftest: nvk_probe still running after the stop: releasing anyway"
    elif [ "$_pw" -gt 0 ]; then log "selftest: nvk_probe exited after the stop"
    fi
  fi
  st_unmark
  st_exit
  exit 0
}

st_probe() {                             # $1 = nvk_probe mode; PRC = its exit status (124/137 = timeout)
  [ -d "$FLAGS" ] || { mkdir -p "$FLAGS" && chmod 0700 "$FLAGS"; }
  _tmp=$FLAGS/probe.$$
  rm -f "$_tmp"
  out "== nvk_probe -t $1 -T $ST_PROBE_TT"
  up_ms
  _pt0=$UPMS
  timeout "$ST_PROBE_T" nvk_probe -t "$1" -T "$ST_PROBE_TT" > "$_tmp" 2>&1 &
  PROBE_PID=$!
  wait "$PROBE_PID"                      # in the background, so the TERM trap runs at once (a foreground child
  PRC=$?                                 # would delay it until the probe ends)
  PROBE_PID=
  up_ms
  tail -n 200 "$_tmp" 2>/dev/null | tr -cd ' -~\n' >> "$OUT"
  rm -f "$_tmp"
  out "nvk_probe_exit=$PRC ms=$((UPMS - _pt0))"
}

st_map() {                               # $1 mode, $2 exit status -> VK (pass|fail|error), VR (nvk_probe.c:29-30)
  case $2 in
    0) VK=pass VR=ok ;;
    1) VK=fail VR=$1 ;;
    124 | 137) VK=fail VR="$1 timeout" ;;
    2) VK=error VR=icd ;;
    3) VK=error VR=nodevice ;;
    *) VK=fail VR="$1 rc $2" ;;
  esac
}

do_selftest() {
  # step 1: refusals (exit 1, no file written)
  hw_native || { log "selftest: refused (not a native image)"; exit 1; }
  cl_nouveau
  [ "$CL_NV" = test ] && { log "selftest: refused (gb.nouveau=test boot)"; exit 1; }
  find_dgpu || { log "selftest: refused (no NVIDIA function)"; exit 1; }
  drv_of "$PCI/$DGPU"
  if [ "$DRV" != nouveau ] || [ ! -d "$R/sys/module/nouveau" ]; then
    log "selftest: refused (nouveau does not drive $DGPU)"
    exit 1
  fi
  st_dir_ok "$ST" || { log "selftest: refused ($ST is not a root-only 0700 directory)"; exit 1; }
  detaching && { log "selftest: refused (a detach is under way)"; exit 1; }
  if ! rd "$GFXID" || [ -z "$V" ]; then
    log "selftest: refused ($GFXID missing: the driver package is not configured)"
    exit 1
  fi
  [ -d "$REP" ] || { log "selftest: refused ($REP missing)"; exit 1; }
  st_identity
  st_prev
  trap 'st_interrupted' HUP INT TERM
  trap 'st_exit' EXIT
  bootseq                                # step 5 (before the lock: the esp verdict names its report too, NP-1B-D6)
  OUT=$REP/selftest-$BSEQ.txt
  : > "$OUT"
  repmode "$OUT"
  prune selftest
  up_ms
  out "== gb_dgpu_report selftest n=$BSEQ uptime_s=$UPS dgpu=$DGPU"
  out "build=$ID_BUILD"
  out "krel=$ID_KREL"
  out "gfx=$(san "$ID_GFX" 600)"
  out "uname_r=$(uname -r 2>/dev/null)"
  out "argv=$(gp vendor.gb.nouveau.argv) result=$(gp vendor.gb.nouveau.result)"
  out "previous attempts=$P_ATT crashes=$P_CRASH"
  if ! lock_esp 120; then                # step 2
    out "== ESP lock busy for 120 s"
    ST_DONE=1
    st_record error esp "$P_ATT"
    exit 0
  fi
  _bid=                                  # step 3: crash marker (noclobber; selftest/ is root-only 0700)
  rd "$R/proc/sys/kernel/random/boot_id" && _bid=$V
  if ! (set -C && printf 'boot=%s t=%s\n' "$_bid" "$(date +%s 2>/dev/null)" > "$STMARK") 2>/dev/null; then
    out "== a crash marker is already there: left for gb_power (INTERFACES 25.5)"
    log "selftest: $STMARK already exists: not running"
    ST_DONE=1
    unlock_esp
    exit 1
  fi
  STMARKED=1
  sync
  wake_lock                              # R-7: held until the script exits (EXIT trap)
  sp vendor.gb.nouveau.foreign 1         # step 4: both modes import i915 buffers into nouveau (as phase_probe)
  : > "$FLAGS/foreign"
  log "selftest started (report/selftest-$BSEQ.txt, attempt $((P_ATT + 1)))"
  if detaching; then                     # step 6
    out "== detach under way before the first probe"
    ST_DONE=1
    st_record error interrupted "$P_ATT"
    st_unmark
    unlock_esp
    exit 0
  fi
  st_probe ahb-i915-readback
  _r1=$PRC
  if detaching; then
    out "== detach under way after the first probe"
    ST_DONE=1
    st_record error interrupted "$P_ATT"
    st_unmark
    unlock_esp
    exit 0
  fi
  st_probe a17-swapchain-model
  _r2=$PRC
  st_map ahb-i915-readback "$_r1"        # step 7
  _v1=$VK
  _w1=$VR
  st_map a17-swapchain-model "$_r2"
  _v2=$VK
  _w2=$VR
  if [ "$_v1" = pass ] && [ "$_v2" = pass ]; then _vk=pass _vr=ok
  elif [ "$_v1" = fail ]; then _vk=fail _vr=$_w1
  elif [ "$_v2" = fail ]; then _vk=fail _vr=$_w2
  elif [ "$_v1" = error ]; then _vk=error _vr=$_w1
  else _vk=error _vr=$_w2
  fi
  ST_DONE=1                              # step 8
  st_record "$_vk" "$_vr" $((P_ATT + 1))
  st_unmark
  unlock_esp
  up_ms
  out "== end uptime_s=$UPS"
  sync
  exit 0
}

case $CMD in
  report) do_report ;;
  test) do_test ;;
  selftest) do_selftest ;;
  *)
    printf 'usage: gb_dgpu_report.sh report|test|selftest\n' >&2
    exit 2
    ;;
esac
