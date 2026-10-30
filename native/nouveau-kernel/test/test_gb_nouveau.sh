#!/bin/sh
# test_gb_nouveau.sh -- acceptance tests NP-01..NP-30 (INTERFACES.md 18.1) and, for v1.2 (graphics switching),
# NP-1B-01..NP-1B-25 (1b\TASKS_1B.md v1.2 section 1.2) and the review fix pass NP-R1..NP-R11 (TASKS_1B section 12)
# for gb_nouveau.sh, gb_dgpu_report.sh, gb_notify.sh and gb_nouveau.rc, run against a fake device tree (GB_ROOT) with
# PATH shims (GB_TEST_PATH) and a fake clock. No root, no device, no network. The wave-1 cases carry the v1.2 rewrites of TASKS_1B.md section 9 (marked "v1.2").
#
#   mksh test/test_gb_nouveau.sh [work_dir]
#   env: GB_TEST_SH (interpreter for the scripts under test, default mksh), GB_INTERFACES (path to INTERFACES.md
#        v1.2: enables the exact 15.1-v12 rc comparison), GB_TEST_ONLY (space-separated case-name prefixes to run),
#        GB_TEST_TOOLS (directory searched after the shims and before /usr/bin: e.g. toybox applets + BWK awk,
#        to run the scripts against Android-like tools instead of GNU coreutils; see run_tests.sh).
# Output: one "ok"/"FAIL" line per assertion, then "RESULT pass=<n> fail=<n>"; exit 0 only if fail=0.

LC_ALL=C
export LC_ALL
HERE=$(cd "$(dirname "$0")" && pwd)
NAT=$(cd "$HERE/../native" && pwd)
WORK=${1:-${TMPDIR:-/tmp}/gbnp-test.$$}
SH=${GB_TEST_SH:-mksh}
SHIM=$WORK/shim
KREL=6.18.21-android17-5-gfaketest-dirty-ab1
NPASS=0
NFAIL=0
FAILED=
CASE=
RUNN=0
RC=0

rm -rf "$WORK"
mkdir -p "$WORK/c"
sh "$HERE/shims.sh" "$SHIM" || { echo "cannot write shims"; exit 1; }

# ---------------------------------------------------------------- assertions

ok() { NPASS=$((NPASS + 1)); printf 'ok   %s: %s\n' "$CASE" "$1"; }
bad() { NFAIL=$((NFAIL + 1)); FAILED="$FAILED $CASE"; printf 'FAIL %s: %s\n' "$CASE" "$1"; }
check() {                               # check "description" command args...
  _d=$1
  shift
  if "$@"; then ok "$_d"; else bad "$_d"; fi
}
eq() { [ "$1" = "$2" ] || { printf '     expected [%s]\n     got      [%s]\n' "$2" "$1"; return 1; }; }
ne() { [ "$1" != "$2" ] || { printf '     unexpected [%s]\n' "$1"; return 1; }; }
has() { grep -qF -- "$2" "$1" 2>/dev/null || { printf '     [%s] not in %s\n' "$2" "${1#"$T"/}"; return 1; }; }
hasline() { grep -qxF -- "$2" "$1" 2>/dev/null || { printf '     line [%s] not in %s\n' "$2" "${1#"$T"/}"; return 1; }; }
hasnt() { if grep -qF -- "$2" "$1" 2>/dev/null; then printf '     [%s] found in %s\n' "$2" "${1#"$T"/}"; return 1; fi; }
exists() { [ -e "$1" ] || { printf '     missing %s\n' "${1#"$T"/}"; return 1; }; }
absent() { [ ! -e "$1" ] || { printf '     present %s\n' "${1#"$T"/}"; return 1; }; }
empty() { [ ! -s "$1" ] || { printf '     %s not empty:\n' "${1#"$T"/}"; head -n 5 "$1" | sed 's/^/       /'; return 1; }; }
le() { [ "$1" -le "$2" ] || { printf '     %s > %s\n' "$1" "$2"; return 1; }; }
ge() { [ "$1" -ge "$2" ] || { printf '     %s < %s\n' "$1" "$2"; return 1; }; }

prop() { cat "$T/props/$1" 2>/dev/null; }
setp() { printf '%s' "$2" > "$T/props/$1"; }
nlines() { if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi; }
mode_of() { stat -c %a "$1" 2>/dev/null; }
upt() { cut -d. -f1 "$R/proc/uptime"; }
lastown() { grep "^$1 " "$T/log/chown.log" 2>/dev/null | tail -n 1 | cut -d' ' -f2; }
nmsg() { find "$R/data/vendor/gb/notify" -name '*.msg' | wc -l | tr -d ' '; }
msg_has() { cat "$R"/data/vendor/gb/notify/*.msg 2>/dev/null | grep -qxF -- "$1" || { printf '     no queued line [%s]\n' "$1"; return 1; }; }
nins() { if [ -f "$T/log/insmod.log" ]; then grep -c '/nouveau.ko|' "$T/log/insmod.log"; else echo 0; fi; }
nouveau_args() { grep '/nouveau.ko|' "$T/log/insmod.log" | tail -n 1 | cut -d'|' -f2; }

fp_tree() {                              # fingerprint of every file/link (content, mode, target) under $1; the
  find "$1" \( -type f -o -type l \) ! -path '*/sys/power/wake_*' | sort | while read -r f; do   # wakelock (R-7) is no dGPU write
    if [ -L "$f" ]; then printf '%s -> %s\n' "$f" "$(readlink "$f")"
    else printf '%s %s %s\n' "$f" "$(stat -c %a "$f")" "$(sha256sum < "$f" | cut -c1-16)"; fi
  done | sha256sum | cut -c1-64
}

want() {                                 # GB_TEST_ONLY filter
  [ -z "${GB_TEST_ONLY:-}" ] && return 0
  for _w in $GB_TEST_ONLY; do case $1 in "$_w"*) return 0 ;; esac; done
  return 1
}

# ---------------------------------------------------------------- fake device tree

mk_pci() {                               # bdf parent vendor device class driver iommu_group
  if [ -n "$2" ]; then _d=$R/sys/devices/pci0000:00/$2/$1; else _d=$R/sys/devices/pci0000:00/$1; fi
  mkdir -p "$_d/power"
  echo "$3" > "$_d/vendor"
  echo "$4" > "$_d/device"
  echo "$5" > "$_d/class"
  printf '0x00000000a2000000 0x00000000a2ffffff 0x0000000000040200\n0x0000000000000000 0x0000000000000000 0x0000000000000000\n' > "$_d/resource"
  echo auto > "$_d/power/control"
  echo suspended > "$_d/power/runtime_status"
  echo D3cold > "$_d/power_state"
  echo 1200 > "$_d/power/runtime_active_time"
  echo 50000 > "$_d/power/runtime_suspended_time"
  echo 5000 > "$_d/power/autosuspend_delay_ms"
  ln -s "$_d" "$R/sys/bus/pci/devices/$1"
  [ -n "$6" ] && ln -s "$R/sys/bus/pci/drivers/$6" "$_d/driver"
  if [ "${7:-0}" != 0 ]; then
    mkdir -p "$R/sys/kernel/iommu_groups/$7/devices"
    ln -s "$_d" "$R/sys/kernel/iommu_groups/$7/devices/$1"
    ln -s "$R/sys/kernel/iommu_groups/$7" "$_d/iommu_group"
  fi
  return 0
}

mk_drm() {                               # bdf node-name
  _d=$(readlink -f "$R/sys/bus/pci/devices/$1")
  mkdir -p "$_d/drm/$2"
  ln -s "$_d" "$_d/drm/$2/device"
  ln -s "$_d/drm/$2" "$R/sys/class/drm/$2"
  : > "$R/dev/dri/$2"
  chmod 0666 "$R/dev/dri/$2"
}

cmdline() { echo "BOOT_IMAGE=/vmlinuz root=PARTUUID=0 gb.hw=native module_blacklist= $*" > "$R/proc/cmdline"; }

mk_tree() {
  for _t in proc/sys/kernel/random proc/asound sys/bus/pci/devices sys/class/drm sys/module sys/power/suspend_stats \
    sys/kernel/iommu_groups sys/firmware/acpi/tables/dynamic sys/bus/usb/devices/1-1 sys/class/block dev/dri \
    dev/block/by-name dev/gb_dgpu data/vendor/gb/report data/vendor/gb/notify data/vendor/gb/payload odm/etc/gb \
    odm/lib/modules odm/firmware/nvidia/tu117/gsp odm/dgpu mnt vendor/bin; do
    mkdir -p "$R/$_t"
  done
  echo native > "$R/odm/etc/gb_hw"
  chmod 0700 "$R/data/vendor/gb/payload"           # as init creates it (INTERFACES 15.1)
  cmdline ""
  echo '25.00 100.00' > "$R/proc/uptime"
  printf 'MemTotal:       16000000 kB\nMemFree:         9000000 kB\nMemAvailable:   12000000 kB\n' > "$R/proc/meminfo"
  printf '/dev/block/dm-13 /vendor erofs ro,seclabel 0 0\ntmpfs /odm tmpfs rw 0 0\n' > "$R/proc/mounts"
  echo 0 > "$R/proc/sys/kernel/panic"
  echo 1 > "$R/proc/sys/kernel/panic_on_oops"
  echo "boot-$$-$(date +%s%N)" > "$R/proc/sys/kernel/random/boot_id"
  echo ' 0 [sofhdadsp      ]: sof-hda-dsp - sof-hda-dsp' > "$R/proc/asound/cards"
  : > "$R/dev/kmsg"
  : > "$R/dev/block/sda1"
  ln -s "$R/dev/block/sda1" "$R/dev/block/by-name/EFI-SYSTEM"
  for _t in i915 nouveau snd_hda_intel pcieport vmwgfx; do
    mkdir -p "$R/sys/bus/pci/drivers/$_t"
    : > "$R/sys/bus/pci/drivers/$_t/unbind"
    : > "$R/sys/bus/pci/drivers/$_t/bind"
  done
  mk_pci 0000:00:02.0 '' 0x8086 0x8a52 0x030000 i915 1
  mk_pci 0000:00:1d.4 '' 0x8086 0x34b4 0x060400 pcieport 9
  mk_pci 0000:02:00.0 0000:00:1d.4 0x10de 0x1f91 0x030200 '' 12
  mk_pci 0000:02:00.1 0000:00:1d.4 0x10de 0x10fa 0x040300 '' 12
  mk_drm 0000:00:02.0 card0
  mk_drm 0000:00:02.0 renderD128
  mkdir -p "$R/sys/class/drm/card0-eDP-1"
  for _m in drm_exec drm_gpuvm gpu-sched drm_ttm_helper nouveau; do echo "fake legacy $_m" > "$R/odm/lib/modules/$_m.ko"; done
  echo fw-gsp > "$R/odm/firmware/nvidia/tu117/gsp/gsp-570.144.bin"
  echo fw-booter > "$R/odm/firmware/nvidia/tu117/gsp/booter_load-570.144.bin"
  (cd "$R/odm/firmware" && sha256sum nvidia/tu117/gsp/*.bin | sed 's/  / */') > "$R/odm/etc/gb/tu117.sha256"
  echo 045e > "$R/sys/bus/usb/devices/1-1/idVendor"
  echo 0922 > "$R/sys/bus/usb/devices/1-1/idProduct"
  echo 480 > "$R/sys/bus/usb/devices/1-1/speed"
  echo 'Surface Keyboard' > "$R/sys/bus/usb/devices/1-1/product"
  : > "$R/sys/firmware/acpi/tables/DSDT"
  : > "$R/sys/firmware/acpi/tables/SSDT1"
  : > "$R/sys/firmware/acpi/tables/dynamic/SSDT14"
  echo 0 > "$R/sys/power/suspend_stats/success"
  : > "$R/sys/power/wake_lock"                    # R-7 (regular files here: the last name written)
  : > "$R/sys/power/wake_unlock"
  echo 0 > "$R/sys/power/suspend_stats/fail"
  echo '' > "$R/sys/power/suspend_stats/last_failed_dev"
}

new_case() {
  CASE=$1
  T=$WORK/c/$1
  R=$T/root
  rm -rf "$T"
  mkdir -p "$T/props" "$T/log" "$T/ctl" "$T/esp/gb" "$T/payload_tree"
  printf '%s' "$KREL" > "$T/krel"
  GBT=$T
  GB_ROOT=$R
  GB_TEST_PATH=$SHIM${GB_TEST_TOOLS:+:$GB_TEST_TOOLS}:/usr/bin:/bin
  export GBT GB_ROOT GB_TEST_PATH
  mk_tree
  setp sys.boot_completed 1
  RUNN=0
}

payload_mode() {                         # P5 layout: descriptor in /odm, image on the fake ESP, no legacy files
  rm -f "$R"/odm/lib/modules/*.ko
  rm -rf "$R/odm/firmware/nvidia/tu117"
  P=$T/payload_tree
  printf 'gb-dgpu-payload 1\nkrel %s\n' "${1:-$KREL}" > "$P/VERSION"
  mkdir -p "$P/lib/modules" "$P/firmware/nvidia/tu117/gsp"
  for _m in drm_exec drm_gpuvm gpu-sched drm_ttm_helper nouveau; do echo "fake payload $_m" > "$P/lib/modules/$_m.ko"; done
  echo fw-gsp > "$P/firmware/nvidia/tu117/gsp/gsp-570.144.bin"
  echo fw-booter > "$P/firmware/nvidia/tu117/gsp/booter_load-570.144.bin"
  printf 'FAKE EROFS IMAGE %s\n' "$KREL" > "$T/esp/gb/dgpu-$KREL.erofs"
  _sha=$(sha256sum < "$T/esp/gb/dgpu-$KREL.erofs" | cut -c1-64)
  printf '%s  dgpu-%s.erofs\n' "$_sha" "${2:-$KREL}" > "$R/odm/etc/gb/dgpu_payload.sha256"
}

reboot_dev() {                           # new boot: /data and persist.* survive, everything else is reset
  for _t in "$T"/props/*; do
    [ -e "$_t" ] || continue
    case ${_t##*/} in persist.*) ;; *) rm -f "$_t" ;; esac
  done
  setp sys.boot_completed 1
  rm -rf "$R"/sys/module/*
  rm -rf "$R/dev/gb_dgpu" "$R/dev/gb_esp.lock"
  mkdir -p "$R/dev/gb_dgpu"
  _dd=$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")
  rm -f "$_dd/driver"
  rm -rf "$_dd/drm" "$R/sys/class/drm/card1" "$R/sys/class/drm/renderD129" "$R/dev/dri/card1" "$R/dev/dri/renderD129"
  echo suspended > "$_dd/power/runtime_status"
  echo D3cold > "$_dd/power_state"
  printf '/dev/block/dm-13 /vendor erofs ro,seclabel 0 0\ntmpfs /odm tmpfs rw 0 0\n' > "$R/proc/mounts"
  find "$R/odm/dgpu" "$R/mnt" -mindepth 1 -delete 2>/dev/null
  [ -f "$R/odm/etc/gb/dgpu_payload.sha256" ] && find "$R/odm/firmware/nvidia" -mindepth 1 -delete 2>/dev/null
  echo '25.00 100.00' > "$R/proc/uptime"
  echo 0 > "$R/proc/sys/kernel/panic"
  echo 1 > "$R/proc/sys/kernel/panic_on_oops"
  echo "boot-$$-$(date +%s%N)" > "$R/proc/sys/kernel/random/boot_id"
  : > "$R/dev/kmsg"
  for _t in insmod.log rmmod.log setprop.log mount.log unbind.log insmod_state.log; do rm -f "$T/log/$_t"; done
}

run() {                                  # run <script> <args...>: RC, output in $T/out.<n>
  _s=$1
  shift
  RUNN=$((RUNN + 1))
  timeout 120 "$SH" "$NAT/$_s" "$@" > "$T/out.$RUNN" 2>&1 < /dev/null
  RC=$?
}
np() { run gb_nouveau.sh "$@"; }

loaded() {                               # helper: an auto load (v1.2: gfx_mode unset = auto) that reached the clean mark
  echo 3 > "$R/data/vendor/gb/nouveau.clean_boots"
  np auto
  [ "$(prop vendor.gb.nouveau.result)" = clean ] || { printf '     setup load failed: %s\n' "$(prop vendor.gb.nouveau.result)"; return 1; }
}

intel() { setp persist.vendor.gb.gfx_mode intel; }   # v1.2: "off" acts only in Intel mode (INTERFACES 7.3)

mod_only() {                             # v1.2 helper: nouveau.ko and its helpers loaded, the dGPU driverless
  for _m in drm_exec drm_gpuvm gpu_sched drm_ttm_helper nouveau; do
    mkdir -p "$R/sys/module/$_m"
    echo 0 > "$R/sys/module/$_m/refcnt"
  done
  echo 2 > "$R/data/vendor/gb/nouveau.clean_boots"
}

boot_of() { cat "$R/proc/sys/kernel/random/boot_id"; }
results() { grep 'vendor.gb.nouveau.result=' "$T/log/setprop.log" | cut -d= -f2 | tr '\n' ' '; }
nobindw() {                              # no bind write ever reached the fake kernel
  if [ -s "$T/log/bind.log" ] || [ -s "$R/sys/bus/pci/drivers/nouveau/bind" ]; then
    printf '     bind write seen\n'
    return 1
  fi
}
mark_line() { cat "$R/data/vendor/gb/nouveau.loading" 2>/dev/null; }

holder() {                               # holder <pid> <comm> fd|maps <node>
  mkdir -p "$R/proc/$1/fd"
  printf '%s\n' "$2" > "$R/proc/$1/comm"
  : > "$R/proc/$1/maps"
  if [ "$3" = fd ]; then
    ln -s "/dev/dri/$4" "$R/proc/$1/fd/7"
  else
    printf '7f0000000000-7f0000100000 rw-s 00000000 00:05 1234                       /dev/dri/%s\n' "$4" > "$R/proc/$1/maps"
  fi
}

# ================================================================= NP-01 shellcheck

if want NP-01; then
  CASE=NP-01
  if command -v shellcheck > /dev/null 2>&1; then
    shellcheck -s sh "$NAT"/gb_nouveau.sh "$NAT"/gb_dgpu_report.sh "$NAT"/gb_notify.sh "$NAT"/gb_dgpu_check.sh \
      "$HERE"/test_gb_nouveau.sh "$HERE"/shims.sh "$HERE"/rc_lint.sh "$HERE"/run_tests.sh > "$WORK/shellcheck.txt" 2>&1
    check "shellcheck -s sh: 0 findings on all .sh files" eq "$?" 0
    [ -s "$WORK/shellcheck.txt" ] && head -n 40 "$WORK/shellcheck.txt"
  else
    bad "shellcheck not installed"
  fi
fi

# ================================================================= NP-02 kill switches

for kc in "zero:gb.nouveau=0" "bogus:gb.nouveau=bogus" "blacklist:module_blacklist=a,nouveau,b" \
  "lastwins:gb.nouveau=once gb.nouveau=0" "empty:gb.nouveau="; do
  want NP-02 || break
  new_case "NP-02-${kc%%:*}"
  cmdline "${kc#*:}"
  setp persist.vendor.gb.gfx_mode nvidia          # v1.2: the persistent setting is the graphics mode
  np auto
  check "exit 0" eq "$RC" 0
  check "no insmod" absent "$T/log/insmod.log"
  check "mode=disabled" eq "$(prop vendor.gb.nouveau.mode)" disabled
  check "result=disabled" eq "$(prop vendor.gb.nouveau.result)" disabled
  check "booted unset" eq "$(prop vendor.gb.nouveau.booted)" ""
  check "panic untouched" eq "$(cat "$R/proc/sys/kernel/panic")" 0
  check "gfx_mode unchanged (v1.2)" eq "$(prop persist.vendor.gb.gfx_mode)" nvidia
  case $kc in bogus:*) check "unknown value logged" has "$R/dev/kmsg" "unknown gb.nouveau value 'bogus'" ;; esac
done

if want NP-02; then
  new_case NP-02-lastwins-load
  cmdline "gb.nouveau=0 gb.nouveau=once"
  np auto
  check "gb.nouveau=0 then =once: once wins, loads" eq "$(prop vendor.gb.nouveau.mode)" once
  check "one nouveau insmod" eq "$(nins)" 1
fi

# ================================================================= NP-03 crash-loop marker

if want NP-03; then
  new_case NP-03
  echo 2 > "$R/data/vendor/gb/nouveau.clean_boots"
  echo "mode=persist t=31 argv=runpm=1 modeset=2" > "$R/data/vendor/gb/nouveau.loading"   # v1.1 format (no boot=)
  np auto
  check "exit 0" eq "$RC" 0
  check "marker removed" absent "$R/data/vendor/gb/nouveau.loading"
  check "gfx_mode set to intel (v1.2)" eq "$(prop persist.vendor.gb.gfx_mode)" intel
  check "the retired opt-in property is never written (v1.2)" hasnt "$T/log/setprop.log" "persist.vendor.gb.nouveau="
  check "clean_boots file 0" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 0
  check "vendor.gb.nouveau.clean=0" eq "$(prop vendor.gb.nouveau.clean)" 0
  check "result=crashloop" eq "$(prop vendor.gb.nouveau.result)" crashloop
  check "no insmod" absent "$T/log/insmod.log"
  check "booted unset" eq "$(prop vendor.gb.nouveau.booted)" ""
  check "one notification queued" eq "$(nmsg)" 1
  check "N01 title (v1.2)" msg_has "title=Switched to Intel graphics"
  check "N01 text (v1.2)" msg_has "text=The NVIDIA driver did not start cleanly last time, so graphics were set to Intel only. To try again, choose Auto in Settings > System > Graphics."
  check "N01 tag" msg_has "tag=gb_dgpu"
  check "vendor.gb.notify set (13-digit epoch ms)" eq "$(prop vendor.gb.notify | grep -cE '^[0-9]{13}$')" 1
  _f=$(find "$R/data/vendor/gb/notify" -name '*.msg' | head -n 1)
  check "queue file name format" eq "$(basename "$_f" | grep -cE '^[0-9]{13}-[0-9]+-1\.msg$')" 1
  check "queue file mode 0644" eq "$(mode_of "$_f")" 644
fi

# ================================================================= NP-04 probation counting

if want NP-04; then
  new_case NP-04                                   # v1.2: gfx_mode unset = auto, probation for auto loads
  for b in 1 2 3 4; do
    [ $b = 1 ] || reboot_dev
    np auto
    check "boot $b: mode=auto (v1.2)" eq "$(prop vendor.gb.nouveau.mode)" auto
    check "boot $b: result=clean" eq "$(prop vendor.gb.nouveau.result)" clean
    check "boot $b: clean_boots=$b" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" "$b"
    check "boot $b: vendor.gb.nouveau.clean=$b" eq "$(prop vendor.gb.nouveau.clean)" "$b"
    check "boot $b: marker removed" absent "$R/data/vendor/gb/nouveau.loading"
    check "boot $b: panic=30" eq "$(cat "$R/proc/sys/kernel/panic")" 30
    if [ $b -le 3 ]; then
      check "boot $b: dmesg stream requested" has "$T/log/setprop.log" "vendor.gb.dgpu.dmesg=1"
      check "boot $b: logs probation $b/3" has "$R/data/vendor/gb/report/gb_nouveau.log" "probation $b/3"
    else
      check "boot 4: no dmesg stream" hasnt "$T/log/setprop.log" "vendor.gb.dgpu.dmesg=1"
      check "boot 4: load record probation empty" hasline "$R/data/vendor/gb/report/load-4.txt" "probation="
      check "boot 4: panic_on_oops untouched" eq "$(cat "$R/proc/sys/kernel/panic_on_oops")" 1
    fi
  done
  check "bootseq = 4" eq "$(cut -d' ' -f1 "$R/data/vendor/gb/bootseq")" 4
  check "load-1..4 records" eq "$(find "$R/data/vendor/gb/report" -name 'load-*.txt' | wc -l | tr -d ' ')" 4
fi

# ================================================================= NP-05 crash after insmod

if want NP-05; then
  new_case NP-05
  echo 60 > "$T/ctl/crash_at"
  np auto
  check "boot 1 killed during the clean window (rc 137)" eq "$RC" 137
  check "boot 1: marker survives" exists "$R/data/vendor/gb/nouveau.loading"
  check "boot 1: marker content" has "$R/data/vendor/gb/nouveau.loading" "argv=runpm=1 modeset=2"
  check "boot 1: marker names boot 1 (v1.2)" has "$R/data/vendor/gb/nouveau.loading" "boot=$(boot_of) "
  check "boot 1: marker mode 0600" eq "$(mode_of "$R/data/vendor/gb/nouveau.loading")" 600
  check "boot 1: sync after the marker" exists "$T/log/sync.log"
  reboot_dev
  np auto
  check "boot 2: result=crashloop" eq "$(prop vendor.gb.nouveau.result)" crashloop
  check "boot 2: gfx_mode=intel (v1.2)" eq "$(prop persist.vendor.gb.gfx_mode)" intel
  check "boot 2: no insmod" absent "$T/log/insmod.log"
  reboot_dev
  np auto
  check "boot 3: gfx_mode intel: result=idle" eq "$(prop vendor.gb.nouveau.result)" idle
  check "boot 3: booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
  check "boot 3: no insmod" absent "$T/log/insmod.log"
fi

# ================================================================= NP-06 i915 guard

if want NP-06; then
  for v in render card; do
    new_case "NP-06-$v"
    mk_pci 0000:00:0f.0 '' 0x15ad 0x0405 0x030000 vmwgfx 2
    rm -rf "$R/sys/class/drm/renderD128" "$R/sys/class/drm/card0"
    if [ $v = render ]; then
      mk_drm 0000:00:0f.0 renderD128
      mk_drm 0000:00:0f.0 card0
    else
      mk_drm 0000:00:02.0 renderD128
      mk_drm 0000:00:0f.0 card0
    fi
    cmdline gb.nouveau=once
    np auto
    check "exit 1" eq "$RC" 1
    check "result=refused:i915" eq "$(prop vendor.gb.nouveau.result)" refused:i915
    check "no insmod" absent "$T/log/insmod.log"
    check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
    check "no marker" absent "$R/data/vendor/gb/nouveau.loading"
    check "no ESP lock left" absent "$R/dev/gb_esp.lock"
    check "panic untouched" eq "$(cat "$R/proc/sys/kernel/panic")" 0
  done
fi

# ================================================================= NP-07 dGPU absent / BAR zero

if want NP-07; then
  new_case NP-07-absent
  rm -f "$R/sys/bus/pci/devices/0000:02:00.0" "$R/sys/bus/pci/devices/0000:02:00.1"
  cmdline gb.nouveau=test
  np auto
  check "refused:nodgpu" eq "$(prop vendor.gb.nouveau.result)" refused:nodgpu
  check "exit 1" eq "$RC" 1
  check "no insmod" absent "$T/log/insmod.log"
  check "booted=1 (gb_power hot-attach restarts auto, 7.9)" eq "$(prop vendor.gb.nouveau.booted)" 1

  new_case NP-07-hda-only
  rm -f "$R/sys/bus/pci/devices/0000:02:00.0"
  cmdline gb.nouveau=once
  np auto
  check "only the HDA function: refused:nodgpu" eq "$(prop vendor.gb.nouveau.result)" refused:nodgpu

  new_case NP-07-bar
  _dd=$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")
  printf '0x0000000000000000 0x0000000000000000 0x0000000000000000\n' > "$_dd/resource"
  cmdline gb.nouveau=once
  np auto
  check "refused:bar" eq "$(prop vendor.gb.nouveau.result)" refused:bar
  check "no insmod" absent "$T/log/insmod.log"
fi

# ================================================================= NP-08 argument injection

if want NP-08; then
  new_case NP-08
  cmdline gb.nouveau=once
  # shellcheck disable=SC2046  # seq output is meant to be split
  _long=$(printf 'x%.0s' $(seq 1 300))
  # shellcheck disable=SC2016  # the $(...) and ${IFS} must reach the script literally
  setp persist.vendor.gb.nouveau.args "runpm=0 modeset=1 \$(touch $T/pwned) \$(touch\${IFS}$T/pwned2) \`touch$T/pwned3\` * config=NvGspRm=0;x $_long debug=gsp=debug,foo config=NvGspFw=0"
  (cd "$T" && : > glob_bait && np auto)
  check "loaded" eq "$(nins)" 1
  check "argv = whitelisted atom + runpm=1 modeset=2 last" eq "$(nouveau_args)" "config=NvGspFw=0 runpm=1 modeset=2"
  check "vendor.gb.nouveau.argv" eq "$(prop vendor.gb.nouveau.argv)" "config=NvGspFw=0 runpm=1 modeset=2"
  check "no command executed (pwned)" absent "$T/pwned"
  check "no command executed (pwned2)" absent "$T/pwned2"
  check "no command executed (pwned3)" absent "$T/pwned3"
  check "glob not expanded" hasnt "$T/log/insmod.log" glob_bait
  check "runpm=0 dropped+logged" has "$R/dev/kmsg" "dropped nouveau argument 'runpm=0'"
  check "modeset=1 dropped+logged" has "$R/dev/kmsg" "dropped nouveau argument 'modeset=1'"
  check "\$( token dropped+logged" has "$R/dev/kmsg" "dropped nouveau argument '\$(touch'"
  check "* dropped+logged" has "$R/dev/kmsg" "dropped nouveau argument '*'"
  check "config=NvGspRm=0;x dropped" has "$R/dev/kmsg" "dropped nouveau argument 'config=NvGspRm=0;x'"
  _x32=$(printf '%.32s' "$_long")
  check "300-char token logged truncated to 32" has "$R/dev/kmsg" "dropped nouveau argument '$_x32'"
  check "300-char token not logged in full" hasnt "$R/dev/kmsg" "${_x32}x"
  check "debug=gsp=debug,foo dropped" has "$R/dev/kmsg" "dropped nouveau argument 'debug=gsp=debug,foo'"
fi

# ================================================================= NP-09 argument assembly

if want NP-09; then
  new_case NP-09-a
  cmdline "gb.nouveau=once gb.nouveau.fw=535"
  setp persist.vendor.gb.nouveau.args "config=NvGspFw=0 debug=gsp=debug debug=gsp=trace"
  np auto
  check "fw=535 + trace wins" eq "$(nouveau_args)" "config=NvGspFw=0 debug=gsp=trace runpm=1 modeset=2"

  new_case NP-09-b
  cmdline "gb.nouveau=once"
  setp persist.vendor.gb.nouveau.args "config=NvGspRm=0 config=NvGspFw=0"
  np auto
  check "NvGspRm=0 drops NvGspFw=0" eq "$(nouveau_args)" "config=NvGspRm=0 runpm=1 modeset=2"
  check "drop logged" has "$R/dev/kmsg" "dropped NvGspFw=0"

  new_case NP-09-c
  cmdline "gb.nouveau=test"
  np auto
  check "test mode adds debug=gsp=debug" eq "$(nouveau_args)" "debug=gsp=debug runpm=1 modeset=2"

  new_case NP-09-d
  cmdline "gb.nouveau=once gb.nouveau.fw=999"
  setp persist.vendor.gb.nouveau.args "debug=gsp=trace debug=gsp=debug"
  np auto
  check "unknown fw ignored; trace kept over a later debug" eq "$(nouveau_args)" "debug=gsp=trace runpm=1 modeset=2"
  check "unknown fw logged" has "$R/dev/kmsg" "ignored gb.nouveau.fw value '999'"

  new_case NP-09-e
  cmdline "gb.nouveau=once gb.nouveau.fw=570"
  np auto
  check "fw=570 is a no-op" eq "$(nouveau_args)" "runpm=1 modeset=2"
fi

# ================================================================= NP-10 ESP lock / ESP mounted

if want NP-10; then
  new_case NP-10-live
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4242"
  echo "4242 gb_bootlog 20" > "$R/dev/gb_esp.lock/owner"
  _t0=$(upt)
  np auto
  _t1=$(upt)
  check "live holder >120 s: refused:esp" eq "$(prop vendor.gb.nouveau.result)" refused:esp
  check "waited >= 120 s (fake clock)" ge $((_t1 - _t0)) 120
  check "waited <= 125 s" le $((_t1 - _t0)) 125
  check "no insmod" absent "$T/log/insmod.log"
  check "foreign lock untouched" eq "$(cat "$R/dev/gb_esp.lock/owner")" "4242 gb_bootlog 20"
  check "loading never set" hasnt "$T/log/setprop.log" "vendor.gb.nouveau.loading=1"

  new_case NP-10-stale
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock"
  echo "999999 gb_bootlog 20" > "$R/dev/gb_esp.lock/owner"
  np auto
  check "stale lock stolen" has "$R/dev/kmsg" "stealing a stale lock"
  check "load proceeds (result=clean)" eq "$(prop vendor.gb.nouveau.result)" clean
  check "lock released after the load window" absent "$R/dev/gb_esp.lock"
  check "loading 1 then 0" eq "$(grep 'vendor.gb.nouveau.loading=' "$T/log/setprop.log" | tr '\n' ' ')" \
    "vendor.gb.nouveau.loading=1 vendor.gb.nouveau.loading=0 "

  new_case NP-10-race
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4243"
  echo "999999 gb_bootlog 20" > "$R/dev/gb_esp.lock/owner"
  : > "$T/ctl/steal_race"                         # a live holder re-creates the lock right before our rename
  np auto
  check "a lock re-created meanwhile is put back, not removed" eq "$(cat "$R/dev/gb_esp.lock/owner" 2>/dev/null)" "4243 gb_bootlog 30"
  check "logged" has "$R/dev/kmsg" "the lock changed while stealing it"
  check "then the live holder is waited for: refused:esp" eq "$(prop vendor.gb.nouveau.result)" refused:esp
  check "no insmod" absent "$T/log/insmod.log"
  check "no stale directory left" eq "$(find "$R/dev" -maxdepth 1 -name 'gb_esp.lock.stale.*' | wc -l | tr -d ' ')" 0

  new_case NP-10-ownerless
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock"
  np auto
  check "ownerless lock stolen after 5 s, load proceeds" eq "$(prop vendor.gb.nouveau.result)" clean

  for mv in byname:/dev/block/by-name/EFI-SYSTEM node:/dev/block/sda1; do
    new_case "NP-10-vfat-${mv%%:*}"
    cmdline gb.nouveau=once
    echo "${mv#*:} /mnt/gb_esp vfat rw,flush 0 0" >> "$R/proc/mounts"
    np auto
    check "vfat ESP mount at insmod time: refused:esp" eq "$(prop vendor.gb.nouveau.result)" refused:esp
    check "no insmod" absent "$T/log/insmod.log"
    check "no marker" absent "$R/data/vendor/gb/nouveau.loading"
    check "lock released" absent "$R/dev/gb_esp.lock"
    check "loading back to 0" eq "$(prop vendor.gb.nouveau.loading)" 0
  done

  new_case NP-10-othervfat
  cmdline gb.nouveau=once
  : > "$R/dev/block/sdb1"
  echo "/dev/block/sdb1 /mnt/media_rw/1234-ABCD vfat rw 0 0" >> "$R/proc/mounts"
  np auto
  check "an unrelated vfat mount does not block" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-10-logpart
  cmdline "gb.nouveau=once gb.logpart=ESP2"
  : > "$R/dev/block/sdc1"
  ln -s "$R/dev/block/sdc1" "$R/dev/block/by-name/ESP2"
  echo "/dev/block/by-name/ESP2 /mnt/gb_esp vfat rw 0 0" >> "$R/proc/mounts"
  np auto
  check "gb.logpart selects the ESP device" eq "$(prop vendor.gb.nouveau.result)" refused:esp
fi

# ================================================================= NP-11 payload stage / verify / mount

if want NP-11; then
  new_case NP-11-copy
  payload_mode
  cmdline gb.nouveau=once
  np auto
  check "result=clean" eq "$(prop vendor.gb.nouveau.result)" clean
  check "ESP mounted read-only for the copy" has "$T/log/mount.log" \
    "mount type=vfat opts=ro,nodev,nosuid,noexec src=/dev/block/by-name/EFI-SYSTEM dst=/mnt/gb_esp_ro"
  check "ESP unmounted" has "$T/log/mount.log" "umount /mnt/gb_esp_ro"
  check "ESP not mounted any more" hasnt "$R/proc/mounts" "/mnt/gb_esp_ro"
  check "/data copy present" exists "$R/data/vendor/gb/payload/dgpu.erofs"
  check "/data copy mode 0640" eq "$(mode_of "$R/data/vendor/gb/payload/dgpu.erofs")" 640
  check "/data copy == ESP image" eq "$(sha256sum < "$R/data/vendor/gb/payload/dgpu.erofs")" "$(sha256sum < "$T/esp/gb/dgpu-$KREL.erofs")"
  check "no tmp left" absent "$R/data/vendor/gb/payload/dgpu.erofs.tmp"
  check "erofs mount with context" has "$T/log/mount.log" \
    "mount type=erofs opts=ro,nodev,nosuid,context=u:object_r:vendor_file:s0 src=/data/vendor/gb/payload/dgpu.erofs dst=/odm/dgpu"
  check "firmware bind" has "$T/log/mount.log" "opts=bind src=/odm/dgpu/firmware/nvidia dst=/odm/firmware/nvidia"
  check "insmod from the payload" has "$T/log/insmod.log" "/odm/dgpu/lib/modules/nouveau.ko|"
  check "deps from the payload" has "$T/log/insmod.log" "/odm/dgpu/lib/modules/gpu-sched.ko|"
  check "payload=mounted" eq "$(prop vendor.gb.dgpu.payload)" mounted
  check "load record payload_backing=file" has "$R/data/vendor/gb/report/load-1.txt" "payload_backing=file"
  _m1=$(grep -n 'type=erofs' "$T/log/mount.log" | cut -d: -f1)
  _u1=$(grep -n 'umount /mnt/gb_esp_ro' "$T/log/mount.log" | cut -d: -f1)
  check "ESP unmounted before the erofs mount" le "$_u1" "$_m1"
  # second boot: the verified copy is reused, the ESP is not mounted again
  reboot_dev
  np auto
  check "boot 2: no ESP mount (copy matches)" hasnt "$T/log/mount.log" "type=vfat"
  check "boot 2: loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-refresh
  payload_mode
  cmdline gb.nouveau=once
  echo "old image" > "$R/data/vendor/gb/payload/dgpu.erofs"
  np auto
  check "outdated copy refreshed" has "$R/dev/kmsg" "differs from the image"
  check "copy == ESP image" eq "$(sha256sum < "$R/data/vendor/gb/payload/dgpu.erofs")" "$(sha256sum < "$T/esp/gb/dgpu-$KREL.erofs")"
  check "loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-corrupt
  payload_mode
  cmdline gb.nouveau=once
  echo "corrupted bytes" >> "$T/esp/gb/dgpu-$KREL.erofs"
  np auto
  check "refused:payload" eq "$(prop vendor.gb.nouveau.result)" refused:payload
  check "payload=error:sha" eq "$(prop vendor.gb.dgpu.payload)" error:sha
  check "no insmod" absent "$T/log/insmod.log"
  check "tmp deleted" absent "$R/data/vendor/gb/payload/dgpu.erofs.tmp"
  check "no copy kept" absent "$R/data/vendor/gb/payload/dgpu.erofs"
  check "ESP unmounted" hasnt "$R/proc/mounts" "/mnt/gb_esp_ro"
  check "no erofs mount" hasnt "$T/log/mount.log" "type=erofs"
  check "lock released, loading=0" eq "$(prop vendor.gb.nouveau.loading)$(test -e "$R/dev/gb_esp.lock" && echo L)" 0
  check "no marker" absent "$R/data/vendor/gb/nouveau.loading"
  check "used from the start of the load window (property)" eq "$(prop vendor.gb.nouveau.used)" 1
  check "used from the start of the load window (file)" exists "$R/dev/gb_dgpu/used"

  new_case NP-11-tampered
  payload_mode
  cmdline gb.nouveau=once
  cp "$T/esp/gb/dgpu-$KREL.erofs" "$R/data/vendor/gb/payload/dgpu.erofs"
  np auto
  check "valid copy: loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-mountfail
  payload_mode
  cmdline gb.nouveau=once
  : > "$T/ctl/mount_fail_erofs"
  np auto
  check "payload=error:mount" eq "$(prop vendor.gb.dgpu.payload)" error:mount
  check "refused:payload" eq "$(prop vendor.gb.nouveau.result)" refused:payload
  check "no insmod" absent "$T/log/insmod.log"
  check "no bind attempted" hasnt "$T/log/mount.log" "opts=bind"

  new_case NP-11-krel
  payload_mode "$KREL" "6.18.99-other"
  cmdline gb.nouveau=once
  np auto
  check "payload=error:krel" eq "$(prop vendor.gb.dgpu.payload)" error:krel
  check "refused:payload" eq "$(prop vendor.gb.nouveau.result)" refused:payload
  check "nothing mounted" absent "$T/log/mount.log"

  new_case NP-11-version
  payload_mode "6.18.99-other"
  cmdline gb.nouveau=once
  np auto
  check "VERSION for another krel: error:version" eq "$(prop vendor.gb.dgpu.payload)" error:version
  check "both mounts undone" eq "$(grep -c '^umount /odm/' "$T/log/mount.log")" 2
  check "nothing left mounted under /odm" hasnt "$R/proc/mounts" " /odm/dgpu "
  check "no insmod" absent "$T/log/insmod.log"

  new_case NP-11-bind
  payload_mode
  cmdline gb.nouveau=once
  : > "$T/ctl/mount_fail_bind"
  np auto
  check "payload=error:bind" eq "$(prop vendor.gb.dgpu.payload)" error:bind
  check "erofs unmounted again" has "$T/log/mount.log" "umount /odm/dgpu"
  check "no insmod" absent "$T/log/insmod.log"

  new_case NP-11-espmount
  payload_mode
  cmdline gb.nouveau=once
  : > "$T/ctl/mount_fail_vfat"
  np auto
  check "payload=error:esp-mount" eq "$(prop vendor.gb.dgpu.payload)" error:esp-mount
  check "no insmod" absent "$T/log/insmod.log"

  new_case NP-11-absent
  payload_mode
  cmdline gb.nouveau=once
  rm -f "$T/esp/gb/dgpu-$KREL.erofs"
  np auto
  check "payload=absent" eq "$(prop vendor.gb.dgpu.payload)" absent
  check "refused:payload" eq "$(prop vendor.gb.nouveau.result)" refused:payload
  check "ESP unmounted" has "$T/log/mount.log" "umount /mnt/gb_esp_ro"

  new_case NP-11-loop
  payload_mode
  cmdline gb.nouveau=once
  : > "$T/ctl/erofs_loop"
  np auto
  check "loop-backed mount recorded (D27)" has "$R/data/vendor/gb/report/load-1.txt" "payload_backing=loop"

  new_case NP-11-remount
  payload_mode
  cmdline gb.nouveau=once
  np auto
  rm -rf "$R/sys/module/nouveau"
  : > "$T/log/mount.log"
  np auto
  check "second auto run: payload already mounted, not remounted" eq "$(nlines "$T/log/mount.log")" 0
  check "second auto run loads" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-symdir
  payload_mode
  cmdline gb.nouveau=once
  mkdir -p "$T/evil"
  cp "$T/esp/gb/dgpu-$KREL.erofs" "$T/evil/dgpu.erofs"
  rm -rf "$R/data/vendor/gb/payload"
  ln -s "$T/evil" "$R/data/vendor/gb/payload"
  np auto
  check "payload/ symlink replaced by a real directory" eq "$(test -L "$R/data/vendor/gb/payload" && echo L; test -d "$R/data/vendor/gb/payload" && echo D)" D
  check "payload/ mode 0700" eq "$(mode_of "$R/data/vendor/gb/payload")" 700
  check "symlink target untouched" exists "$T/evil/dgpu.erofs"
  check "logged" has "$R/dev/kmsg" "is not a root-only directory: recreated"
  check "re-copied from the ESP" has "$T/log/mount.log" "type=vfat"
  check "loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-symfile
  payload_mode
  cmdline gb.nouveau=once
  cp "$T/esp/gb/dgpu-$KREL.erofs" "$T/elsewhere.erofs"
  ln -s "$T/elsewhere.erofs" "$R/data/vendor/gb/payload/dgpu.erofs"
  np auto
  check "symlinked copy removed and re-copied from the ESP" has "$T/log/mount.log" "type=vfat"
  check "copy is a plain file now" eq "$(test -L "$R/data/vendor/gb/payload/dgpu.erofs" && echo L; test -f "$R/data/vendor/gb/payload/dgpu.erofs" && echo F)" F
  check "symlink target untouched" exists "$T/elsewhere.erofs"
  check "loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-hardlink
  payload_mode
  cmdline gb.nouveau=once
  cp "$T/esp/gb/dgpu-$KREL.erofs" "$T/hl.erofs"
  ln "$T/hl.erofs" "$R/data/vendor/gb/payload/dgpu.erofs"
  np auto
  check "hard-linked copy (2 links) removed and re-copied" has "$T/log/mount.log" "type=vfat"
  check "second link untouched" exists "$T/hl.erofs"
  check "loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-dirmode
  payload_mode
  cmdline gb.nouveau=once
  chmod 0777 "$R/data/vendor/gb/payload"
  np auto
  check "a world-writable payload/ is recreated 0700" eq "$(mode_of "$R/data/vendor/gb/payload")" 700
  check "loaded" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-11-legacy
  cmdline gb.nouveau=once
  np auto
  check "legacy: loads from /odm/lib/modules" has "$T/log/insmod.log" "/odm/lib/modules/nouveau.ko|"
  check "legacy: payload=legacy" eq "$(prop vendor.gb.dgpu.payload)" legacy
  check "legacy: no mounts" absent "$T/log/mount.log"

  new_case NP-11-legacy-nomodule
  cmdline gb.nouveau=once
  rm -f "$R/odm/lib/modules/nouveau.ko"
  np auto
  check "legacy without nouveau.ko: refused:module" eq "$(prop vendor.gb.nouveau.result)" refused:module
fi

# ================================================================= NP-12 health

if want NP-12; then
  new_case NP-12-healthy
  cmdline gb.nouveau=once
  np auto
  check "exit 0" eq "$RC" 0
  check "healthy then clean" eq "$(grep 'vendor.gb.nouveau.result=' "$T/log/setprop.log" | cut -d= -f2 | tr '\n' ' ')" "loading healthy clean "
  L=$R/data/vendor/gb/report/load-1.txt
  check "load record health=clean" has "$L" "health=clean"
  check "load record rm_version" has "$L" "rm_version=570.144"
  check "load record render_map" has "$L" "render_map=renderD128=0x8086:i915,renderD129=0x10de:nouveau"
  check "load record refcnt_baseline=0" has "$L" "refcnt_baseline=0"
  check "load record mem before" has "$L" "mem_available_before_kb=12000000"
  check "load record fbsr" has "$L" "fbsr_line=[   30.300000] nouveau 0000:02:00.0: fbsr: size: 0x1d00000 bytes"
  check "load record krel" has "$L" "krel=$KREL"
  check "load record mode 0640" eq "$(mode_of "$L")" 640
  check "load record root:shell" eq "$(lastown /data/vendor/gb/report/load-1.txt.tmp)" root:shell
  check "clean_boots counts once boots" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 1
  check "used flag (property)" eq "$(prop vendor.gb.nouveau.used)" 1
  check "used flag (file)" exists "$R/dev/gb_dgpu/used"
  check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
  check "loading=0" eq "$(prop vendor.gb.nouveau.loading)" 0

  for w in d3cold dmesg bind; do
    new_case "NP-12-$w"
    echo 2 > "$R/data/vendor/gb/nouveau.clean_boots"
    case $w in
      d3cold) : > "$T/ctl/no_d3cold" ;;
      dmesg) echo "[   31.000000] nouveau 0000:02:00.0: gsp: timeout waiting for GSP RPC" > "$T/ctl/dmesg_add" ;;
      bind) : > "$T/ctl/nobind" ;;
    esac
    _t0=$(upt)
    np auto
    _t1=$(upt)
    check "exit 1" eq "$RC" 1
    check "result=unhealthy:$w" eq "$(prop vendor.gb.nouveau.result)" "unhealthy:$w"
    check "gfx_mode set to intel (v1.2)" eq "$(prop persist.vendor.gb.gfx_mode)" intel
    check "clean_boots 0" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 0
    check "marker removed" absent "$R/data/vendor/gb/nouveau.loading"
    check "N02 queued with <why> (v1.2 text)" msg_has "text=The NVIDIA driver started with errors ($w). Graphics are set to Intel only."
    check "N02 title" msg_has "title=NVIDIA GPU problem"
    check "no rmmod (driver left loaded)" absent "$T/log/rmmod.log"
    check "nouveau still loaded" exists "$R/sys/module/nouveau"
    check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
    check "lock released" absent "$R/dev/gb_esp.lock"
    check "decided within the 30 s window" le $((_t1 - _t0)) 32
    # the rc now starts gb_nouveau_off (booted=1 && gfx_mode=intel): it must not unload the sick driver (D20)
    np off
    check "off after unhealthy: no unbind" absent "$T/log/unbind.log"
    check "off after unhealthy: no rmmod" absent "$T/log/rmmod.log"
    check "off after unhealthy: result kept" eq "$(prop vendor.gb.nouveau.result)" "unhealthy:$w"
  done

  new_case NP-12-late
  cmdline gb.nouveau=once
  echo 90 > "$T/ctl/late_dmesg_at"
  echo "[   90.000000] WARNING: CPU: 1 PID: 77 at drivers/gpu/drm/nouveau/nvkm/subdev/gsp/r535.c:123" > "$T/ctl/late_dmesg"
  np auto
  check "WARNING after the window, before +120 s: unhealthy:dmesg" eq "$(prop vendor.gb.nouveau.result)" unhealthy:dmesg
  check "not counted as clean" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 0

  new_case NP-12-noise
  cmdline gb.nouveau=once
  {
    echo "[   31.000000] init: Unable to set property 'vendor.gb.nouveau.result' to 'insmod-failed:x' error"
    echo "[   31.100000] gb_power: dGPU nouveau bound, detach slow path armed (timeout fault test)"
  } > "$T/ctl/dmesg_add"
  np auto
  check "init:/gb_*: lines are not driver errors" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-12-once-d3cold
  cmdline gb.nouveau=once
  : > "$T/ctl/no_d3cold"
  np auto
  check "never D3cold (once mode): unhealthy:d3cold" eq "$(prop vendor.gb.nouveau.result)" unhealthy:d3cold
  check "v1.2: every unhealthy load sets gfx_mode=intel (once boots too)" eq "$(prop persist.vendor.gb.gfx_mode)" intel

  new_case NP-12-before-mark
  cmdline gb.nouveau=once
  echo "[    9.000000] nouveau 0000:02:00.0: old error from before this load" > "$R/dev/kmsg"
  np auto
  check "errors before the insmod mark are ignored" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-12-scrolled
  cmdline gb.nouveau=once
  echo "[    9.000000] nouveau 0000:02:00.0: fifo: fault 01 [VIRT_WRITE] at 0000000000400000" > "$R/dev/kmsg"
  : > "$T/ctl/dmesg_drop_mark"
  np auto
  check "mark scrolled out of the ring: the whole buffer counts" eq "$(prop vendor.gb.nouveau.result)" unhealthy:dmesg
fi

# ================================================================= NP-13 node permissions

if want NP-13; then
  new_case NP-13-normal
  cmdline gb.nouveau=once
  np auto
  check "renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "renderD129 root:graphics" eq "$(lastown /dev/dri/renderD129)" root:graphics
  check "card1 0600" eq "$(mode_of "$R/dev/dri/card1")" 600
  check "card1 root:root" eq "$(lastown /dev/dri/card1)" root:root
  check "i915 renderD128 untouched" eq "$(mode_of "$R/dev/dri/renderD128")" 666
  check "i915 card0 untouched" eq "$(mode_of "$R/dev/dri/card0")" 666
  check "no chown of i915 nodes" hasnt "$T/log/chown.log" "/dev/dri/renderD128"

  # v1.2 (TASKS_1B 9): the retired P8 gate opens nothing any more: 0660 in every case, auto never sets foreign
  new_case NP-13-gfx
  cmdline "gb.nouveau=once gb.gfxdriver=1"
  setp vendor.gb.gfxdriver on
  : > "$R/dev/gb_dgpu/gfxdriver"
  np auto
  check "v1.2: retired gfxdriver property + flag: renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "gfxdriver boot: card1 0600" eq "$(mode_of "$R/dev/dri/card1")" 600
  check "v1.2: auto does not set foreign (property)" eq "$(prop vendor.gb.nouveau.foreign)" ""
  check "v1.2: auto does not set foreign (file)" absent "$R/dev/gb_dgpu/foreign"

  new_case NP-13-spoof
  cmdline "gb.nouveau=once gb.gfxdriver=1"
  setp vendor.gb.gfxdriver on
  np auto
  check "property and cmdline token: renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "v1.2: no foreign from the property" eq "$(prop vendor.gb.nouveau.foreign)" ""

  new_case NP-13-flagonly
  cmdline "gb.nouveau=once"
  : > "$R/dev/gb_dgpu/gfxdriver"
  np auto
  check "flag without the property: renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "v1.2: no foreign from the flag" absent "$R/dev/gb_dgpu/foreign"
fi

# ================================================================= NP-14 off refused while in use

if want NP-14; then
  for hk in fd maps none six; do
    new_case "NP-14-$hk"
    loaded || continue
    intel
    echo 1 > "$R/sys/module/nouveau/refcnt"
    case $hk in
      fd) holder 4321 nvk_probe fd renderD129 ;;
      maps) holder 4322 gb.app.render maps card1 ;;
      six)
        holder 501 a1 fd renderD129; holder 502 a2 fd renderD129; holder 503 a3 maps renderD129
        holder 504 a4 fd card1; holder 505 a5 fd renderD129; holder 506 a6 maps card1; holder 507 bystander fd null
        echo 6 > "$R/sys/module/nouveau/refcnt" ;;
    esac
    np off
    check "exit 1" eq "$RC" 1
    case $hk in
      fd)
        check "off-busy:1" eq "$(prop vendor.gb.nouveau.result)" off-busy:1
        check "N03 names the holder (v1.2 text)" msg_has "text=Close nvk_probe to finish switching to Intel graphics." ;;
      maps)
        check "off-busy:1 (maps only)" eq "$(prop vendor.gb.nouveau.result)" off-busy:1
        check "N03 names the maps holder" msg_has "text=Close gb.app.render to finish switching to Intel graphics." ;;
      none)
        check "refcnt>0, no holder found: off-busy:1" eq "$(prop vendor.gb.nouveau.result)" off-busy:1
        check "N03 generic" msg_has "text=Close the apps using it to finish switching to Intel graphics." ;;
      six)
        check "off-busy:6" eq "$(prop vendor.gb.nouveau.result)" off-busy:6
        check "N03 max 5 names + and N more" msg_has "text=Close a1, a2, a3, a4, a5 and 1 more to finish switching to Intel graphics." ;;
    esac
    check "N03 title" msg_has "title=NVIDIA GPU is in use"
    check "no unbind" absent "$T/log/unbind.log"
    check "no rmmod" absent "$T/log/rmmod.log"
    check "still bound" exists "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/driver"
    check "perms restored: renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
    check "perms restored: card1 0600" eq "$(mode_of "$R/dev/dri/card1")" 600
    check "chmod 000 on renderD129 first" has "$T/log/chmod.log" "000 /dev/dri/renderD129"
    check "chmod 000 on card1 first" has "$T/log/chmod.log" "000 /dev/dri/card1"
    check "logged refcnt and holders" has "$R/data/vendor/gb/report/gb_nouveau.log" "off refused: refcnt="
    check "gb_gpu_compute stopped first (D29)" has "$T/log/setprop.log" "ctl.stop=gb_gpu_compute"
    check "gb_nvk_probe stopped first (root opener)" has "$T/log/setprop.log" "ctl.stop=gb_nvk_probe"
    check "gb_dgpu_test stopped first (root opener)" has "$T/log/setprop.log" "ctl.stop=gb_dgpu_test"
    check "gb_gfx_selftest stopped first (v1.2)" has "$T/log/setprop.log" "ctl.stop=gb_gfx_selftest"
  done
fi

# ================================================================= NP-15 off with a hung unbind

if want NP-15; then
  new_case NP-15
  loaded
  intel
  rm -f "$R/sys/bus/pci/drivers/nouveau/unbind"
  mkfifo "$R/sys/bus/pci/drivers/nouveau/unbind"
  _t0=$(upt)
  np off
  _t1=$(upt)
  timeout 5 cat "$R/sys/bus/pci/drivers/nouveau/unbind" > /dev/null 2>&1   # release the stuck writer
  check "exit 1" eq "$RC" 1
  check "result=off-wedged" eq "$(prop vendor.gb.nouveau.result)" off-wedged
  check "wedged flag file" exists "$R/dev/gb_dgpu/wedged"
  check "no wedged property (D9)" hasnt "$T/log/setprop.log" "wedged="
  check "within 21 s (fake clock)" le $((_t1 - _t0)) 21
  check "N05 queued" msg_has "text=The NVIDIA GPU stopped responding. Restart before detaching the screen."
  check "no rmmod" absent "$T/log/rmmod.log"
  : > "$T/log/setprop.log"
  _fp=$(fp_tree "$R/sys")
  np off
  check "second off: off-wedged, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" off-wedged/1
  check "second off: no sysfs write" eq "$(fp_tree "$R/sys")" "$_fp"
  np auto
  check "auto after wedge: refused:wedged" eq "$(prop vendor.gb.nouveau.result)" refused:wedged
fi

# ================================================================= NP-16 off with foreign set

if want NP-16; then
  for fk in prop file; do
    new_case "NP-16-$fk"
    [ $fk = prop ] && payload_mode
    loaded || continue
    intel
    if [ $fk = prop ]; then setp vendor.gb.nouveau.foreign 1; else : > "$R/dev/gb_dgpu/foreign"; fi
    for f in 0000:02:00.0 0000:02:00.1 0000:00:1d.4; do echo on > "$(readlink -f "$R/sys/bus/pci/devices/$f")/power/control"; done
    np off
    check "exit 0" eq "$RC" 0
    check "unbound" has "$T/log/unbind.log" "nouveau 0000:02:00.0"
    check "no rmmod" absent "$T/log/rmmod.log"
    check "module stays loaded" exists "$R/sys/module/nouveau"
    check "result=off-unbound" eq "$(prop vendor.gb.nouveau.result)" off-unbound
    check "off flag (module kept loaded)" exists "$R/dev/gb_dgpu/off"
    check "v1.2: no N04 queued" eq "$(nmsg)" 0
    for f in 0000:02:00.0 0000:02:00.1 0000:00:1d.4; do
      check "power/control=auto on $f" eq "$(cat "$(readlink -f "$R/sys/bus/pci/devices/$f")/power/control")" auto
    done
    if [ $fk = prop ]; then
      check "payload stays mounted" has "$R/proc/mounts" " /odm/dgpu "
      check "firmware bind stays" has "$R/proc/mounts" " /odm/firmware/nvidia "
    fi
  done

  new_case NP-16-clean
  payload_mode
  loaded
  intel
  holder 600 bystander fd null
  _dd=$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.1")
  ln -s "$R/sys/bus/pci/drivers/snd_hda_intel" "$_dd/driver"
  np off
  check "exit 0" eq "$RC" 0
  check "nouveau unbound" has "$T/log/unbind.log" "nouveau 0000:02:00.0"
  check "HDA function unbound too" has "$T/log/unbind.log" "snd_hda_intel 0000:02:00.1"
  check "rmmod nouveau first" eq "$(head -n 1 "$T/log/rmmod.log")" nouveau
  check "rmmod the helpers this script loaded" eq "$(tr '\n' ' ' < "$T/log/rmmod.log")" "nouveau drm_ttm_helper gpu_sched drm_gpuvm drm_exec "
  check "result=off-done" eq "$(prop vendor.gb.nouveau.result)" off-done
  check "module unloaded: no off flag" absent "$R/dev/gb_dgpu/off"
  check "payload unmounted (bind first)" eq "$(grep '^umount /odm' "$T/log/mount.log" | tr '\n' ' ')" "umount /odm/firmware/nvidia umount /odm/dgpu "
  check "payload=ready" eq "$(prop vendor.gb.dgpu.payload)" ready
  setp persist.vendor.gb.gfx_mode auto
  np auto
  check "re-enable after off (gfx_mode auto): loads again" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-16-sharedhelper
  mkdir -p "$R/sys/module/drm_ttm_helper"
  loaded
  intel
  np off
  check "a helper loaded by someone else is not rmmodded" eq "$(tr '\n' ' ' < "$T/log/rmmod.log")" "nouveau gpu_sched drm_gpuvm drm_exec "

  new_case NP-16-notloaded
  intel
  _dd=$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")
  echo on > "$_dd/power/control"
  np off
  check "not loaded: off-done" eq "$(prop vendor.gb.nouveau.result)" off-done
  check "not loaded: power/control=auto" eq "$(cat "$_dd/power/control")" auto
  check "not loaded: no unbind/rmmod" eq "$(nlines "$T/log/unbind.log")$(nlines "$T/log/rmmod.log")" 00

  new_case NP-16-loader
  loaded
  intel
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/777"
  echo "777 gb_nouveau 30" > "$R/dev/gb_esp.lock/owner"
  _t0=$(upt)
  np off
  _t1=$(upt)
  check "off waits for a live load window (<= 150 s), then proceeds" ge $((_t1 - _t0)) 150
  check "then unloads" eq "$(prop vendor.gb.nouveau.result)" off-done
fi

# ================================================================= NP-17 off in once/test boots

if want NP-17; then
  for bm in once test; do
    new_case "NP-17-$bm"
    cmdline "gb.nouveau=$bm"
    np auto
    : > "$T/log/setprop.log"
    _fp=$(fp_tree "$R/sys")
    np off
    check "exit 0" eq "$RC" 0
    check "result=off-ignored" eq "$(prop vendor.gb.nouveau.result)" off-ignored
    check "nothing written to sysfs" eq "$(fp_tree "$R/sys")" "$_fp"
    check "no unbind, no rmmod" eq "$(nlines "$T/log/unbind.log")$(nlines "$T/log/rmmod.log")" 00
    check "only the result property was set" eq "$(cat "$T/log/setprop.log")" "vendor.gb.nouveau.result=off-ignored"
  done
  new_case NP-17-running
  cmdline "gb.nouveau=once"
  np auto
  setp init.svc.gb_nouveau running
  np off
  check "auto still running: result left alone" eq "$(prop vendor.gb.nouveau.result)" clean
fi

# ================================================================= NP-18 auto when already loaded

if want NP-18; then
  new_case NP-18
  loaded
  chmod 0644 "$R/dev/dri/renderD129"            # R-10: 0660/0666 are gb_power's choice when bound (NP-R10-bound)
  _n=$(nins)
  np auto
  check "exit 0" eq "$RC" 0
  check "no second insmod" eq "$(nins)" "$_n"
  check "perms re-applied (R-10: a node at neither 0660 nor 0666)" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
fi

# ================================================================= NP-19 panic policy

if want NP-19; then
  new_case NP-19-test
  cmdline gb.nouveau=test
  np auto
  check "test: panic=30 at insmod" has "$T/log/insmod_state.log" "nouveau panic=30 oops=0"
  check "test: dmesg stream" has "$T/log/setprop.log" "vendor.gb.dgpu.dmesg=1"
  check "test: report sequence requested" has "$T/log/setprop.log" "vendor.gb.dgpu_report=2"

  new_case NP-19-probation                         # v1.2: auto (gfx_mode unset) with clean_boots < 3
  np auto
  check "probation: panic=30, panic_on_oops kept" has "$T/log/insmod_state.log" "nouveau panic=30 oops=1"
  check "probation: dmesg stream" has "$T/log/setprop.log" "vendor.gb.dgpu.dmesg=1"
  check "probation: no test report" hasnt "$T/log/setprop.log" "vendor.gb.dgpu_report=2"

  new_case NP-19-auto                              # v1.2 (was -persist): auto after 3 clean boots
  echo 3 > "$R/data/vendor/gb/nouveau.clean_boots"
  np auto
  check "auto: panic=30 only" has "$T/log/insmod_state.log" "nouveau panic=30 oops=1"
  check "auto: no dmesg stream" hasnt "$T/log/setprop.log" "vendor.gb.dgpu.dmesg"

  new_case NP-19-default                           # v1.2: "default" = gfx_mode intel
  intel
  np auto
  check "default: exit 0" eq "$RC" 0
  check "default: result=idle" eq "$(prop vendor.gb.nouveau.result)" idle
  check "default: booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
  check "default: panic untouched" eq "$(cat "$R/proc/sys/kernel/panic")" 0
  check "default: panic_on_oops untouched" eq "$(cat "$R/proc/sys/kernel/panic_on_oops")" 1
  check "default: no dmesg stream" hasnt "$T/log/setprop.log" "vendor.gb.dgpu.dmesg"
  check "default: no insmod" absent "$T/log/insmod.log"
  check "default: no marker" absent "$R/data/vendor/gb/nouveau.loading"
  check "default: mode=default" eq "$(prop vendor.gb.nouveau.mode)" default

  new_case NP-19-vm
  echo vm > "$R/odm/etc/gb_hw"
  cmdline gb.nouveau=test
  np auto
  check "VM image: refused:hw, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:hw/1
  check "VM image: booted unset" eq "$(prop vendor.gb.nouveau.booted)" ""
  np off
  check "VM image: off refused:hw" eq "$RC" 1
  np frobnicate
  check "usage: exit 2" eq "$RC" 2

  new_case NP-19-insmodfail
  cmdline gb.nouveau=once
  : > "$T/ctl/insmod_fail_nouveau"
  np auto
  check "insmod failure: insmod-failed:nouveau" eq "$(prop vendor.gb.nouveau.result)" insmod-failed:nouveau
  check "insmod failure: marker removed" absent "$R/data/vendor/gb/nouveau.loading"
  check "insmod failure: booted=1, loading=0" eq "$(prop vendor.gb.nouveau.booted)$(prop vendor.gb.nouveau.loading)" 10

  new_case NP-19-depfail
  cmdline gb.nouveau=once
  : > "$T/ctl/insmod_fail_gpu_sched"
  np auto
  check "dependency failure: insmod-failed:gpu-sched" eq "$(prop vendor.gb.nouveau.result)" insmod-failed:gpu-sched
  check "dependency failure: nouveau.ko not tried" eq "$(nins)" 0

  new_case NP-19-marker
  cmdline gb.nouveau=once
  chmod 0555 "$R/data/vendor/gb"
  np auto
  chmod 0755 "$R/data/vendor/gb"
  check "marker cannot be written: refused:marker, no insmod" eq "$(prop vendor.gb.nouveau.result)$(nins)" refused:marker0
fi

# ================================================================= dmesg-stream (7.6)

if want NP-DS; then
  new_case NP-DS-rotate
  echo "== gb_dgpu_dmesg boot_id=previous-boot" > "$R/data/vendor/gb/report/dmesg-live.txt"
  echo "[  12.0] last words of the previous boot" >> "$R/data/vendor/gb/report/dmesg-live.txt"
  echo "[   1.0] kernel line of this boot" > "$R/dev/kmsg"
  : > "$T/ctl/dmesg_w_exit"
  np dmesg-stream
  check "exit 0" eq "$RC" 0
  check "previous boot kept as .prev" has "$R/data/vendor/gb/report/dmesg-live.prev.txt" "last words of the previous boot"
  check "new file has this boot's header" eq "$(head -n 1 "$R/data/vendor/gb/report/dmesg-live.txt")" "== gb_dgpu_dmesg boot_id=$(cat "$R/proc/sys/kernel/random/boot_id")"
  check "stream content" has "$R/data/vendor/gb/report/dmesg-live.txt" "kernel line of this boot"
  check "dmesg -w used" has "$T/log/dmesg.log" "-w"
  check "synced" exists "$T/log/sync.log"
  np dmesg-stream
  check "restart in the same boot appends, keeps .prev" has "$R/data/vendor/gb/report/dmesg-live.prev.txt" "last words of the previous boot"

  new_case NP-DS-cap
  : > "$T/ctl/dmesg_flood"
  np dmesg-stream
  check "cap: exit 0" eq "$RC" 0
  check "cap reached and stream stopped" has "$R/dev/kmsg" "16 MiB cap reached"
  _sz=$(wc -c < "$R/data/vendor/gb/report/dmesg-live.txt")
  check "file at least 16 MiB" ge "$_sz" 16777216
  check "dmesg -w child killed" eq "$(pgrep -f "^/bin/sleep 300" 2>/dev/null | wc -l | tr -d ' ')" 0
fi

# ================================================================= NP-20 gb_notify

if want NP-20; then
  new_case NP-20
  Q=$R/data/vendor/gb/notify
  printf 'kind=notify\ntag=gb_dgpu\ntitle=NVIDIA GPU is in use\ntext=Close nvk_probe to switch the NVIDIA GPU off.\n' > "$Q/1792900000001-10-1.msg"
  printf 'kind=notify\ntag=evil\ntitle=x\ntext=y\n' > "$Q/1792900000002-10-2.msg"
  printf 'kind=force-stop\npkg=../x;rm -rf /\nuser=0\n' > "$Q/1792900000003-10-3.msg"
  printf 'kind=broadcast\n' > "$Q/1792900000004-10-4.msg"
  printf 'kind=force-stop\npkg=com.example.game\nuser=10\n' > "$Q/1792900000005-10-5.msg"
  printf 'kind=notify\ntag=gb_dtx\ntitle=Bad\001title\ntext=z\n' > "$Q/1792900000006-10-6.msg"
  printf 'kind=notify\ntag=gb_dtx\ntitle=Ready to detach\ntext=You can remove the screen now.\n' > "$Q/1792900000007-10-7.msg"
  printf 'kind=notify\ntag=gb_dgpu\ntitle=in progress\ntext=x\n' > "$Q/1792900000008-10-8.msg.tmp"
  printf 'kind=notify\ntag=gb_dgpu\ntitle=wrong name\ntext=x\n' > "$Q/notes.txt"
  run gb_notify.sh
  check "exit 0" eq "$RC" 0
  check "exactly 2 notification posts" eq "$(nlines "$T/log/cmd.log")" 2
  check "first post exact (queue order)" eq "$(sed -n 1p "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|NVIDIA GPU is in use|gb_dgpu|Close nvk_probe to switch the NVIDIA GPU off.|"
  check "second post exact" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|Ready to detach|gb_dtx|You can remove the screen now.|"
  check "broadcast delivered to the running full user 10 (also to a stopped GbGpu)" has "$T/log/am.log" "broadcast|--user|10|--include-stopped-packages|-a|gb.sb3.gpu.action.STATE_CHANGED|-p|gb.sb3.gpu|"
  check "no broadcast to the headless system user 0 (wave 6)" hasnt "$T/log/am.log" "broadcast|--user|0|"
  check "the user list was read once (one broadcast message)" eq "$(grep -c '^list|users|$' "$T/log/pm.log")" 1
  check "valid force-stop delivered" has "$T/log/am.log" "force-stop|--user|10|com.example.game|"
  check "bad package dropped" hasnt "$T/log/am.log" "rm -rf"
  check "am called twice" eq "$(nlines "$T/log/am.log")" 2
  check "invalid tag logged" has "$R/dev/kmsg" "1792900000002-10-2.msg: bad tag"
  check "non-ASCII title logged" has "$R/dev/kmsg" "1792900000006-10-6.msg: bad title"
  check "no .msg left" eq "$(nmsg)" 0
  check ".tmp in progress untouched" exists "$Q/1792900000008-10-8.msg.tmp"
  check "foreign file name untouched" exists "$Q/notes.txt"
  chmod 0000 "$R/dev/kmsg"
  printf 'kind=notify\ntag=bad\ntitle=x\ntext=y\n' > "$Q/1792900000009-10-9.msg"
  run gb_notify.sh
  chmod 0644 "$R/dev/kmsg"
  check "kmsg not writable (shell user): logcat fallback" has "$T/log/alog.log" "-t gb_notify -p w dropped 1792900000009-10-9.msg: bad tag"
fi

# ================================================================= NP-22 / NP-23 gb_dgpu_report

if want NP-22; then
  for st in driverless loaded; do
    new_case "NP-22-$st"
    [ $st = loaded ] && loaded
    for n in "$R"/dev/dri/*; do rm -f "$n"; mkfifo "$n"; done   # any open() of a DRM node would hang the run
    : > "$T/log/setprop.log"
    _fp=$(fp_tree "$R/sys")
    run gb_dgpu_report.sh report
    check "exit 0 (no DRM open: a FIFO open would time out)" eq "$RC" 0
    check "no sysfs writes" eq "$(fp_tree "$R/sys")" "$_fp"
    check "no property writes" empty "$T/log/setprop.log"
    _n=$(cut -d' ' -f1 "$R/data/vendor/gb/bootseq")
    F=$R/data/vendor/gb/report/dgpu-$_n.txt
    for s in build drm vulkan renderengine pci acpi usb asound firmware power dtx props end; do
      check "section == $s" has "$F" "== $s"
    done
    check "report mode 0640" eq "$(mode_of "$F")" 640
    check "vkjson device name" has "$F" "Intel(R) Iris(R) Plus Graphics (ICL GT2)"
    check "dGPU line" has "$F" "0000:02:00.0 vendor=0x10de device=0x1f91 class=0x030200"
    check "HDA line" has "$F" "0000:02:00.1 vendor=0x10de"
    check "root port line" has "$F" "0000:00:1d.4 vendor=0x8086"
    check "iommu group members" has "$F" "iommu_group=12 members=0000:02:00.0 0000:02:00.1"
    check "30 power samples" eq "$(grep -c 'net_uW=-4123456' "$F")" 30
    check "usb device" has "$F" "1-1 045e:0922 speed=480 product=Surface Keyboard"
    check "acpi tables" has "$F" "tables: DSDT SSDT1 dynamic"
    if [ $st = loaded ]; then
      check "drm map" has "$F" "renderD129 -> 0x10de nouveau"
      check "firmware check (legacy dir)" has "$F" "ok=2 mismatch=0 missing=0"
      check "refcnt" has "$F" "nouveau refcnt=0"
    else
      check "drm map" has "$F" "renderD128 -> 0x8086 i915"
      check "refcnt not loaded" has "$F" "nouveau refcnt=-"
    fi
  done
  new_case NP-22-retention
  for i in $(seq 1 25); do echo old > "$R/data/vendor/gb/report/dgpu-$i.txt"; done
  echo "25 some-older-boot" > "$R/data/vendor/gb/bootseq"
  run gb_dgpu_report.sh report
  check "bootseq advanced to 26" eq "$(cut -d' ' -f1 "$R/data/vendor/gb/bootseq")" 26
  check "newest 20 kept" eq "$(find "$R/data/vendor/gb/report" -name 'dgpu-*.txt' | wc -l | tr -d ' ')" 20
  check "oldest pruned" absent "$R/data/vendor/gb/report/dgpu-6.txt"
  check "dgpu-7 kept" exists "$R/data/vendor/gb/report/dgpu-7.txt"
  run gb_dgpu_report.sh report
  check "same boot: same N" eq "$(cut -d' ' -f1 "$R/data/vendor/gb/bootseq")" 26
fi

if want NP-23; then
  for tc in auto default notloaded; do
    new_case "NP-23-$tc"
    case $tc in
      auto) loaded ;;
      notloaded) cmdline gb.nouveau=test ;;
    esac
    : > "$T/log/setprop.log"
    _fp=$(fp_tree "$R/sys")
    _fr=$(fp_tree "$R/data")
    run gb_dgpu_report.sh test
    check "exit 1" eq "$RC" 1
    check "no sysfs writes" eq "$(fp_tree "$R/sys")" "$_fp"
    check "no /data writes" eq "$(fp_tree "$R/data")" "$_fr"
    check "no property writes" empty "$T/log/setprop.log"
    check "nvk_probe not run" absent "$T/log/nvk_probe.log"
  done

  new_case NP-23-testboot
  cmdline gb.nouveau=test
  np auto
  _u=$(upt)
  printf '%s\n%s\n' $((_u + 500)) $((_u + 560)) > "$T/ctl/resume_at"   # inside the 5-minute suspend phase
  : > "$T/log/setprop.log"
  run gb_dgpu_report.sh test
  F=$R/data/vendor/gb/report/test-1.txt
  check "test boot: exit 0" eq "$RC" 0
  check "d3cold per function" has "$F" "0000:02:00.0 d3cold_s=0"
  check "30 bound-idle samples" eq "$(grep -c 'net_uW=-4123456' "$F")" 30
  check "nvk_probe -t all -n 200" eq "$(head -n 1 "$T/log/nvk_probe.log")" "-t all -n 200"
  check "foreign set before the anb run" has "$T/log/setprop.log" "vendor.gb.nouveau.foreign=1"
  check "foreign file" exists "$R/dev/gb_dgpu/foreign"
  check "30 stress runs" eq "$(grep -c -- '-t offscreen -n 1' "$T/log/nvk_probe.log")" 30
  check "stress summary" has "$F" "pm_stress runs=30 probe_failures=0 runtime_status_error=0 warnings=0 etimedout=0"
  check "autosuspend restored to 5000" eq "$(cat "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/power/autosuspend_delay_ms")" 5000
  check "busy 1 then 0 (stress), 1 then 0 (suspend)" eq "$(grep 'vendor.gb.dgpu.busy=' "$T/log/setprop.log" | cut -d= -f2 | tr -d '\n')" 1010
  check "two resumes recorded" eq "$(grep -c '^resume success=' "$F")" 2
  check "suspend summary" has "$F" "suspends_seen=2"
  check "N13 queued (gb_dgpu_test)" msg_has "title=NVIDIA test: suspend"
  check "N14 queued" msg_has "text=Wait until 15 minutes after start-up, then restart. Results are in the gblog folder on the boot partition."
  check "test tag" msg_has "tag=gb_dgpu_test"
  check "ESP lock released" absent "$R/dev/gb_esp.lock"
fi

# ================================================================= NP-26 noload / boot_completed (R2-5)

if want NP-26; then
  new_case NP-26-crashloop
  echo "mode=persist t=31 argv=runpm=1 modeset=2" > "$R/data/vendor/gb/nouveau.loading"
  np auto
  check "crash-loop: noload flag" exists "$R/dev/gb_dgpu/noload"
  setp persist.vendor.gb.gfx_mode auto           # any app re-enables through the (untrusted) properties
  setp vendor.gb.nouveau.booted 1
  np auto
  check "second auto in the crash-loop boot: refused:noload, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:noload/1
  check "no insmod" absent "$T/log/insmod.log"
  check "no marker" absent "$R/data/vendor/gb/nouveau.loading"
  reboot_dev
  np auto
  check "next boot (gfx_mode=auto again): loads" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-26-kill
  cmdline gb.nouveau=0
  np auto
  check "kill switch: noload flag" exists "$R/dev/gb_dgpu/noload"

  new_case NP-26-unhealthy
  cmdline gb.nouveau=once
  : > "$T/ctl/no_d3cold"
  np auto
  check "unhealthy: keep + noload flags" eq "$(test -e "$R/dev/gb_dgpu/keep" && echo k)$(test -e "$R/dev/gb_dgpu/noload" && echo n)" kn
  np auto
  check "v1.2 (review A2 order): a second auto is refused:noload before the loaded branch" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:noload/1
  check "no second insmod" eq "$(nins)" 1

  new_case NP-26-early
  rm -f "$T/props/sys.boot_completed"
  cmdline gb.nouveau=once
  np auto
  check "before sys.boot_completed: refused:boot, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:boot/1
  check "no insmod" absent "$T/log/insmod.log"
  check "booted unset (the boot_completed trigger runs it later)" eq "$(prop vendor.gb.nouveau.booted)" ""
  echo "mode=persist t=31 argv=x" > "$R/data/vendor/gb/nouveau.loading"
  np auto
  check "early start does not consume the crash-loop marker" exists "$R/data/vendor/gb/nouveau.loading"
fi

# ================================================================= NP-28 detach in progress (R2-2)

if want NP-28; then
  new_case NP-28-wait
  cmdline gb.nouveau=once
  : > "$R/dev/gb_dgpu/detaching"
  _t0=$(upt)
  np auto
  _t1=$(upt)
  check "detach flag for 15 s: refused:detach" eq "$(prop vendor.gb.nouveau.result)" refused:detach
  check "waited about 15 s" ge $((_t1 - _t0)) 15
  check "no ESP lock taken" hasnt "$T/log/setprop.log" "vendor.gb.nouveau.loading=1"
  check "no insmod" absent "$T/log/insmod.log"
  check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1

  new_case NP-28-clears
  cmdline gb.nouveau=once
  : > "$R/dev/gb_dgpu/detaching"
  echo $(($(upt) + 5)) > "$T/ctl/undetach_at"
  np auto
  check "flag gone after 5 s: loads" eq "$(prop vendor.gb.nouveau.result)" clean

  new_case NP-28-late
  cmdline gb.nouveau=once
  : > "$T/ctl/detach_on_dep"
  np auto
  check "detach during the load window: refused:detach" eq "$(prop vendor.gb.nouveau.result)" refused:detach
  check "helpers loaded, nouveau.ko never" eq "$(nins)" 0
  check "marker removed (nothing probed)" absent "$R/data/vendor/gb/nouveau.loading"
  check "lock released, loading=0" eq "$(prop vendor.gb.nouveau.loading)$(test -e "$R/dev/gb_esp.lock" && echo L)" 0
  check "used set" eq "$(prop vendor.gb.nouveau.used)" 1

  new_case NP-28-pulled
  cmdline gb.nouveau=once
  : > "$T/ctl/remove_on_dep"
  np auto
  check "dGPU gone during the load window: refused:nodgpu" eq "$(prop vendor.gb.nouveau.result)" refused:nodgpu
  check "nouveau.ko never" eq "$(nins)" 0
  check "marker removed" absent "$R/data/vendor/gb/nouveau.loading"
fi

# ================================================================= NP-29 oops / killed insmod (R2 low)

if want NP-29; then
  for ok in oops sig; do
    new_case "NP-29-$ok"
    if [ $ok = oops ]; then : > "$T/ctl/oops_on_nouveau"; else : > "$T/ctl/insmod_sig_nouveau"; fi
    np auto
    check "result=insmod-failed:nouveau" eq "$(prop vendor.gb.nouveau.result)" insmod-failed:nouveau
    check "crash-loop marker kept" exists "$R/data/vendor/gb/nouveau.loading"
    check "noload for the rest of the boot" exists "$R/dev/gb_dgpu/noload"
    check "gfx_mode intel (v1.2)" eq "$(prop persist.vendor.gb.gfx_mode)" intel
    reboot_dev
    np auto
    check "next boot: crashloop" eq "$(prop vendor.gb.nouveau.result)" crashloop
  done

  new_case NP-29-bug
  cmdline gb.nouveau=once
  echo "[   31.000000] BUG: unable to handle page fault for address: ffffc90000a00000" > "$T/ctl/dmesg_add"
  np auto
  check "BUG: line without 'nouveau': unhealthy:dmesg" eq "$(prop vendor.gb.nouveau.result)" unhealthy:dmesg

  new_case NP-29-depfail
  cmdline gb.nouveau=once
  : > "$T/ctl/insmod_fail_drm_exec"
  echo "[    5.000000] Oops: old oops from before" > "$R/dev/kmsg"
  np auto
  check "plain helper failure: marker removed (no crash)" absent "$R/data/vendor/gb/nouveau.loading"
fi

# ================================================================= NP-1B: v1.2 graphics switching (TASKS_1B 1.2)

hx() { printf '%064d' 0 | tr 0 "$1"; }    # 64 hex digits of one kind
GFXLINE="apk=$(hx 1) icd=$(hx 2) nvk=$(hx 3) probe=$(hx 4) pay=legacy"
ST_BUILD=CL3B.260622.276.R4/16658739

st_setup() {                             # a bound dGPU (auto load), selftest/ 0700, the identity sources
  loaded || return 1
  mkdir -p "$R/data/vendor/gb/selftest"
  chmod 0700 "$R/data/vendor/gb/selftest"
  printf '%s\n' "$GFXLINE" > "$R/odm/etc/gb/gfx_build_id"
  setp ro.build.id CL3B.260622.276.R4
  setp ro.build.version.incremental 16658739
  : > "$T/log/setprop.log"
}
st() { run gb_dgpu_report.sh selftest; }
STR() { printf '%s' "$R/data/vendor/gb/selftest/result"; }
recv() { sed -n "s/^$1=//p" "$(STR)" 2>/dev/null; }

if want NP-1B-01; then
  new_case NP-1B-01
  np auto
  check "exit 0" eq "$RC" 0
  check "gfx_mode unset: one nouveau insmod" eq "$(nins)" 1
  check "mode=auto" eq "$(prop vendor.gb.nouveau.mode)" auto
  check "probation 1/3 logged" has "$R/data/vendor/gb/report/gb_nouveau.log" "probation 1/3"
  check "vendor.gb.dgpu.dmesg=1" has "$T/log/setprop.log" "vendor.gb.dgpu.dmesg=1"
  check "healthy then clean" eq "$(results)" "loading healthy clean "
  check "renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "marker at the insmod: v1.2 format with this boot's id" eq "$(cut -d' ' -f1,3- "$T/log/marker_at_insmod.log")" "marker=mode=auto boot=$(boot_of) argv=runpm=1 modeset=2"
  check "marker removed at the clean mark" absent "$R/data/vendor/gb/nouveau.loading"
  check "clean_boots 1" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 1
  check "kernel.panic=30 at the insmod (every load, D2)" has "$T/log/insmod_state.log" "nouveau panic=30 oops=1"
  check "auto sets no foreign" eq "$(prop vendor.gb.nouveau.foreign)$(test -e "$R/dev/gb_dgpu/foreign" && echo F)" ""
  check "booted set once" eq "$(grep -c '^vendor.gb.nouveau.booted=' "$T/log/setprop.log")" 1
fi

if want NP-1B-02; then
  for gm in garbage nvidia AUTO; do
    new_case "NP-1B-02-$gm"
    setp persist.vendor.gb.gfx_mode "$gm"
    np auto
    check "gfx_mode=$gm: loads like NP-1B-01" eq "$(nins)/$(prop vendor.gb.nouveau.result)" 1/clean
    check "mode=auto" eq "$(prop vendor.gb.nouveau.mode)" auto
    check "probation 1/3" has "$R/data/vendor/gb/report/gb_nouveau.log" "probation 1/3"
    check "the setting itself is not rewritten" eq "$(prop persist.vendor.gb.gfx_mode)" "$gm"
  done
fi

if want NP-1B-03; then
  new_case NP-1B-03
  intel
  _dd=$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")
  np auto
  check "exit 0" eq "$RC" 0
  check "gfx_mode=intel: result=idle" eq "$(prop vendor.gb.nouveau.result)" idle
  check "mode=default" eq "$(prop vendor.gb.nouveau.mode)" default
  check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
  check "no insmod" absent "$T/log/insmod.log"
  check "no marker" absent "$R/data/vendor/gb/nouveau.loading"
  check "panic untouched" eq "$(cat "$R/proc/sys/kernel/panic")" 0
  echo on > "$_dd/power/control"
  np off                                         # the rc trigger booted=1 && gfx_mode=intel
  check "off: off-done" eq "$(prop vendor.gb.nouveau.result)/$RC" off-done/0
  check "off: power/control=auto (ctl_auto)" eq "$(cat "$_dd/power/control")" auto
  check "no insmod in any path" absent "$T/log/insmod.log"
  check "no unbind, no rmmod" eq "$(nlines "$T/log/unbind.log")$(nlines "$T/log/rmmod.log")" 00
fi

if want NP-1B-04; then
  new_case NP-1B-04-legacy1
  setp persist.vendor.gb.nouveau 1
  intel
  np auto
  check "retired opt-in =1 with gfx_mode=intel: no load" eq "$(prop vendor.gb.nouveau.result)$(nins)" idle0
  new_case NP-1B-04-legacy0
  setp persist.vendor.gb.nouveau 0
  np auto
  check "retired opt-in =0 with gfx_mode unset: loads" eq "$(prop vendor.gb.nouveau.result)$(nins)" clean1
  check "the retired property is never written" hasnt "$T/log/setprop.log" "persist.vendor.gb.nouveau="
  CASE=NP-1B-04-static
  check "no script and no rc names the retired property" eq "$(grep -lE 'persist\.vendor\.gb\.nouveau([^.]|$)' "$NAT"/*.sh "$NAT"/gb_nouveau.rc | wc -l | tr -d ' ')" 0
  check "no script reads the retired vendor.gb.gfxdriver" eq "$(grep -l 'vendor\.gb\.gfxdriver' "$NAT"/*.sh | wc -l | tr -d ' ')" 0
fi

if want NP-1B-05; then
  for mk in other v11; do
    new_case "NP-1B-05-$mk"
    echo 2 > "$R/data/vendor/gb/nouveau.clean_boots"
    case $mk in
      other) echo "mode=auto t=40 boot=0b6e2c1d-1111-2222-3333-444455556666 argv=runpm=1 modeset=2" > "$R/data/vendor/gb/nouveau.loading" ;;
      v11) echo "mode=persist t=31 argv=runpm=1 modeset=2" > "$R/data/vendor/gb/nouveau.loading" ;;
    esac
    np auto
    check "exit 0, result=crashloop" eq "$RC/$(prop vendor.gb.nouveau.result)" 0/crashloop
    check "marker removed" absent "$R/data/vendor/gb/nouveau.loading"
    check "setprop persist.vendor.gb.gfx_mode=intel" hasline "$T/log/setprop.log" "persist.vendor.gb.gfx_mode=intel"
    check "never persist.vendor.gb.nouveau" hasnt "$T/log/setprop.log" "persist.vendor.gb.nouveau="
    check "clean_boots=0" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 0
    check "N01 title" msg_has "title=Switched to Intel graphics"
    check "N01 text (exact v1.2)" msg_has "text=The NVIDIA driver did not start cleanly last time, so graphics were set to Intel only. To try again, choose Auto in Settings > System > Graphics."
    check "N01 tag" msg_has "tag=gb_dgpu"
    check "no insmod" absent "$T/log/insmod.log"
    check "booted unset" eq "$(prop vendor.gb.nouveau.booted)" ""
    check "noload" exists "$R/dev/gb_dgpu/noload"
    check "mode=default" eq "$(prop vendor.gb.nouveau.mode)" default
  done

  new_case NP-1B-05-thisboot
  echo "mode=rebind t=200 boot=$(boot_of) argv=-" > "$R/data/vendor/gb/nouveau.loading"
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4250"
  echo "4250 gb_power 200" > "$R/dev/gb_esp.lock/owner"   # gb_power's rebind guard holds the lock (23.8)
  _t0=$(upt)
  np auto
  _t1=$(upt)
  check "a marker of this boot is no crash-loop: the run waits for the ESP lock" eq "$(prop vendor.gb.nouveau.result)" refused:esp
  check "waited >= 120 s" ge $((_t1 - _t0)) 120
  check "the marker of this boot is left alone" eq "$(mark_line)" "mode=rebind t=200 boot=$(boot_of) argv=-"
  check "no insmod" absent "$T/log/insmod.log"
  check "gfx_mode not touched" eq "$(prop persist.vendor.gb.gfx_mode)" ""
  check "no noload" absent "$R/dev/gb_dgpu/noload"
  check "logged" has "$R/dev/kmsg" "belongs to another window of this boot"
  check "N01 not queued" eq "$(nmsg)" 0

  new_case NP-1B-05-thisboot-free
  echo "mode=rebind t=200 boot=$(boot_of) argv=-" > "$R/data/vendor/gb/nouveau.loading"
  np auto
  check "lock free: the load runs (the ESP lock serializes the windows)" eq "$(prop vendor.gb.nouveau.result)" clean
  check "its own marker replaced it" has "$T/log/marker_at_insmod.log" "marker=mode=auto t="
  check "removed at the clean mark" absent "$R/data/vendor/gb/nouveau.loading"
fi

if want NP-1B-06; then
  for w in d3cold dmesg sig oops; do
    new_case "NP-1B-06-$w"
    case $w in
      d3cold) : > "$T/ctl/no_d3cold" ;;
      dmesg) echo "[   31.000000] nouveau 0000:02:00.0: gsp: timeout waiting for GSP RPC" > "$T/ctl/dmesg_add" ;;
      sig) : > "$T/ctl/insmod_sig_nouveau" ;;
      oops) : > "$T/ctl/oops_on_nouveau" ;;
    esac
    np auto
    check "keep and noload exist before the gfx_mode=intel setprop" eq "$(cat "$T/log/order.log" 2>/dev/null)" "setprop persist.vendor.gb.gfx_mode=intel keep=1 noload=1 wedged=0"
    case $w in
      d3cold | dmesg)
        check "result=unhealthy:$w" eq "$(prop vendor.gb.nouveau.result)" "unhealthy:$w"
        check "N02 title" msg_has "title=NVIDIA GPU problem"
        check "N02 text (exact v1.2)" msg_has "text=The NVIDIA driver started with errors ($w). Graphics are set to Intel only."
        _fp=$(fp_tree "$R/sys")
        np off
        check "the following off writes nothing to sysfs" eq "$(fp_tree "$R/sys")" "$_fp"
        ;;
      *)
        check "killed insmod: insmod-failed:nouveau, marker kept" eq "$(prop vendor.gb.nouveau.result)$(test -e "$R/data/vendor/gb/nouveau.loading" && echo M)" insmod-failed:nouveauM
        np off
        ;;
    esac
    check "off: no unbind, no rmmod" eq "$(nlines "$T/log/unbind.log")$(nlines "$T/log/rmmod.log")" 00
  done
fi

if want NP-1B-07; then
  new_case NP-1B-07
  rm -f "$R/sys/bus/pci/devices/0000:02:00.0" "$R/sys/bus/pci/devices/0000:02:00.1"
  np auto
  check "base detached: refused:nodgpu, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:nodgpu/1
  check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
  check "no insmod" absent "$T/log/insmod.log"
  ln -s "$R/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0" "$R/sys/bus/pci/devices/0000:02:00.0"
  ln -s "$R/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1" "$R/sys/bus/pci/devices/0000:02:00.1"
  np auto                                        # gb_power's hot attach start (23.7)
  check "reattached: the second run loads" eq "$(prop vendor.gb.nouveau.result)/$(nins)" clean/1
  check "booted set once over both runs (review E2)" eq "$(grep -c '^vendor.gb.nouveau.booted=' "$T/log/setprop.log")" 1
fi

if want NP-1B-08; then
  new_case NP-1B-08
  mod_only
  np auto
  B=$T/log/bind.log
  check "exit 0" eq "$RC" 0
  check "bind path: no insmod" absent "$T/log/insmod.log"
  check "healthy then clean" eq "$(results)" "loading healthy clean "
  check "one bind write" eq "$(nlines "$B")" 1
  check "the BDF went into drivers/nouveau/bind" eq "$(cut -d' ' -f1 "$B")" 0000:02:00.0
  check "marker mode=bind of this boot before the write" eq "$(sed 's/.* marker=\(.*\) lock=.*/\1/' "$B" | cut -d' ' -f1,3-)" "mode=bind boot=$(boot_of) argv=-"
  check "synced before the write" ge "$(sed 's/.* syncs=\([0-9]*\) .*/\1/' "$B")" 1
  check "ESP lock owner gb_nouveau during the write" has "$B" " lock=gb_nouveau "
  check "kernel.panic=30 before the write" has "$B" " panic=30 "
  check "bound to nouveau" eq "$(readlink "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/driver" | sed 's#.*/##')" nouveau
  check "the HDA function stays driverless" absent "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.1")/driver"
  check "renderD129 0660, card1 0600" eq "$(mode_of "$R/dev/dri/renderD129")/$(mode_of "$R/dev/dri/card1")" 660/600
  check "clean_boots unchanged (review C8)" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 2
  check "marker removed at the clean mark" absent "$R/data/vendor/gb/nouveau.loading"
  check "used" eq "$(prop vendor.gb.nouveau.used)" 1
  check "loading 1 then 0" eq "$(grep 'vendor.gb.nouveau.loading=' "$T/log/setprop.log" | tr '\n' ' ')" "vendor.gb.nouveau.loading=1 vendor.gb.nouveau.loading=0 "
  check "lock released" absent "$R/dev/gb_esp.lock"
  check "bind record mode=bind health=clean" eq "$(grep -E '^(mode|health)=' "$R/data/vendor/gb/report/bind-1.txt" | tr '\n' ' ')" "mode=bind health=clean "
  check "no insmod load record" absent "$R/data/vendor/gb/report/load-1.txt"
  check "booted=1, mode=auto" eq "$(prop vendor.gb.nouveau.booted)/$(prop vendor.gb.nouveau.mode)" 1/auto
  check "no foreign" eq "$(prop vendor.gb.nouveau.foreign)" ""

  new_case NP-1B-08-nobind
  mod_only
  : > "$T/ctl/nobind"                            # the write returns, the probe failed (-110)
  np auto
  check "write returned, not bound: unhealthy:bind" eq "$(prop vendor.gb.nouveau.result)/$RC" unhealthy:bind/1
  check "keep + noload, gfx_mode=intel" eq "$(test -e "$R/dev/gb_dgpu/keep" && echo k)$(test -e "$R/dev/gb_dgpu/noload" && echo n)/$(prop persist.vendor.gb.gfx_mode)" kn/intel
  check "N02 (bind)" msg_has "text=The NVIDIA driver started with errors (bind). Graphics are set to Intel only."
  check "marker removed (this boot survived)" absent "$R/data/vendor/gb/nouveau.loading"
  check "not wedged" absent "$R/dev/gb_dgpu/wedged"
  check "lock released" absent "$R/dev/gb_esp.lock"

  new_case NP-1B-08-payload
  payload_mode
  cmdline gb.nouveau=once
  np auto                                        # insmod from the payload, then simulate a detach + reattach
  _dd=$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")
  rm -f "$_dd/driver"
  rm -rf "$_dd/drm" "$R/sys/class/drm/card1" "$R/sys/class/drm/renderD129" "$R/dev/dri/card1" "$R/dev/dri/renderD129"
  : > "$T/log/mount.log"
  np auto
  check "payload mode: the bind path verifies the mounted payload, no remount" eq "$(nlines "$T/log/mount.log")" 0
  check "binds" eq "$(prop vendor.gb.nouveau.result)" clean
  check "one insmod in the boot" eq "$(nins)" 1
fi

if want NP-1B-09; then
  new_case NP-1B-09
  mod_only
  rm -f "$R/sys/bus/pci/drivers/nouveau/bind"
  mkfifo "$R/sys/bus/pci/drivers/nouveau/bind"
  _t0=$(upt)
  np auto
  _t1=$(upt)
  timeout 5 cat "$R/sys/bus/pci/drivers/nouveau/bind" > "$T/fifo_got" 2>/dev/null   # release the stuck writer
  check "exit 1, result=bind-failed" eq "$RC/$(prop vendor.gb.nouveau.result)" 1/bind-failed
  check "within 21 fake s" le $((_t1 - _t0)) 21
  check "wedged, keep, noload" eq "$(test -e "$R/dev/gb_dgpu/wedged" && echo w)$(test -e "$R/dev/gb_dgpu/keep" && echo k)$(test -e "$R/dev/gb_dgpu/noload" && echo n)" wkn
  check "gfx_mode=intel" eq "$(prop persist.vendor.gb.gfx_mode)" intel
  check "marker kept (mode=bind)" has "$R/data/vendor/gb/nouveau.loading" "mode=bind "
  check "N05 title" msg_has "title=Restart required"
  check "N05 text" msg_has "text=The NVIDIA GPU stopped responding. Restart before detaching the screen."
  check "loading=0, lock released, booted=1" eq "$(prop vendor.gb.nouveau.loading)$(test -e "$R/dev/gb_esp.lock" && echo L)$(prop vendor.gb.nouveau.booted)" 01
  check "the hung write carried the BDF" eq "$(cat "$T/fifo_got")" 0000:02:00.0
  _fp=$(fp_tree "$R/sys")
  np auto
  check "auto afterwards: refused:wedged" eq "$(prop vendor.gb.nouveau.result)" refused:wedged
  np off
  check "off afterwards: off-wedged" eq "$(prop vendor.gb.nouveau.result)" off-wedged
  check "no further sysfs write" eq "$(fp_tree "$R/sys")" "$_fp"
fi

if want NP-1B-10; then
  for rc in i915 bar esp-live esp-vfat detach detach-late; do
    new_case "NP-1B-10-$rc"
    mod_only
    _exp=refused:esp
    case $rc in
      i915)
        mk_pci 0000:00:0f.0 '' 0x15ad 0x0405 0x030000 vmwgfx 2
        rm -rf "$R/sys/class/drm/renderD128" "$R/sys/class/drm/card0"
        mk_drm 0000:00:0f.0 renderD128
        mk_drm 0000:00:0f.0 card0
        _exp=refused:i915 ;;
      bar)
        printf '0x0000000000000000 0x0000000000000000 0x0000000000000000\n' > "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/resource"
        _exp=refused:bar ;;
      esp-live)
        mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4242"
        echo "4242 gb_bootlog 20" > "$R/dev/gb_esp.lock/owner" ;;
      esp-vfat) echo "/dev/block/by-name/EFI-SYSTEM /mnt/gb_esp vfat rw,flush 0 0" >> "$R/proc/mounts" ;;
      detach) : > "$R/dev/gb_dgpu/detaching"; _exp=refused:detach ;;
      detach-late) : > "$T/ctl/detach_on_bind_marker"; _exp=refused:detach ;;
    esac
    np auto
    check "$_exp, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" "$_exp/1"
    check "never a bind write" nobindw
    check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
    check "no marker left" absent "$R/data/vendor/gb/nouveau.loading"
    check "no insmod" absent "$T/log/insmod.log"
    case $rc in
      esp-live) check "foreign lock untouched" eq "$(cat "$R/dev/gb_esp.lock/owner")" "4242 gb_bootlog 20" ;;
      detach-late)
        check "the marker was written, then removed" has "$R/dev/kmsg" "a detach started during the bind window"
        check "lock released, loading=0" eq "$(prop vendor.gb.nouveau.loading)$(test -e "$R/dev/gb_esp.lock" && echo L)" 0 ;;
      *) check "lock released" absent "$R/dev/gb_esp.lock" ;;
    esac
  done

  # NP-1B-D7: the bind path waits for the ESP lock that gb_power's rebind guard holds (INTERFACES 23.8)
  for gw in guardfail kbound; do
    new_case "NP-1B-10-$gw"
    mod_only
    _bid=$(boot_of)
    echo "mode=rebind t=20 boot=$_bid argv=-" > "$R/data/vendor/gb/nouveau.loading"
    mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4251"
    echo "4251 gb_power 20" > "$R/dev/gb_esp.lock/owner"
    _t0=$(upt)
    if [ $gw = guardfail ]; then              # the guard fails at +30 s: keep + noload, unmark, release
      printf '%s touch dev/gb_dgpu/keep\n%s touch dev/gb_dgpu/noload\n%s rm data/vendor/gb/nouveau.loading\n%s rm dev/gb_esp.lock\n' \
        $((_t0 + 30)) $((_t0 + 30)) $((_t0 + 30)) $((_t0 + 31)) > "$T/ctl/at"
    else                                      # the kernel's re-probe binds at +20 s, the guard releases at +21 s
      printf '%s kbind 0000:02:00.0\n%s rm dev/gb_esp.lock\n' $((_t0 + 20)) $((_t0 + 21)) > "$T/ctl/at"
    fi
    np auto
    check "never a bind write" nobindw
    check "lock not left behind" absent "$R/dev/gb_esp.lock"
    if [ $gw = guardfail ]; then
      check "noload appeared during the lock wait: refused:noload, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:noload/1
      check "no marker written" absent "$R/data/vendor/gb/nouveau.loading"
      check "loading never set" hasnt "$T/log/setprop.log" "vendor.gb.nouveau.loading=1"
    else
      check "bound while waiting: permissions only, exit 0" eq "$RC" 0
      check "renderD129 0660 (the kernel created it 0666)" eq "$(mode_of "$R/dev/dri/renderD129")" 660
      check "gb_power's marker of this boot untouched" eq "$(mark_line)" "mode=rebind t=20 boot=$_bid argv=-"
      check "no load window opened" hasnt "$T/log/setprop.log" "vendor.gb.nouveau.loading=1"
      check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
    fi
  done
fi

if want NP-1B-11; then
  for gc in noload keep kill boot marker; do
    new_case "NP-1B-11-$gc"
    mod_only
    case $gc in
      noload) : > "$R/dev/gb_dgpu/noload"; _exp=refused:noload/1 ;;
      keep) : > "$R/dev/gb_dgpu/keep"; _exp=refused:keep/1 ;;
      kill) cmdline gb.nouveau=0; _exp=disabled/0 ;;
      boot) rm -f "$T/props/sys.boot_completed"; _exp=refused:boot/1 ;;
      marker) echo "mode=bind t=99 boot=an-earlier-boot argv=-" > "$R/data/vendor/gb/nouveau.loading"; _exp=crashloop/0 ;;
    esac
    np auto
    check "$gc: $_exp" eq "$(prop vendor.gb.nouveau.result)/$RC" "$_exp"
    check "never a bind write" nobindw
    check "no insmod" absent "$T/log/insmod.log"
    if [ $gc = keep ]; then
      check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
    else
      check "booted unset" eq "$(prop vendor.gb.nouveau.booted)" ""
    fi
  done
fi

if want NP-1B-12; then
  new_case NP-1B-12-wait
  mod_only
  setp init.svc.gb_nouveau_off running
  _t0=$(upt)
  echo "$((_t0 + 10)) init.svc.gb_nouveau_off stopped" > "$T/ctl/prop_at"
  np auto
  check "waits for off, then binds" eq "$(prop vendor.gb.nouveau.result)" clean
  check "the bind write came after the 10 s" ge "$(sed 's/.* at=\([0-9]*\).*/\1/' "$T/log/bind.log")" $((_t0 + 10))
  check "one bind write" eq "$(nlines "$T/log/bind.log")" 1

  new_case NP-1B-12-stuck
  mod_only
  setp init.svc.gb_nouveau_off running
  _t0=$(upt)
  np auto
  _t1=$(upt)
  check "off still running after 30 s: refused:off, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:off/1
  check "after 30 s" eq "$(ge $((_t1 - _t0)) 30 && le $((_t1 - _t0)) 32 && echo y)" y
  check "never a bind write" nobindw
  check "no lock taken" hasnt "$T/log/setprop.log" "vendor.gb.nouveau.loading=1"
  check "booted=1" eq "$(prop vendor.gb.nouveau.booted)" 1
fi

if want NP-1B-13; then
  new_case NP-1B-13
  cmdline "gb.gfxdriver=1"
  setp vendor.gb.gfxdriver on
  : > "$R/dev/gb_dgpu/gfxdriver"
  np auto
  check "loads (auto)" eq "$(prop vendor.gb.nouveau.result)" clean
  check "renderD129 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "auto sets no foreign (property)" eq "$(prop vendor.gb.nouveau.foreign)" ""
  check "auto sets no foreign (file)" absent "$R/dev/gb_dgpu/foreign"
  check "no chmod 0666 by the script" hasnt "$T/log/chmod.log" "0666 /dev/dri/renderD129"
fi

if want NP-1B-14; then
  new_case NP-1B-14-auto
  loaded
  setp persist.vendor.gb.gfx_mode auto
  : > "$T/log/chmod.log"
  : > "$T/log/setprop.log"
  np off
  check "off with gfx_mode=auto: off-skipped, exit 0" eq "$(prop vendor.gb.nouveau.result)/$RC" off-skipped/0
  check "no unbind, no rmmod" eq "$(nlines "$T/log/unbind.log")$(nlines "$T/log/rmmod.log")" 00
  check "no chmod 000" hasnt "$T/log/chmod.log" "000 /dev/dri"
  check "no service stopped" hasnt "$T/log/setprop.log" "ctl.stop"
  check "still bound" exists "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/driver"

  new_case NP-1B-14-flip
  loaded
  intel
  awk '{ print $1 + 0.5, "persist.vendor.gb.gfx_mode", "auto" }' "$R/proc/uptime" > "$T/ctl/prop_at"
  : > "$T/log/chmod.log"
  np off
  check "mode flips to auto during the chmod-000 second: off-skipped" eq "$(prop vendor.gb.nouveau.result)/$RC" off-skipped/0
  check "the nodes were at 000 first" has "$T/log/chmod.log" "000 /dev/dri/renderD129"
  check "permissions back to 0660 / 0600" eq "$(mode_of "$R/dev/dri/renderD129")/$(mode_of "$R/dev/dri/card1")" 660/600
  check "no unbind, no rmmod" eq "$(nlines "$T/log/unbind.log")$(nlines "$T/log/rmmod.log")" 00
fi

if want NP-1B-15; then
  new_case NP-1B-15
  loaded
  intel
  : > "$R/dev/gb_dgpu/foreign"
  np off
  check "foreign boot, gfx_mode=intel: off-unbound, exit 0" eq "$(prop vendor.gb.nouveau.result)/$RC" off-unbound/0
  check "no rmmod" absent "$T/log/rmmod.log"
  check "module stays loaded" exists "$R/sys/module/nouveau"
  check "no N04 (no notification at all)" eq "$(nmsg)" 0
  check "/dev/gb_dgpu/off still created" exists "$R/dev/gb_dgpu/off"
  check "ctl.stop of the four GPU users" eq "$(grep '^ctl.stop=' "$T/log/setprop.log" | tr '\n' ' ')" "ctl.stop=gb_dgpu_test ctl.stop=gb_nvk_probe ctl.stop=gb_gpu_compute ctl.stop=gb_gfx_selftest "
  setp persist.vendor.gb.gfx_mode auto           # the user picks Auto again: the bind path (7.2a)
  np auto
  check "then Auto again: the bind path binds the kept module" eq "$(prop vendor.gb.nouveau.result)/$(nlines "$T/log/bind.log")" clean/1
fi

if want NP-1B-16; then
  CASE=NP-1B-16
  RCF=$NAT/gb_nouveau.rc
  sh "$HERE/rc_lint.sh" "$RCF" > "$WORK/rc_lint_1b.txt" 2>&1
  check "rc_lint exit 0" eq "$?" 0
  check "7 services, 0 findings" has "$WORK/rc_lint_1b.txt" ": 7 services, 0 findings"
  check "service gb_gfx_selftest" hasline "$RCF" "service gb_gfx_selftest /system/bin/sh /vendor/bin/gb_dgpu_report.sh selftest"
  check "mkdir selftest/" hasline "$RCF" "    mkdir /data/vendor/gb/selftest 0700 root root"
  for m in auto nvidia intel; do
    check "trigger gfx_mode=$m" hasline "$RCF" "on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=$m"
  done
  check "no persist.vendor.gb.nouveau trigger" hasnt "$RCF" "property:persist.vendor.gb.nouveau="
  check "no gb_gfxdriver service" hasnt "$RCF" "gb_gfxdriver"
  if [ -n "${GB_INTERFACES:-}" ] && [ -r "$GB_INTERFACES" ]; then
    awk '/^#### 15.1-v12 /{f=1} f && /^```$/{n++; if (n == 2) exit; next} f && n == 1' "$GB_INTERFACES" > "$WORK/rc_spec_v12.txt"
    grep -v '^#' "$RCF" > "$WORK/rc_body_v12.txt"
    check "rc body byte-identical to INTERFACES 15.1-v12" eq "$(cmp "$WORK/rc_spec_v12.txt" "$WORK/rc_body_v12.txt" > "$WORK/rc_cmp.txt" 2>&1; echo $?)" 0
    check "15.1-v12 block found (7 services)" eq "$(grep -c '^service ' "$WORK/rc_spec_v12.txt")" 7
    [ -s "$WORK/rc_cmp.txt" ] && cat "$WORK/rc_cmp.txt"
  else
    printf 'note NP-1B-16: GB_INTERFACES not set, exact 15.1-v12 comparison skipped\n'
  fi
fi

if want NP-1B-17; then
  new_case NP-1B-17
  Q=$R/data/vendor/gb/notify
  G=$R/data/vendor/gb
  K=$T/log/cmd.log
  OI=updatable_driver_production_opt_in_apps
  OO=updatable_driver_production_opt_out_apps
  printf 'kind=settings\nkey=updatable_driver_all_apps\nvalue=0\n' > "$Q/1792900000001-20-1.msg"
  printf 'kind=settings\nkey=updatable_driver_all_apps\nvalue=3\n' > "$Q/1792900000002-20-2.msg"
  printf 'kind=settings\nkey=updatable_driver_all_apps\nvalue=1\n' > "$Q/1792900000003-20-3.msg"
  printf 'kind=settings\nkey=updatable_driver_all_apps\nvalue=0;reboot\n' > "$Q/1792900000004-20-4.msg"
  printf 'kind=settings\nkey=adb_enabled\nvalue=1\n' > "$Q/1792900000005-20-5.msg"
  printf 'kind=settings\nkey=%s\nsrc=gfx_optout\n' "$OI" > "$Q/1792900000006-20-6.msg"
  printf 'kind=settings\nkey=updatable_driver_all_apps\nsrc=gfx_optin\n' > "$Q/1792900000007-20-7.msg"
  printf 'kind=settings\nkey=%s\nsrc=../../../system/packages.list\n' "$OI" > "$Q/1792900000008-20-8.msg"
  printf 'kind=settings\nkey=%s\nvalue=com.evil.app\n' "$OI" > "$Q/1792900000009-20-9.msg"
  printf 'kind=settings\nkey=updatable_driver_all_apps\nvalue=0\nsrc=gfx_optin\n' > "$Q/1792900000010-20-10.msg"
  printf 'kind=settings\nkey=%s\nsrc=gfx_optin\n' "$OI" > "$Q/1792900000011-20-11.msg"
  printf 'com.example.game\ncom.example.racer\n' > "$G/gfx_optin"
  run gb_notify.sh
  check "exit 0" eq "$RC" 0
  check "all_apps 0" eq "$(sed -n 1p "$K")" "settings|put|global|updatable_driver_all_apps|0|"
  check "all_apps 3" eq "$(sed -n 2p "$K")" "settings|put|global|updatable_driver_all_apps|3|"
  check "valid opt-in file: joined in file order" eq "$(sed -n 3p "$K")" "settings|put|global|$OI|com.example.game,com.example.racer|"
  check "exactly 3 settings writes" eq "$(nlines "$K")" 3
  check "value 1 dropped, logged" has "$R/dev/kmsg" "1792900000003-20-3.msg: bad value for updatable_driver_all_apps"
  check "value 0;reboot dropped, logged" has "$R/dev/kmsg" "1792900000004-20-4.msg: bad value for updatable_driver_all_apps"
  check "unknown key dropped" has "$R/dev/kmsg" "1792900000005-20-5.msg: settings key not allowed"
  check "key/src mismatch dropped" has "$R/dev/kmsg" "1792900000006-20-6.msg: bad settings pairing"
  check "all_apps with src dropped" has "$R/dev/kmsg" "1792900000007-20-7.msg: bad settings pairing"
  check "a path as src dropped" has "$R/dev/kmsg" "1792900000008-20-8.msg: bad settings pairing"
  check "opt-in with a value dropped" has "$R/dev/kmsg" "1792900000009-20-9.msg: bad settings pairing"
  check "value and src together dropped" has "$R/dev/kmsg" "1792900000010-20-10.msg: bad settings pairing"
  check "queue empty" eq "$(nmsg)" 0
  check "never adb_enabled" hasnt "$K" "adb_enabled"

  NQN=100
  sq() {                                 # $1 in|out: queue one list message and run gb_notify
    NQN=$((NQN + 1))
    : > "$K"
    : > "$R/dev/kmsg"
    printf 'kind=settings\nkey=updatable_driver_production_opt_%s_apps\nsrc=gfx_opt%s\n' "$1" "$1" > "$Q/1792900000$NQN-21-$NQN.msg"
    run gb_notify.sh
  }
  # shellcheck disable=SC2016  # the $(id) must reach the file literally
  printf 'com.good.one\nnot a package\n../etc/passwd\ncom.good.one\n\ncom.good.two\nx\ncom.$(id)\n_bad.start\ncom.trailing.\n' > "$G/gfx_optin"
  sq in
  check "bad lines dropped, de-duplicated, file order" eq "$(cat "$K")" "settings|put|global|$OI|com.good.one,com.good.two|"
  check "bad lines logged" eq "$(grep -c 'gfx_optin line [0-9]* dropped' "$R/dev/kmsg")" 6
  : > "$G/gfx_optin"
  sq in
  check "empty file: delete" eq "$(cat "$K")" "settings|delete|global|$OI|"
  rm -f "$G/gfx_optin"
  printf 'com.example.game\n' > "$T/elsewhere"
  ln -s "$T/elsewhere" "$G/gfx_optin"
  sq in
  check "symlink: dropped" eq "$(nlines "$K")/$(grep -c 'gfx_optin is not a regular file' "$R/dev/kmsg")" 0/1
  rm -f "$G/gfx_optin"
  sq in
  check "missing file: dropped" eq "$(nlines "$K")" 0
  printf 'com.example.game\n' > "$G/gfx_optin"
  echo 1000 > "$T/ctl/uid_gfx_optin"
  sq in
  check "owned by uid 1000: dropped, logged" eq "$(nlines "$K")/$(grep -c "not owned by root (uid '1000')" "$R/dev/kmsg")" 0/1
  rm -f "$T/ctl/uid_gfx_optin"
  seq 1 513 | sed 's/^/com.example.app/' > "$G/gfx_optin"
  sq in
  check "513 lines: dropped" eq "$(nlines "$K")/$(grep -c 'gfx_optin has more than 512 lines' "$R/dev/kmsg")" 0/1
  seq 1 512 | sed 's/^/com.example.app/' > "$G/gfx_optin"
  sq in
  check "512 lines: written" eq "$(tr '|' '\n' < "$K" | sed -n 5p | tr ',' '\n' | wc -l | tr -d ' ')" 512
  awk 'BEGIN { for (i = 1; i <= 300; i++) { s = "com.example.p" i "."; while (length(s) < 240) s = s "x"; print s } }' > "$G/gfx_optin"
  sq in
  check "> 64 KiB: dropped" eq "$(nlines "$K")/$(grep -c 'gfx_optin is larger than 64 KiB' "$R/dev/kmsg")" 0/1
  seq 1 129 | sed 's/^/com.example.intel/' > "$G/gfx_optout"
  sq out
  check "opt-out with 129 lines: dropped" eq "$(nlines "$K")/$(grep -c 'gfx_optout has more than 128 lines' "$R/dev/kmsg")" 0/1
  printf 'com.example.intel1\ncom.example.intel2\n' > "$G/gfx_optout"
  sq out
  check "opt-out written" eq "$(cat "$K")" "settings|put|global|$OO|com.example.intel1,com.example.intel2|"
fi

# (wave 6, notify) headless system user mode: notices for UserHandle.ALL, state broadcasts per running full user
if want NP-W6-NOTIFY; then
  new_case NP-W6-NOTIFY
  Q=$R/data/vendor/gb/notify
  bq() { printf 'kind=broadcast\n' > "$Q/1792900000$1-50-$1.msg"; }
  # 1: three running full users (10, 11, 12), a profile (13), the headless user 0, a stopped full user (14); a name
  #    holding ':' and '}' (the flags are the field after the last ':')
  printf '%s\n' '	UserInfo{0:System User:811} running' '	UserInfo{10:Pe:rs}on:412} running' \
    '	UserInfo{11:Second:412} running' '	UserInfo{12:Guest:414} running' '	UserInfo{13:Work:1030} running' \
    '	UserInfo{14:Stopped:412}' '	UserInfo{15:x:412} running:10} running' '	UserInfo{1x:Bad:412} running' \
    > "$T/ctl/users"
  bq 101
  run gb_notify.sh
  check "exit 0" eq "$RC" 0
  check "broadcast to 10, 11, 12 in list order, nothing else" eq "$(tr '\n' ' ' < "$T/log/am.log")" \
    "broadcast|--user|10|--include-stopped-packages|-a|gb.sb3.gpu.action.STATE_CHANGED|-p|gb.sb3.gpu| broadcast|--user|11|--include-stopped-packages|-a|gb.sb3.gpu.action.STATE_CHANGED|-p|gb.sb3.gpu| broadcast|--user|12|--include-stopped-packages|-a|gb.sb3.gpu.action.STATE_CHANGED|-p|gb.sb3.gpu| "
  # 2: only the headless user runs (the login screen before the sign-in): nothing sent, logged, GbGpu refreshes later
  printf '\tUserInfo{0:System User:811} running\n\tUserInfo{10:User:412}\n' > "$T/ctl/users"
  : > "$T/log/am.log"
  bq 102
  run gb_notify.sh
  check "login screen only: no broadcast" eq "$(nlines "$T/log/am.log")" 0
  check "login screen only: logged" has "$R/dev/kmsg" "no running full user: state broadcast 1792900000102-50-102.msg skipped"
  # 3: pm list users fails: one broadcast to --user current, logged
  : > "$T/ctl/pm_fail"
  : > "$T/log/am.log"
  bq 103
  run gb_notify.sh
  rm -f "$T/ctl/pm_fail"
  check "no user list: one broadcast to --user current" eq "$(cat "$T/log/am.log")" "broadcast|--user|current|--include-stopped-packages|-a|gb.sb3.gpu.action.STATE_CHANGED|-p|gb.sb3.gpu|"
  check "no user list: logged" has "$R/dev/kmsg" "pm list users failed: state broadcast 1792900000103-50-103.msg sent to --user current"
  # 4: am broadcast fails: logged per user, the message is still deleted, the next message is delivered
  rm -f "$T/ctl/users"
  : > "$T/ctl/am_fail"
  : > "$T/log/am.log"
  bq 104
  printf 'kind=notify\ntag=gb_dgpu\ntitle=t\ntext=x\n' > "$Q/1792900000105-50-105.msg"
  run gb_notify.sh
  rm -f "$T/ctl/am_fail"
  check "broadcast failure logged (user 10)" has "$R/dev/kmsg" "am broadcast failed for 1792900000104-50-104.msg (user 10)"
  check "after a failed broadcast the notice is still posted" has "$T/log/cmd.log" "notification|post|-u|all|-S|bigtext|-t|t|gb_dgpu|x|"
  check "queue empty" eq "$(nmsg)" 0
  # 5: every notice is posted for UserHandle.ALL, exactly once, never without -u all
  : > "$T/log/cmd.log"
  printf 'kind=notify\ntag=gb_update\ntitle=System update held\ntext=-u 0 starts like an option\n' > "$Q/1792900000106-50-106.msg"
  run gb_notify.sh
  check "one post, for all users, text kept as one argument" eq "$(cat "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|System update held|gb_update|-u 0 starts like an option|"
  check "never a post without -u all" eq "$(grep -c '^notification|post|' "$T/log/cmd.log")" "$(grep -c '^notification|post|-u|all|' "$T/log/cmd.log")"
  # 6: a message gb_notify cannot read (a root producer without umask 022: the notify proof found gb_otagate's 0600)
  printf 'kind=notify
tag=gb_dgpu
title=t
text=x
' > "$Q/1792900000107-50-107.msg"
  chmod 0000 "$Q/1792900000107-50-107.msg"
  : > "$T/log/cmd.log"
  run gb_notify.sh
  check "unreadable message: dropped and logged" has "$R/dev/kmsg" "dropped 1792900000107-50-107.msg: not readable by gb_notify (12.1: mode 0644)"
  check "unreadable message: nothing posted" eq "$(nlines "$T/log/cmd.log")" 0
  check "unreadable message: deleted all the same" eq "$(nmsg)" 0
fi

# (wave 7, notify O14) at most 8 running full users get the state broadcast
if want NP-W7-CAP; then
  new_case NP-W7-CAP
  Q=$R/data/vendor/gb/notify
  { printf '\tUserInfo{0:System User:811} running\n'; i=10; while [ $i -lt 20 ]; do printf '\tUserInfo{%s:U%s:412} running\n' $i $i; i=$((i + 1)); done; } > "$T/ctl/users"
  printf 'kind=broadcast\n' > "$Q/1792900000201-60-1.msg"
  run gb_notify.sh
  check "exit 0" eq "$RC" 0
  check "10 running full users: 8 broadcasts" eq "$(nlines "$T/log/am.log")" 8
  check "the first 8 in list order (10..17)" eq "$(cut -d'|' -f3 "$T/log/am.log" | tr '\n' ' ')" "10 11 12 13 14 15 16 17 "
  rm -f "$T/ctl/users"
fi

# (wave 7, notify O5) cmd notification post exits 0 when it posted nothing: its output decides, with the fallbacks
if want NP-W7-O5; then
  new_case NP-W7-O5
  Q=$R/data/vendor/gb/notify
  U="usage: cmd notification post [flags] <tag> <text>

flags:
  -h|--help
  -t|--title <text>
  -S|--style <style> [styleargs]"
  nq() { printf 'kind=notify\ntag=gb_update\ntitle=System update held\ntext=x y\n' > "$Q/17929000003$1-70-$1.msg"; }
  posts() { grep -c '^notification|post|' "$T/log/cmd.log"; }
  # 1: a build without -u (its usage lists bigtext): posted again with -S bigtext, no -u (user 0, the login screen)
  printf '%s\nstyles: (default none)\n  bigtext\n' "$U" > "$T/ctl/post_reply.uall_s"
  : > "$T/log/cmd.log"; nq 01
  run gb_notify.sh
  check "no -u: two posts" eq "$(posts)" 2
  check "no -u: the second without -u, with bigtext" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-S|bigtext|-t|System update held|gb_update|x y|"
  check "no -u: logged" has "$R/dev/kmsg" "this build's cmd notification post has no -u: 1792900000301-70-01.msg posted for user 0 only"
  # 2: a build without -u and without bigtext: plain post
  printf '%s\nstyles: (default none)\n  inbox\n' "$U" > "$T/ctl/post_reply.uall_s"
  : > "$T/log/cmd.log"; nq 02
  run gb_notify.sh
  check "no -u, no bigtext: plain second post" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-t|System update held|gb_update|x y|"
  # 3: -u listed, bigtext not: for all users without -S
  printf '%s\n  -u|--user <user_id>\nstyles: (default none)\n  inbox\n' "$U" > "$T/ctl/post_reply.uall_s"
  : > "$T/log/cmd.log"; nq 03
  run gb_notify.sh
  check "no bigtext: second post -u all without -S" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-u|all|-t|System update held|gb_update|x y|"
  check "no bigtext: logged" has "$R/dev/kmsg" "1792900000303-70-03.msg posted without -S bigtext"
  # 4: the style is unknown (an error, not the usage): for all users without -S
  printf 'Error occurred. Check logcat for details. unrecognized notification style: bigtext\n' > "$T/ctl/post_reply.uall_s"
  : > "$T/log/cmd.log"; nq 04
  run gb_notify.sh
  check "unknown style: second post -u all without -S" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-u|all|-t|System update held|gb_update|x y|"
  # 5: -u all refused ("Bad user"): one post per running full user (10 runs, 11 does not)
  printf 'Error occurred. Check logcat for details. Bad user number: all\n' > "$T/ctl/post_reply.uall_s"
  printf '\tUserInfo{0:System User:811} running\n\tUserInfo{10:User:412} running\n\tUserInfo{11:B:412}\n' > "$T/ctl/users"
  : > "$T/log/cmd.log"; nq 05
  run gb_notify.sh
  check "bad user: posts -u all, then -u 10 only" eq "$(cut -d'|' -f3,4 "$T/log/cmd.log" | tr '\n' ' ')" "-u|all -u|10 "
  check "bad user: logged" has "$R/dev/kmsg" "cmd notification post -u all refused for 1792900000305-70-05.msg: posted per running full user (1 of 10)"
  # 6: -u all refused at the login screen (no running full user): logged with the reason, nothing else posted
  printf '\tUserInfo{0:System User:811} running\n\tUserInfo{10:User:412}\n' > "$T/ctl/users"
  : > "$T/log/cmd.log"; nq 06
  run gb_notify.sh
  check "bad user, login screen: one post" eq "$(posts)" 1
  check "bad user, login screen: the reason logged" has "$R/dev/kmsg" "cmd notification post failed for 1792900000306-70-06.msg (Error occurred. Check logcat for details. Bad user number: all)"
  rm -f "$T/ctl/users"
  # 7: the fallback is refused too: two posts, one failure line
  printf '%s\nstyles: (default none)\n  bigtext\n' "$U" > "$T/ctl/post_reply.uall_s"
  printf '%s\n' "$U" > "$T/ctl/post_reply.s"
  : > "$T/log/cmd.log"; nq 07
  run gb_notify.sh
  check "fallback refused: two posts" eq "$(posts)" 2
  check "fallback refused: logged as a failure" has "$R/dev/kmsg" "cmd notification post failed for 1792900000307-70-07.msg (usage: cmd notification post"
  rm -f "$T/ctl/post_reply.uall_s" "$T/ctl/post_reply.s"
  # 8: any other error: logged, no second post
  printf 'Error occurred. Check logcat for details. something else\n' > "$T/ctl/post_reply.uall_s"
  : > "$T/log/cmd.log"; nq 08
  run gb_notify.sh
  check "other error: one post" eq "$(posts)" 1
  check "other error: logged" has "$R/dev/kmsg" "cmd notification post failed for 1792900000308-70-08.msg (Error occurred. Check logcat for details. something else)"
  rm -f "$T/ctl/post_reply.uall_s"
  # 9: exit status 1 without output (as before wave 7): logged, no second post
  : > "$T/ctl/cmd_fail"
  : > "$T/log/cmd.log"; nq 09
  run gb_notify.sh
  rm -f "$T/ctl/cmd_fail"
  check "cmd fails: one post" eq "$(posts)" 1
  check "cmd fails: logged" has "$R/dev/kmsg" "cmd notification post failed for 1792900000309-70-09.msg"
  # 10: a post that prints nothing is final (the normal case): one post, nothing logged about it
  : > "$T/log/cmd.log"; nq 10
  run gb_notify.sh
  check "normal: one post" eq "$(posts)" 1
  check "normal: no post line logged" eq "$(grep -c '1792900000310-70-10.msg' "$R/dev/kmsg")" 0
  check "queue empty" eq "$(nmsg)" 0
fi

if want NP-1B-18; then
  new_case NP-1B-18
  Q=$R/data/vendor/gb/notify
  printf 'kind=notify\ntag=gb_gfx\ntitle=Performance base connected\ntext=NVIDIA graphics on for apps you open now. Restart apps that are already open to switch them.\n' > "$Q/1792900000001-30-1.msg"
  printf 'kind=notify\ntag=gb_gfx\ntitle=Tablet mode - Intel graphics\ntext=Apps use Intel graphics while the keyboard base is detached. Reattach it to use NVIDIA graphics again.\n' > "$Q/1792900000002-30-2.msg"
  run gb_notify.sh
  check "N16 posted under gb_gfx" eq "$(sed -n 1p "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|Performance base connected|gb_gfx|NVIDIA graphics on for apps you open now. Restart apps that are already open to switch them.|"
  check "N17 posted under gb_gfx" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|Tablet mode - Intel graphics|gb_gfx|Apps use Intel graphics while the keyboard base is detached. Reattach it to use NVIDIA graphics again.|"
fi

if want NP-W3-01; then                            # wave 3 (lead decision D-a): tag gb_update, gb_slotsync's N22/N23
  new_case NP-W3-01
  Q=$R/data/vendor/gb/notify
  printf 'kind=notify
tag=gb_update
title=System update held
text=Kernel mismatch: the update (slot B) needs kernel 6.18-android17-6, this system runs 6.18-android17-5. Startup stays on slot A; (slot B) is in the boot menu.
' > "$Q/1792900000001-40-1.msg"
  printf 'kind=notify
tag=gb_update
title=System update held
text=Kernel check failed: the kernel version of the update (slot B) could not be checked. Startup stays on slot A; (slot B) is in the boot menu.
' > "$Q/1792900000002-40-2.msg"
  printf 'kind=notify
tag=gb_updates
title=System update held
text=x
' > "$Q/1792900000003-40-3.msg"
  run gb_notify.sh
  check "exit 0" eq "$RC" 0
  check "N22 posted under gb_update" eq "$(sed -n 1p "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|System update held|gb_update|Kernel mismatch: the update (slot B) needs kernel 6.18-android17-6, this system runs 6.18-android17-5. Startup stays on slot A; (slot B) is in the boot menu.|"
  check "N23 posted under gb_update" eq "$(sed -n 2p "$T/log/cmd.log")" "notification|post|-u|all|-S|bigtext|-t|System update held|gb_update|Kernel check failed: the kernel version of the update (slot B) could not be checked. Startup stays on slot A; (slot B) is in the boot menu.|"
  check "exactly 2 posts (gb_updates is not a tag)" eq "$(nlines "$T/log/cmd.log")" 2
  check "the near-miss tag is dropped and logged" has "$R/dev/kmsg" "1792900000003-40-3.msg: bad tag"
  check "no .msg left" eq "$(nmsg)" 0
fi

if want NP-1B-19; then
  new_case NP-1B-19
  st_setup
  _fp=$(fp_tree "$R/sys")
  st
  check "exit 0" eq "$RC" 0
  check "both probes, in order, with -T 60" eq "$(tr '\n' '|' < "$T/log/nvk_probe.log")" "-t ahb-i915-readback -T 60|-t a17-swapchain-model -T 60|"
  check "at the first probe: foreign (property and file), the marker of this boot, ESP lock gb_dgpu_report" eq "$(head -n 1 "$T/log/nvk_state.log" | sed 's/ t=[0-9]* lock=/ lock=/')" "t=ahb-i915-readback foreign=1/1 marker=boot=$(boot_of) lock=gb_dgpu_report"
  check "ESP lock released" absent "$R/dev/gb_esp.lock"
  check "marker removed after the verdict" absent "$R/data/vendor/gb/selftest/running"
  check "record keys in order" eq "$(cut -d= -f1 "$(STR)" | tr '\n' ' ')" "verdict reason build krel gfx t attempts crashes report "
  check "verdict=pass reason=ok" eq "$(recv verdict)/$(recv reason)" pass/ok
  check "identity: build" eq "$(recv build)" "$ST_BUILD"
  check "identity: krel" eq "$(recv krel)" "$KREL"
  check "identity: gfx (first line of gfx_build_id)" eq "$(recv gfx)" "$GFXLINE"
  check "t = epoch s" eq "$(recv t | grep -cE '^[0-9]{10}$')" 1
  check "attempts=1 crashes=0" eq "$(recv attempts)/$(recv crashes)" 1/0
  check "report=selftest-1.txt" eq "$(recv report)" selftest-1.txt
  check "record mode 0644" eq "$(mode_of "$(STR)")" 644
  check "no .tmp left" absent "$(STR).tmp"
  F=$R/data/vendor/gb/report/selftest-1.txt
  check "report 0640 root:shell" eq "$(mode_of "$F")/$(lastown /data/vendor/gb/report/selftest-1.txt)" 640/root:shell
  check "report: identity" has "$F" "gfx=$GFXLINE"
  check "report: nouveau argv and result" has "$F" "argv=runpm=1 modeset=2 result=clean"
  check "report: both probe outputs" eq "$(grep -c '^nvk_probe (fake): -t ' "$F")" 2
  check "report: exit codes" eq "$(grep -c '^nvk_probe_exit=0 ' "$F")" 2
  check "foreign property" has "$T/log/setprop.log" "vendor.gb.nouveau.foreign=1"
  check "no sysfs writes" eq "$(fp_tree "$R/sys")" "$_fp"
  st
  check "second run, same identity: attempts=2" eq "$(recv attempts)" 2
fi

if want NP-W3-02; then                            # wave 3 item 18: the HWUI-on-Vulkan abort count in the self-test report
  new_case NP-W3-02
  st_setup
  printf '%s
' '10-26 01:02:03.456  1234  1250 F HWUI    : queueFamilyProperties.queueCount < kRequestedQueueCount'     'unrelated line' '10-26 01:02:04.000  1300  1301 F HWUI    : queueFamilyProperties.queueCount < kRequestedQueueCount'     > "$T/ctl/logcat"
  st
  F=$R/data/vendor/gb/report/selftest-1.txt
  check "exit 0, verdict pass (the count never changes the verdict)" eq "$RC/$(recv verdict)" 0/pass
  check "report: section and count 2" eq "$(grep -A1 -x '== hwui_vulkan (0 expected; non-zero: HWUI on a one-queue-per-family Vulkan device, report it)' "$F" | tail -n 1)" "queueCount_abort=2"
  check "logcat called once, as a dump (-d -b all)" eq "$(tr '
' '|' < "$T/log/logcat.log")" "-d -b all|"
  check "section placed before == end" eq "$(awk '/^== hwui_vulkan/{h=NR} /^== end /{e=NR} END {print (h && e > h)}' "$F")" 1
  check "no HWUI/renderer property written" eq "$(grep -c -E 'hwui|renderer' "$T/log/setprop.log" 2>/dev/null)" 0
fi

if want NP-1B-20; then
  new_case NP-1B-20
  st_setup
  _n=0
  for vm in "1 0:fail:ahb-i915-readback" "0 124:fail:a17-swapchain-model timeout" "0 137:fail:a17-swapchain-model timeout" \
    "3 3:error:nodevice" "2 0:error:icd" "0 5:fail:a17-swapchain-model rc 5" "0 0:pass:ok" "2 1:fail:a17-swapchain-model"; do
    _rcs=${vm%%:*}
    _rest=${vm#*:}
    echo "${_rcs% *}" > "$T/ctl/nvk_rc_ahb-i915-readback"
    echo "${_rcs#* }" > "$T/ctl/nvk_rc_a17-swapchain-model"
    st
    _n=$((_n + 1))
    check "($_rcs): ${_rest%%:*} ${_rest#*:}" eq "$(recv verdict):$(recv reason)" "$_rest"
    check "($_rcs): attempts $_n (same identity)" eq "$(recv attempts)" "$_n"
  done
  sed -i 's/^crashes=.*/crashes=1/' "$(STR)"
  st
  check "crashes carried over for the same identity" eq "$(recv crashes)/$(recv attempts)" "1/$((_n + 1))"
  printf 'apk=%s icd=%s nvk=%s probe=%s pay=%s\n' "$(hx 5)" "$(hx 2)" "$(hx 3)" "$(hx 4)" "$(hx 6)" > "$R/odm/etc/gb/gfx_build_id"
  st
  check "a new identity starts at attempts=1, crashes=0" eq "$(recv attempts)/$(recv crashes)" 1/0
fi

if want NP-1B-21; then
  for rf in vm testboot unbound nodir symdir dirmode detaching nobuildid; do
    new_case "NP-1B-21-$rf"
    st_setup
    case $rf in
      vm) echo vm > "$R/odm/etc/gb_hw" ;;
      testboot) cmdline gb.nouveau=test ;;
      unbound) rm -f "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/driver" ;;
      nodir) rmdir "$R/data/vendor/gb/selftest" ;;
      symdir)
        rmdir "$R/data/vendor/gb/selftest"
        mkdir -m 0700 "$T/elsewhere"
        ln -s "$T/elsewhere" "$R/data/vendor/gb/selftest" ;;
      dirmode) chmod 0755 "$R/data/vendor/gb/selftest" ;;
      detaching) : > "$R/dev/gb_dgpu/detaching" ;;
      nobuildid) rm -f "$R/odm/etc/gb/gfx_build_id" ;;
    esac
    _fd=$(fp_tree "$R/data")
    st
    check "exit 1" eq "$RC" 1
    check "no file written" eq "$(fp_tree "$R/data")" "$_fd"
    check "nvk_probe not run" absent "$T/log/nvk_probe.log"
    check "foreign not set" eq "$(prop vendor.gb.nouveau.foreign)$(test -e "$R/dev/gb_dgpu/foreign" && echo F)" ""
    check "no ESP lock taken" absent "$R/dev/gb_esp.lock"
    [ $rf = symdir ] && check "nothing created behind the symlink" eq "$(find "$T/elsewhere" -mindepth 1 | wc -l | tr -d ' ')" 0
  done

  new_case NP-1B-21-esp
  st_setup
  printf 'verdict=error\nreason=crash\nbuild=%s\nkrel=%s\ngfx=%s\nt=1\nattempts=2\ncrashes=1\nreport=selftest-1.txt\n' "$ST_BUILD" "$KREL" "$GFXLINE" > "$(STR)"
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4260"
  echo "4260 gb_bootlog 30" > "$R/dev/gb_esp.lock/owner"
  _t0=$(upt)
  st
  _t1=$(upt)
  check "exit 0" eq "$RC" 0
  check "verdict=error reason=esp" eq "$(recv verdict)/$(recv reason)" error/esp
  check "attempts unchanged, crashes carried" eq "$(recv attempts)/$(recv crashes)" 2/1
  check "waited >= 120 s" ge $((_t1 - _t0)) 120
  check "nvk_probe not run" absent "$T/log/nvk_probe.log"
  check "no marker" absent "$R/data/vendor/gb/selftest/running"
  check "foreign lock untouched" eq "$(cat "$R/dev/gb_esp.lock/owner")" "4260 gb_bootlog 30"
  check "foreign not set (no probe ran)" eq "$(prop vendor.gb.nouveau.foreign)" ""
fi

if want NP-1B-22; then
  new_case NP-1B-22-term
  st_setup
  st
  check "setup: a pass with attempts=1" eq "$(recv verdict)/$(recv attempts)" pass/1
  : > "$T/log/nvk_probe.log"
  : > "$T/ctl/nvk_term_ahb-i915-readback"
  st
  check "TERM during the first probe: exit 0" eq "$RC" 0
  check "the TERM reached the script" has "$T/log/nvk_state.log" "term sent to"
  check "verdict=error reason=interrupted" eq "$(recv verdict)/$(recv reason)" error/interrupted
  check "attempts unchanged" eq "$(recv attempts)" 1
  check "marker removed" absent "$R/data/vendor/gb/selftest/running"
  check "lock released" absent "$R/dev/gb_esp.lock"
  check "the second probe never ran" eq "$(nlines "$T/log/nvk_probe.log")" 1

  new_case NP-1B-22-detach
  st_setup
  : > "$T/ctl/nvk_detach_ahb-i915-readback"
  st
  check "detaching between the probes: exit 0" eq "$RC" 0
  check "verdict=error reason=interrupted" eq "$(recv verdict)/$(recv reason)" error/interrupted
  check "attempts unchanged (0, no earlier record)" eq "$(recv attempts)" 0
  check "marker removed" absent "$R/data/vendor/gb/selftest/running"
  check "lock released" absent "$R/dev/gb_esp.lock"
  check "the second probe never ran" eq "$(nlines "$T/log/nvk_probe.log")" 1
fi

if want NP-1B-23; then
  new_case NP-1B-23
  rm -f "$R/sys/bus/pci/devices/0000:02:00.0" "$R/sys/bus/pci/devices/0000:02:00.1"
  np auto
  np auto
  check "both runs refused:nodgpu" eq "$(results)" "refused:nodgpu refused:nodgpu "
  check "setprop vendor.gb.nouveau.booted appears once" eq "$(grep -c '^vendor.gb.nouveau.booted=' "$T/log/setprop.log")" 1
fi

if want NP-1B-VM; then                            # D8: the native-smoke image in VMware (VM-1B-04, script side)
  new_case NP-1B-VM
  mk_pci 0000:00:0f.0 '' 0x15ad 0x0405 0x030000 vmwgfx 2
  rm -rf "$R/sys/class/drm/renderD128" "$R/sys/class/drm/card0"
  mk_drm 0000:00:0f.0 renderD128
  mk_drm 0000:00:0f.0 card0
  rm -f "$R/sys/bus/pci/devices/0000:02:00.0" "$R/sys/bus/pci/devices/0000:02:00.1" "$R/sys/bus/pci/devices/0000:00:02.0"
  np auto
  check "gfx_mode unset in VMware: refused:i915, no insmod" eq "$(prop vendor.gb.nouveau.result)$(nins)" refused:i9150
  check "nothing queued (no settings, no notification)" eq "$(nmsg)" 0
  check "gfx_mode not written" hasnt "$T/log/setprop.log" "persist.vendor.gb.gfx_mode"
  run gb_dgpu_report.sh selftest
  check "selftest refused" eq "$RC" 1
fi

# ================================================================= NP-R: review fix pass (INTERFACES 29, TASKS_1B 12)

wake_state() { printf '%s/%s' "$(cat "$R/sys/power/wake_lock" 2>/dev/null)" "$(cat "$R/sys/power/wake_unlock" 2>/dev/null)"; }
rel() { awk -v u="$(cat "$R/proc/uptime")" -v d="$1" 'BEGIN { split(u, a, " "); printf "%.2f", a[1] + d }'; }

if want NP-R1; then                               # R-1: a clean shutdown in a load window is not a crash
  new_case NP-R1-clean                            # insmod path, TERM with sys.powerctl during the clean-mark wait
  echo "85 reboot,userrequested" > "$T/ctl/term_at"
  np auto
  check "clean-mark wait, SIGTERM + sys.powerctl: exit 0" eq "$RC" 0
  check "the TERM came during the clean-mark wait" has "$T/log/term.log" "term at 85"
  check "crash-loop marker removed" absent "$R/data/vendor/gb/nouveau.loading"
  check "logged as a clean shutdown" has "$R/data/vendor/gb/report/gb_nouveau.log" "clean shutdown (sys.powerctl=reboot,userrequested)"
  check "results: loading healthy (never clean: the boot is not counted)" eq "$(results)" "loading healthy "
  check "clean_boots not counted" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots" 2>/dev/null || echo none)" none
  check "loading=0, lock released" eq "$(prop vendor.gb.nouveau.loading)$(test -e "$R/dev/gb_esp.lock" && echo L)" 0
  check "gfx_mode untouched, no notification" eq "$(prop persist.vendor.gb.gfx_mode)$(nmsg)" 0
  reboot_dev
  np auto
  check "next boot: no crash-loop, a normal load" eq "$(prop vendor.gb.nouveau.result)/$(prop persist.vendor.gb.gfx_mode)" clean/

  new_case NP-R1-health                           # TERM with sys.powerctl inside the 30 s health window
  : > "$T/ctl/no_d3cold"
  echo "35 shutdown,userrequested" > "$T/ctl/term_at"
  np auto
  check "health window, SIGTERM + sys.powerctl: exit 0, marker removed" eq "$RC$(test -e "$R/data/vendor/gb/nouveau.loading" && echo M)" 0
  check "no unhealthy verdict, no Intel switch, no noload" eq "$(results)$(prop persist.vendor.gb.gfx_mode)$(test -e "$R/dev/gb_dgpu/noload" && echo N)" "loading "
  check "wakelock released by the EXIT trap" eq "$(wake_state)" gb_nouveau/gb_nouveau
  reboot_dev
  np auto
  check "next boot loads again (not a crash-loop)" eq "$(prop vendor.gb.nouveau.result)" unhealthy:d3cold

  new_case NP-R1-nopc                             # SIGTERM without sys.powerctl (a plain stop): the marker stays
  echo "85 -" > "$T/ctl/term_at"
  np auto
  check "no sys.powerctl: exit 1, marker kept" eq "$RC$(test -e "$R/data/vendor/gb/nouveau.loading" && echo M)" 1M
  reboot_dev
  np auto
  check "next boot: crash-loop path as before" eq "$(prop vendor.gb.nouveau.result)/$(prop persist.vendor.gb.gfx_mode)" crashloop/intel

  new_case NP-R1-notours                          # the marker was replaced by another window's: never removed
  printf 'mode=rebind t=90 boot=%s argv=-\n' "$(boot_of)" > "$T/ctl/copy_src"
  echo "60 copy data/vendor/gb/nouveau.loading" > "$T/ctl/at"
  echo "70 reboot" > "$T/ctl/term_at"
  np auto
  check "a marker that is not ours: kept, exit 1" eq "$RC/$(mark_line)" "1/mode=rebind t=90 boot=$(boot_of) argv=-"
  check "logged" has "$R/data/vendor/gb/report/gb_nouveau.log" "crash-loop marker kept (the marker is not ours)"

  new_case NP-R1-bind-pending                     # bind path: TERM while the bind write hangs in the kernel
  mod_only
  rm -f "$R/sys/bus/pci/drivers/nouveau/bind"
  mkfifo "$R/sys/bus/pci/drivers/nouveau/bind"
  echo "28 reboot" > "$T/ctl/term_at"
  np auto
  timeout 5 cat "$R/sys/bus/pci/drivers/nouveau/bind" > /dev/null 2>&1
  check "bind write pending at the TERM: marker kept, exit 1" eq "$RC/$(mark_line | cut -d' ' -f1)" 1/mode=bind
  check "logged" has "$R/data/vendor/gb/report/gb_nouveau.log" "crash-loop marker kept (a bind write is still pending)"

  new_case NP-R1-bind-clean                       # bind path: TERM with sys.powerctl during its clean-mark wait
  mod_only
  echo "60 reboot" > "$T/ctl/term_at"
  np auto
  check "bound, clean-mark wait: exit 0, marker removed" eq "$RC$(test -e "$R/data/vendor/gb/nouveau.loading" && echo M)" 0
  check "results: loading healthy" eq "$(results)" "loading healthy "
  check "clean_boots unchanged (2)" eq "$(cat "$R/data/vendor/gb/nouveau.clean_boots")" 2
  check "bind write made with the wakelock held" has "$T/log/bind.log" " wake=gb_nouveau/"
fi

if want NP-R2; then                               # R-2: off waits for gb_power's rebind guard (ESP lock owner gb_power)
  new_case NP-R2-wait
  loaded
  intel
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4300"
  echo "4300 gb_power 95" > "$R/dev/gb_esp.lock/owner"
  _t0=$(upt)
  echo "$(rel 12) rm dev/gb_esp.lock" > "$T/ctl/at"
  np off
  check "off waited for the guard's lock" ge "$(upt)" $((_t0 + 12))
  check "then unbound (off-done)" eq "$(prop vendor.gb.nouveau.result)" off-done
  check "the unbind came after the wait" has "$T/log/unbind.log" "nouveau 0000:02:00.0"
  new_case NP-R2-other
  loaded
  intel
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4300"
  echo "4300 gb_bootlog 95" > "$R/dev/gb_esp.lock/owner"
  _t0=$(upt)
  np off
  check "another owner (gb_bootlog): no wait" le "$(upt)" $((_t0 + 5))
  check "off-done" eq "$(prop vendor.gb.nouveau.result)" off-done
fi

if want NP-R7; then                               # R-7: windows counted in seconds slept; wakelock
  new_case NP-R7-health                           # a 600 s suspend 3 s into the health window
  : > "$T/ctl/no_d3cold"
  echo "28 600" > "$T/ctl/suspend_at"
  echo "634 d3cold 0000:02:00.0" > "$T/ctl/at"
  np auto
  check "the suspend happened" has "$T/log/suspend.log" "suspend at 28 for 600 s"
  check "D3cold 6 s after the resume: healthy, then clean (no false unhealthy:d3cold)" eq "$(results)" "loading healthy clean "
  check "no Intel switch, no noload" eq "$(prop persist.vendor.gb.gfx_mode)$(test -e "$R/dev/gb_dgpu/noload" && echo N)" ""
  check "insmod with the wakelock held" has "$T/log/insmod_state.log" "nouveau panic=30 oops=1 wake=gb_nouveau/"
  check "wakelock released at the health verdict" eq "$(wake_state)" gb_nouveau/gb_nouveau

  new_case NP-R7-clean                            # a suspend during the clean-mark wait does not shorten the 120 s
  echo "60 600" > "$T/ctl/suspend_at"
  echo 700 > "$T/ctl/crash_at"                    # 35 + 40 s slept since the probe: inside the 120 s
  np auto
  check "killed at 700 (boottime), 75 s slept after the probe" has "$T/log/crash.log" "crash at 700"
  check "the marker was still there (the window was not shortened)" has "$R/data/vendor/gb/nouveau.loading" "mode=auto "
  reboot_dev
  np auto
  check "next boot: crash-loop" eq "$(prop vendor.gb.nouveau.result)" crashloop
fi

if want NP-R10; then                              # R-10: script-side node permissions
  new_case NP-R10-bound
  loaded
  chmod 0666 "$R/dev/dri/renderD129"              # gb_power raised it (gfx_active=nvidia)
  : > "$T/log/chmod.log"
  setp persist.vendor.gb.gfx_mode nvidia
  np auto
  check "already bound: a 0666 node (gb_power's) is not lowered" eq "$(mode_of "$R/dev/dri/renderD129")/$(mode_of "$R/dev/dri/card1")" 666/600
  check "no chmod of the render node" hasnt "$T/log/chmod.log" "/dev/dri/renderD129"
  chmod 0644 "$R/dev/dri/renderD129"
  np auto
  check "already bound: any other mode (0644) is set to 0660" eq "$(mode_of "$R/dev/dri/renderD129")" 660

  new_case NP-R10-late                            # ueventd re-creates the node during the health window
  : > "$T/ctl/no_d3cold"
  printf '27 node renderD129\n29 d3cold 0000:02:00.0\n' > "$T/ctl/at"
  np auto
  check "healthy" eq "$(results)" "loading healthy clean "
  check "the late 0666 node is 0660 before the window closes" eq "$(mode_of "$R/dev/dri/renderD129")" 660
  check "applied twice" eq "$(grep -c '^0660 /dev/dri/renderD129$' "$T/log/chmod.log")" 2
fi

if want NP-R11; then                              # R-11: teardown deadlines
  new_case NP-R11-rmmod
  loaded
  intel
  : > "$T/ctl/rmmod_hang_nouveau"
  _t0=$(upt)
  np off
  check "rmmod never returns: off-wedged, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" off-wedged/1
  check "within 21 fake s of the unbind" le "$(upt)" $((_t0 + 25))
  check "wedged" exists "$R/dev/gb_dgpu/wedged"
  check "N05" msg_has "text=The NVIDIA GPU stopped responding. Restart before detaching the screen."
  pkill -f "^/bin/sleep 300" 2>/dev/null

  new_case NP-R11-probe                           # selftest TERM: wait for nvk_probe before the marker and the lock
  st_setup
  : > "$R/sys/power/wake_unlock"
  : > "$T/ctl/nvk_term_ahb-i915-readback"
  echo 3 > "$T/ctl/pgrep_n"
  st
  check "TERM: exit 0, error interrupted" eq "$RC/$(recv verdict)/$(recv reason)" 0/error/interrupted
  check "waited for nvk_probe (3 found, then none)" eq "$(grep -c '^-x nvk_probe$' "$T/log/pgrep.log")" 4
  check "marker removed, lock released" eq "$(test -e "$R/data/vendor/gb/selftest/running" && echo M)$(test -e "$R/dev/gb_esp.lock" && echo L)" ""
  check "the probe ran with the self-test wakelock" has "$T/log/nvk_wake.log" "t=ahb-i915-readback wake=gb_gfx_selftest/"
  check "wakelock released" eq "$(wake_state)" gb_gfx_selftest/gb_gfx_selftest

  new_case NP-R11-probe-pc                        # during a shutdown the wait is capped (2 polls)
  st_setup
  : > "$T/ctl/nvk_term_ahb-i915-readback"
  setp sys.powerctl shutdown
  echo 10 > "$T/ctl/pgrep_n"
  st
  check "shutdown: at most 2 waits, then released anyway" eq "$(grep -c '^-x nvk_probe$' "$T/log/pgrep.log")/$(recv reason)" 2/interrupted
  check "logged" has "$R/dev/kmsg" "nvk_probe still running after the stop: releasing anyway"

  new_case NP-R7-selftest                         # a full self-test holds its wakelock for both probes
  st_setup
  : > "$R/sys/power/wake_unlock"
  st
  check "both probes with the wakelock held" eq "$(grep -c ' wake=gb_gfx_selftest/$' "$T/log/nvk_wake.log")" 2
  check "released at the end" eq "$(wake_state)/$(recv verdict)" gb_gfx_selftest/gb_gfx_selftest/pass
fi

# ================================================================= NP-W2B: the ESP lock r3 (INTERFACES 5.4, Wave 2b)

nstale() { find "$R/dev" -maxdepth 1 -name 'gb_esp.lock.stale.*' | wc -l | tr -d ' '; }

if want NP-W2B; then
  new_case NP-W2B-busy                            # a steal in progress (live .stale.<pid>): back off, then the load
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock.stale.4245" "$R/proc/4245"
  np auto
  check "steal in progress: backed off (logged once), then the load ran" \
    eq "$(prop vendor.gb.nouveau.result)/$(grep -c 'a steal is in progress: backing off' "$R/dev/kmsg")" clean/1
  check "the stealer's directory untouched, the lock released" eq "$(nstale)$(test -e "$R/dev/gb_esp.lock" && echo L)" 1

  new_case NP-W2B-busy-dead                       # a leftover of a dead stealer is no steal in progress
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock.stale.4247"
  np auto
  check "dead stealer's leftover: no back-off, the load ran" \
    eq "$(prop vendor.gb.nouveau.result)/$(grep -c 'backing off' "$R/dev/kmsg")" clean/0

  new_case NP-W2B-excl                            # an owner file in our new lock directory is never overwritten
  cmdline gb.nouveau=once
  mkdir -p "$R/proc/4244"
  echo "4244 gb_bootlog 31" > "$T/ctl/mkdir_race"
  printf '%s save dev/gb_esp.lock/owner\n%s rm dev/gb_esp.lock\n' "$(rel 3)" "$(rel 4)" > "$T/ctl/at"
  np auto
  check "logged: another lock took our directory's place" has "$R/dev/kmsg" "another lock took the place of ours"
  check "the other holder's owner line kept while it held the lock" eq "$(cat "$T/log/save.log" 2>/dev/null)" "4244 gb_bootlog 31"
  check "after its release the load ran, lock released" eq "$(prop vendor.gb.nouveau.result)$(test -e "$R/dev/gb_esp.lock" && echo L)" clean

  new_case NP-W2B-relwait                         # at release our lock is moved away by a stealer: wait for the put-back
  cmdline gb.nouveau=once
  mkdir -p "$R/proc/4246"
  : > "$T/ctl/displace_at_release"
  np auto
  check "the load ran" eq "$(prop vendor.gb.nouveau.result)" clean
  check "the stealer put it back during the release wait" has "$T/log/at.log" "unsteal 4246"
  check "then released: no lock, no stale directory, no 'not ours'" \
    eq "$(test -e "$R/dev/gb_esp.lock" && echo L)$(nstale)$(grep -c 'not ours any more at release' "$R/dev/kmsg")" 00

  new_case NP-W2B-race-intrude                    # the steal moved a live lock; a newcomer in the way of the put-back
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock" "$R/proc/4243"
  echo "999999 gb_bootlog 20" > "$R/dev/gb_esp.lock/owner"
  : > "$T/ctl/steal_race"
  : > "$T/ctl/steal_intrude"
  echo "$(rel 2) rm dev/gb_esp.lock" > "$T/ctl/at"
  np auto
  check "put back after the newcomer left, never over it" eq "$(cat "$R/dev/gb_esp.lock/owner" 2>/dev/null)" "4243 gb_bootlog 30"
  check "logged" has "$R/dev/kmsg" "the lock changed while stealing it (owner '4243 gb_bootlog 30'): put back"
  check "then the live holder is waited for: refused:esp, no insmod" eq "$(prop vendor.gb.nouveau.result)/$(nins)" refused:esp/0
  check "no stale directory left" eq "$(nstale)" 0

  new_case NP-W2B-race-ownerless                  # an ownerless moved directory that cannot go back is removed
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock"
  echo "999999 gb_bootlog 20" > "$R/dev/gb_esp.lock/owner"
  : > "$T/ctl/steal_race_noowner"
  : > "$T/ctl/steal_intrude"
  np auto
  check "logged: removed" has "$R/dev/kmsg" "the moved directory had no owner and another is in place: removed"
  check "the newcomer's ownerless lock is stolen after 5 polls, the load ran" \
    eq "$(prop vendor.gb.nouveau.result)/$(grep -c 'stealing a stale lock' "$R/dev/kmsg")" clean/1
  check "no stale directory, no lock left" eq "$(nstale)$(test -e "$R/dev/gb_esp.lock" && echo L)" 0

  new_case NP-W2B-report-busy                     # gb_dgpu_report.sh selftest: the same back-off
  st_setup
  : > "$R/dev/kmsg"
  mkdir -p "$R/dev/gb_esp.lock.stale.4248" "$R/proc/4248"
  st
  check "selftest: backed off (logged once), then passed and released" \
    eq "$(grep -c 'gb_dgpu_report: ESP lock: a steal is in progress: backing off' "$R/dev/kmsg")/$(recv verdict)$(test -e "$R/dev/gb_esp.lock" && echo L)" 1/pass

  new_case NP-W2B-report-excl                     # gb_dgpu_report.sh: an owner file in our new directory stays
  st_setup
  : > "$R/dev/kmsg"
  mkdir -p "$R/proc/4249"
  echo "4249 gb_bootlog 31" > "$T/ctl/mkdir_race"
  printf '%s save dev/gb_esp.lock/owner\n%s rm dev/gb_esp.lock\n' "$(rel 3)" "$(rel 4)" > "$T/ctl/at"
  st
  check "selftest: the other owner line kept, then passed" \
    eq "$(cat "$T/log/save.log" 2>/dev/null)/$(recv verdict)" "4249 gb_bootlog 31/pass"
  check "logged" has "$R/dev/kmsg" "gb_dgpu_report: ESP lock: another lock took the place of ours"
fi

# ================================================================= NP-24 rc lint

if want NP-W3-03; then                            # wave 3: ESP lock r4 (INTERFACES 5.4 r4; wave 2b review L1/L2)
  new_case NP-W3-03-busy                          # a steal in progress: no mkdir at all during the back-off
  cmdline gb.nouveau=once
  mkdir -p "$R/dev/gb_esp.lock.stale.4251" "$R/proc/4251"
  np auto
  check "backed off (logged once), then the load ran"     eq "$(prop vendor.gb.nouveau.result)/$(grep -c 'a steal is in progress: backing off' "$R/dev/kmsg")" clean/1
  check "exactly one mkdir of the lock (after the 5 back-offs), none while busy" eq "$(nlines "$T/log/lockmkdir.log")" 1

  new_case NP-W3-03-self                          # a lock naming our own pid while we hold nothing is stale (L2)
  cmdline gb.nouveau=once
  echo SELF > "$T/ctl/mkdir_race"
  np auto
  check "our own pid in a lock we do not hold: stolen once as stale, the load ran"     eq "$(prop vendor.gb.nouveau.result)/$(grep -c 'stealing a stale lock' "$R/dev/kmsg")" clean/1
  check "no lock left" absent "$R/dev/gb_esp.lock"

  new_case NP-W3-03-report-self                   # gb_dgpu_report.sh: the same own-pid rule
  st_setup
  : > "$R/dev/kmsg"
  echo SELF > "$T/ctl/mkdir_race"
  st
  check "selftest: own-pid lock stolen once, then passed"     eq "$(grep -c 'gb_dgpu_report: ESP lock: stealing a stale lock' "$R/dev/kmsg")/$(recv verdict)" 1/pass

  new_case NP-W3-03-report-busy                   # gb_dgpu_report.sh: no mkdir while busy
  st_setup
  : > "$R/dev/kmsg"
  : > "$T/log/lockmkdir.log"                      # (st_setup's auto load took the lock once already)
  mkdir -p "$R/dev/gb_esp.lock.stale.4252" "$R/proc/4252"
  st
  check "selftest: one lock mkdir after the back-offs, passed" eq "$(nlines "$T/log/lockmkdir.log")/$(recv verdict)" 1/pass
fi

# ================================================================= NP-B4 (wave 6, readiness B-4): TU117 device-id gate
# The image has nvidia/tu117 firmware only. A 15-inch Surface Book 3 (TU116 GTX 1660 Ti 0x2191, TU106 Quadro RTX 3000
# 0x1f36) must end in refused:model before any write: no marker, lock, payload, insmod or bind, no notification, the
# graphics mode untouched, one clear log line. Every TU117 id (0x1f80-0x1fff, any hex case) still loads, and (wave 6)
# an unreadable or malformed id is not gated: it loads exactly as before the gate, with one warning line.
set_dev() { printf '%s\n' "$1" > "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/device"; }
no_write() {                             # nothing of the load window happened
  _nw=
  [ -e "$T/log/insmod.log" ] && _nw="$_nw insmod"
  [ -s "$T/log/mount.log" ] && _nw="$_nw mount"
  [ -s "$T/log/lockmkdir.log" ] && _nw="$_nw lock"
  [ -s "$T/log/sync.log" ] && _nw="$_nw sync(marker)"
  grep -q '^vendor.gb.nouveau.loading=' "$T/log/setprop.log" 2>/dev/null && _nw="$_nw loading"
  grep -q '^vendor.gb.nouveau.used=' "$T/log/setprop.log" 2>/dev/null && _nw="$_nw used"
  [ -z "$_nw" ] || { printf '     load-window activity:%s\n' "$_nw"; return 1; }
}

if want NP-B4; then
  for _x in 0x2191:TU116:auto 0x21d1:TU116:nvidia 0x1f36:TU106:auto 0x1f76:TU106:auto 0x1e90:-:auto; do
    _id=${_x%%:*}
    _c=${_x#*:}
    _gm=${_c#*:}
    _c=${_c%%:*}
    new_case "NP-B4-$_id"
    set_dev "$_id"
    [ "$_gm" = nvidia ] && setp persist.vendor.gb.gfx_mode nvidia
    np auto
    check "$_c $_id: refused:model, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:model/1
    check "no load-window activity" no_write
    check "no nouveau insmod, no bind write" eq "$(nins)/$(nlines "$T/log/bind.log")" 0/0
    check "no marker" absent "$R/data/vendor/gb/nouveau.loading"
    check "no notification queued" eq "$(nmsg)" 0
    check "graphics mode untouched" eq "$(prop persist.vendor.gb.gfx_mode)" "$([ "$_gm" = nvidia ] && echo nvidia)"
    check "no noload/keep flag (nothing failed)" eq "$(find "$R/dev/gb_dgpu" -mindepth 1 | wc -l | tr -d ' ')" 0
    check "booted=1 (the runtime triggers are armed, like refused:nodgpu)" eq "$(prop vendor.gb.nouveau.booted)" 1
    check "panic settings untouched" eq "$(cat "$R/proc/sys/kernel/panic")/$(cat "$R/proc/sys/kernel/panic_on_oops")" 0/1
    check "no dmesg stream requested" eq "$(prop vendor.gb.dgpu.dmesg)" ""
    check "no probation line (refused before it)" hasnt "$R/dev/kmsg" "probation"
    [ "$_c" = - ] && _c=
    check "one clear log line" eq "$(grep -c "guard: 0000:02:00.0 is NVIDIA $_id${_c:+ $_c}, not the 13.5-inch Surface Book 3's TU117: this image has no firmware for it; nouveau is not loaded, the dGPU stays powered off (Intel graphics only)" "$R/dev/kmsg")" 1
    check "result logged" has "$R/dev/kmsg" "gb_nouveau: result=refused:model"
  done

  new_case NP-B4-test-mode                        # gb.nouveau=test (the [NVIDIA test] entry) on a 15-inch
  set_dev 0x2191
  cmdline gb.nouveau=test
  np auto
  check "test boot: refused:model, nothing written, panic_on_oops kept" \
    eq "$(prop vendor.gb.nouveau.result)/$(nins)/$(cat "$R/proc/sys/kernel/panic_on_oops")" refused:model/0/1
  check "test boot: no load-window activity" no_write

  new_case NP-B4-unreadable                       # no device attribute: not gated (fail open, wave 6)
  rm -f "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/device"
  cmdline gb.nouveau=once
  np auto
  check "device id unreadable: loads as before the gate (clean, one nouveau insmod)" \
    eq "$(prop vendor.gb.nouveau.result)/$(nins)" clean/1
  check "one fail-open line for the early and the late check together" \
    eq "$(grep -c "model gate: 0000:02:00.0 device id '' is unreadable or malformed: not gated" "$R/dev/kmsg")" 1
  check "no refusal line" hasnt "$R/dev/kmsg" "not the 13.5-inch"
  check "no refused:model at any point" eq "$(results | grep -c refused:model)" 0
  new_case NP-B4-unreadable-auto                  # the same in plain Auto (probation runs as before)
  rm -f "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/device"
  np auto
  check "Auto, unreadable id: probation 1/3 and a clean load as before" \
    eq "$(prop vendor.gb.nouveau.result)/$(nins)/$(grep -c 'probation 1/3' "$R/dev/kmsg")" clean/1/1
  for _id in "" garbage 0x219 0x21910 0X2191 2191 " 0x2191" "0x2191 " 0x2g91; do   # never printed by the kernel
    new_case "NP-B4-malformed-$(printf '%s' "$_id" | tr -c '0-9A-Za-z' '_')"
    set_dev "$_id"
    cmdline gb.nouveau=once
    np auto
    check "malformed id '$_id': not gated, loads (clean, one insmod)" eq "$(prop vendor.gb.nouveau.result)/$(nins)" clean/1
    check "malformed id '$_id': one fail-open line" eq "$(grep -c 'is unreadable or malformed: not gated' "$R/dev/kmsg")" 1
  done
  new_case NP-B4-unreadable-bind                  # bind path (nouveau loaded), unreadable id: binds as before
  rm -f "$(readlink -f "$R/sys/bus/pci/devices/0000:02:00.0")/device"
  mod_only
  np auto
  check "bind path, unreadable id: bound as before (loading healthy clean), one bind write" \
    eq "$(results)/$(nlines "$T/log/bind.log")" "loading healthy clean /1"
  check "bind path, unreadable id: one fail-open line, no refusal" \
    eq "$(grep -c 'is unreadable or malformed: not gated' "$R/dev/kmsg")/$(grep -c 'not the 13.5-inch' "$R/dev/kmsg")" 1/0

  new_case NP-B4-override                         # gb.nouveau.model=any (boot menu, this boot only): gate off
  set_dev 0x2191
  cmdline gb.nouveau=once gb.nouveau.model=any
  np auto
  check "override: a 15-inch id is tried as before the gate (clean in the fake tree, one nouveau insmod)" \
    eq "$(prop vendor.gb.nouveau.result)/$(nins)" clean/1
  check "override: one 'gate off' line, no refusal line" \
    eq "$(grep -c 'model gate: off for this boot (gb.nouveau.model=any)' "$R/dev/kmsg")/$(grep -c 'not the 13.5-inch' "$R/dev/kmsg")" 1/0
  new_case NP-B4-override-bind                    # the same on the bind path
  set_dev 0x1f36
  mod_only
  cmdline gb.nouveau.model=any
  np auto
  check "override, bind path: bound as before (loading healthy clean), one bind write" \
    eq "$(results)/$(nlines "$T/log/bind.log")" "loading healthy clean /1"
  new_case NP-B4-override-bad                     # any other value: the gate stays on, the value is logged
  set_dev 0x2191
  cmdline gb.nouveau=once gb.nouveau.model=yes
  np auto
  check "gb.nouveau.model=yes: still refused:model, no insmod" eq "$(prop vendor.gb.nouveau.result)/$(nins)" refused:model/0
  check "gb.nouveau.model=yes: logged as ignored" has "$R/dev/kmsg" "ignored gb.nouveau.model value 'yes'"
  new_case NP-B4-override-last                    # the last occurrence wins, like every other key
  set_dev 0x2191
  cmdline gb.nouveau=once gb.nouveau.model=any gb.nouveau.model=0
  np auto
  check "gb.nouveau.model=any then =0: the gate is on (refused:model)" eq "$(prop vendor.gb.nouveau.result)" refused:model
  new_case NP-B4-override-kill                    # the kill switch still wins
  set_dev 0x2191
  cmdline gb.nouveau=0 gb.nouveau.model=any
  np auto
  check "gb.nouveau=0 with gb.nouveau.model=any: disabled, no insmod" eq "$(prop vendor.gb.nouveau.result)/$(nins)" disabled/0

  new_case NP-B4-retry                            # re-runs (rc booted trigger, hot attach): the same refusal each time
  set_dev 0x2191
  np auto
  np auto
  np auto
  check "every run refused:model, the same line each time" \
    eq "$(results)/$(grep -c 'is NVIDIA 0x2191 TU116, not the 13.5-inch' "$R/dev/kmsg")" "refused:model refused:model refused:model /3"
  check "booted set once, no notification, no flag" \
    eq "$(grep -c '^vendor.gb.nouveau.booted=' "$T/log/setprop.log")/$(nmsg)/$(find "$R/dev/gb_dgpu" -mindepth 1 | wc -l | tr -d ' ')" 1/0/0

  new_case NP-B4-hda-only                         # the TU116's audio function alone: still refused:nodgpu
  rm -f "$R/sys/bus/pci/devices/0000:02:00.0"
  np auto
  check "no display-class function: refused:nodgpu as before" eq "$(prop vendor.gb.nouveau.result)" refused:nodgpu

  new_case NP-B4-bind                             # nouveau already loaded (bind path, 7.2a) on a 15-inch
  set_dev 0x2191
  mod_only
  np auto
  check "bind path: refused:model, exit 1" eq "$(prop vendor.gb.nouveau.result)/$RC" refused:model/1
  check "bind path: no bind write" nobindw
  check "bind path: no marker, no lock, loading never set" no_write
  check "bind path: logged" has "$R/dev/kmsg" "guard: 0000:02:00.0 is NVIDIA 0x2191 TU116, not the 13.5-inch"

  for _id in 0x1f91 0x1f99 0x1F9D 0x1fdd 0x1f80 0x1fff 0x1FB8; do   # TU117: unchanged behaviour (any hex case)
    new_case "NP-B4-tu117-$_id"
    set_dev "$_id"
    cmdline gb.nouveau=once
    np auto
    check "TU117 $_id loads (clean, one nouveau insmod)" eq "$(prop vendor.gb.nouveau.result)/$(nins)" clean/1
    check "TU117 $_id: no model line" hasnt "$R/dev/kmsg" "not the 13.5-inch"
  done
  for _id in 0x1f7f 0x2000 0x1e00 0x2180 0x10fa; do                # just outside the block, and the HDA id
    new_case "NP-B4-edge-$_id"
    set_dev "$_id"
    cmdline gb.nouveau=once
    np auto
    check "$_id is not a TU117: refused:model, no insmod" eq "$(prop vendor.gb.nouveau.result)/$(nins)" refused:model/0
  done

  new_case NP-B4-late-insmod                      # the id changes inside the load window (after the marker)
  printf '0x2191\n' > "$T/ctl/model_on_marker"
  cmdline gb.nouveau=once
  np auto
  check "late: refused:model, nouveau.ko never" eq "$(prop vendor.gb.nouveau.result)/$(nins)" refused:model/0
  check "late: marker removed (nothing probed), lock released, loading=0" \
    eq "$(test -e "$R/data/vendor/gb/nouveau.loading" && echo M)$(test -e "$R/dev/gb_esp.lock" && echo L)$(prop vendor.gb.nouveau.loading)" 0
  check "late: logged" has "$R/dev/kmsg" "guard: 0000:02:00.0 is NVIDIA 0x2191 TU116, not the 13.5-inch"

  new_case NP-B4-late-bind                        # the same inside the bind window
  mod_only
  printf '0x1f36\n' > "$T/ctl/model_on_marker"
  np auto
  check "late bind: refused:model, no bind write" eq "$(prop vendor.gb.nouveau.result)/$(nobindw && echo none)" refused:model/none
  check "late bind: marker removed, lock released" \
    eq "$(test -e "$R/data/vendor/gb/nouveau.loading" && echo M)$(test -e "$R/dev/gb_esp.lock" && echo L)" ""
fi

if want NP-24; then
  CASE=NP-24
  sh "$HERE/rc_lint.sh" "$NAT/gb_nouveau.rc" > "$WORK/rc_lint.txt" 2>&1
  check "rc_lint gb_nouveau.rc" eq "$?" 0
  cat "$WORK/rc_lint.txt"
  if [ -n "${GB_INTERFACES:-}" ] && [ -r "$GB_INTERFACES" ]; then
    # v1.2: the first code block after "### 15.1 " is the 15.1-v12 text (the v1.1 block follows it, superseded)
    awk '/^### 15.1 /{f=1} f && /^```$/{n++; if (n == 2) exit; next} f && n == 1' "$GB_INTERFACES" > "$WORK/rc_spec.txt"
    grep -v '^#' "$NAT/gb_nouveau.rc" > "$WORK/rc_body.txt"
    check "rc body identical to INTERFACES 15.1 (v1.2: 15.1-v12)" eq "$(diff "$WORK/rc_spec.txt" "$WORK/rc_body.txt" > "$WORK/rc_diff.txt"; echo $?)" 0
    [ -s "$WORK/rc_diff.txt" ] && cat "$WORK/rc_diff.txt"
  else
    printf 'note NP-24: GB_INTERFACES not set, exact 15.1 comparison skipped\n'
  fi
fi

pkill -f "^/bin/sleep 300" 2>/dev/null
printf 'RESULT pass=%s fail=%s%s\n' "$NPASS" "$NFAIL" "${FAILED:+ failed:$FAILED}"
[ "$NFAIL" = 0 ]
