#!/bin/sh
# shims.sh -- writes the PATH shims used by test_gb_nouveau.sh into directory $1.
# Every shim works on the fake device tree $GB_ROOT and the per-case directory $GBT:
#   $GBT/props/<name>   property store          $GBT/log/*.log   call logs
#   $GBT/ctl/*          failure injection        $GBT/esp, $GBT/payload_tree   fake ESP / erofs contents
# The fake "kernel" reacts in the sleep shim (fake clock = $GB_ROOT/proc/uptime): sysfs unbind and (v1.2) nouveau
# bind writes take effect, timed dmesg lines appear, timed property changes happen (ctl/prop_at), a crash kills the
# script under test, resumes bump suspend_stats. Review fix pass (INTERFACES 29): ctl/suspend_at (a system suspend: the
# CLOCK_BOOTTIME uptime jumps), ctl/term_at (init stops the script: SIGTERM, optionally with sys.powerctl set),
# ctl/at ops d3cold/node/copy, ctl/rmmod_hang, and a pgrep shim (ctl/pgrep_n).
set -e
D=$1
mkdir -p "$D"

w() { cat > "$D/$1"; chmod 0755 "$D/$1"; }

w _kbind <<'EOF'
#!/bin/sh
# _kbind <bdf>: the fake kernel's nouveau probe of <bdf> (insmod of nouveau.ko, or a write to drivers/nouveau/bind).
# ctl/nobind: the probe fails (-110); ctl/no_d3cold: bound but stays in D0; ctl/dmesg_add: extra kernel lines.
B=$1
K=$GB_ROOT/dev/kmsg
[ -d "$GB_ROOT/sys/bus/pci/devices/$B" ] || exit 0
echo "[   30.100000] nouveau $B: NVIDIA TU117 (167000a1)" >> "$K"
if [ -e "$GBT/ctl/nobind" ]; then
  echo "[   30.200000] nouveau $B: probe with driver nouveau failed with error -110" >> "$K"
  exit 0
fi
echo "[   30.200000] nouveau $B: gsp: RM version: 570.144" >> "$K"
echo "[   30.300000] nouveau $B: fbsr: size: 0x1d00000 bytes" >> "$K"
[ -f "$GBT/ctl/dmesg_add" ] && cat "$GBT/ctl/dmesg_add" >> "$K"
DR=$(readlink -f "$GB_ROOT/sys/bus/pci/devices/$B")
ln -s "$GB_ROOT/sys/bus/pci/drivers/nouveau" "$DR/driver"
for nd in card1 renderD129; do
  mkdir -p "$DR/drm/$nd"
  ln -s "$DR" "$DR/drm/$nd/device"
  ln -s "$DR/drm/$nd" "$GB_ROOT/sys/class/drm/$nd"
  : > "$GB_ROOT/dev/dri/$nd"
  /bin/chmod 0666 "$GB_ROOT/dev/dri/$nd"           # ueventd's default (D19); the kernel's, so not in chmod.log
done
if [ -e "$GBT/ctl/no_d3cold" ]; then
  echo active > "$DR/power/runtime_status"; echo D0 > "$DR/power_state"
else
  echo suspended > "$DR/power/runtime_status"; echo D3cold > "$DR/power_state"
fi
EOF

w getprop <<'EOF'
#!/bin/sh
P=$GBT/props
if [ $# -eq 0 ]; then
  for f in "$P"/*; do [ -f "$f" ] && printf '[%s]: [%s]\n' "${f##*/}" "$(cat "$f")"; done
  exit 0
fi
case $1 in '' | *[!A-Za-z0-9._-]*) exit 1 ;; esac
if [ -f "$P/$1" ]; then printf '%s\n' "$(cat "$P/$1")"; else printf '\n'; fi
EOF

w setprop <<'EOF'
#!/bin/sh
[ $# -eq 2 ] || exit 1
case $1 in '' | *[!A-Za-z0-9._-]*) exit 1 ;; esac
if [ ${#2} -gt 91 ]; then printf 'TOOLONG %s\n' "$1" >> "$GBT/log/setprop.log"; exit 1; fi
printf '%s=%s\n' "$1" "$2" >> "$GBT/log/setprop.log"
# (Wave 2b) ctl/displace_at_release: when the load window closes (loading=0, right before the ESP lock is released) a
# stealer (pid 4246) moves the lock away; it puts it back 2 fake s later (ctl/at "unsteal 4246")
if [ "$1 $2" = "vendor.gb.nouveau.loading 0" ] && [ -e "$GBT/ctl/displace_at_release" ] && [ -d "$GB_ROOT/dev/gb_esp.lock" ]; then
  rm -f "$GBT/ctl/displace_at_release"
  /bin/mv -T "$GB_ROOT/dev/gb_esp.lock" "$GB_ROOT/dev/gb_esp.lock.stale.4246"
  read -r _u _ < "$GB_ROOT/proc/uptime"
  printf '%s unsteal 4246\n' "$(awk -v u="$_u" 'BEGIN { printf "%.2f", u + 2 }')" >> "$GBT/ctl/at"
fi
if [ "$1" = persist.vendor.gb.gfx_mode ]; then           # ordered evidence: which root-only flags exist at this set
  f=$GB_ROOT/dev/gb_dgpu
  k=0; n=0; x=0
  [ -e "$f/keep" ] && k=1
  [ -e "$f/noload" ] && n=1
  [ -e "$f/wedged" ] && x=1
  printf 'setprop %s=%s keep=%s noload=%s wedged=%s\n' "$1" "$2" "$k" "$n" "$x" >> "$GBT/log/order.log"
fi
case $1 in
  ctl.stop) printf 'stopped' > "$GBT/props/init.svc.$2" ;;
  ctl.start) ;;
  *) printf '%s' "$2" > "$GBT/props/$1" ;;
esac
EOF

w insmod <<'EOF'
#!/bin/sh
m=$1
shift
printf '%s|%s\n' "${m#"$GB_ROOT"}" "$*" >> "$GBT/log/insmod.log"
[ -f "$m" ] || { echo "insmod: $m: No such file or directory" >&2; exit 1; }
n=${m##*/}
n=${n%.ko}
n=$(printf '%s' "$n" | tr - _)
[ -e "$GBT/ctl/insmod_fail_$n" ] && { echo "insmod: fake failure" >&2; exit 1; }
[ -e "$GBT/ctl/insmod_sig_$n" ] && exit 139                      # killed by a signal (oops in insmod's context)
if [ "$n" = nouveau ] && [ -e "$GBT/ctl/oops_on_nouveau" ]; then  # an oops during the probe, insmod then fails
  echo "[   30.150000] BUG: kernel NULL pointer dereference, address: 0000000000000008" >> "$GB_ROOT/dev/kmsg"
  exit 1
fi
if [ "$n" = drm_exec ]; then                                      # events during the load window
  [ -e "$GBT/ctl/detach_on_dep" ] && : > "$GB_ROOT/dev/gb_dgpu/detaching"
  [ -e "$GBT/ctl/remove_on_dep" ] && rm -f "$GB_ROOT/sys/bus/pci/devices/0000:02:00.0"
fi
[ -d "$GB_ROOT/sys/module/$n" ] && { echo "insmod: File exists" >&2; exit 1; }
mkdir -p "$GB_ROOT/sys/module/$n"
echo 0 > "$GB_ROOT/sys/module/$n/refcnt"
printf '%s panic=%s oops=%s wake=%s/%s\n' "$n" "$(cat "$GB_ROOT/proc/sys/kernel/panic")" \
  "$(cat "$GB_ROOT/proc/sys/kernel/panic_on_oops")" "$(cat "$GB_ROOT/sys/power/wake_lock" 2>/dev/null)" \
  "$(cat "$GB_ROOT/sys/power/wake_unlock" 2>/dev/null)" >> "$GBT/log/insmod_state.log"
[ "$n" = nouveau ] || exit 0
printf 'marker=%s\n' "$(cat "$GB_ROOT/data/vendor/gb/nouveau.loading" 2>/dev/null)" >> "$GBT/log/marker_at_insmod.log"
exec "${0%/*}/_kbind" 0000:02:00.0
EOF

w rmmod <<'EOF'
#!/bin/sh
n=$(printf '%s' "$1" | tr - _)
printf '%s\n' "$1" >> "$GBT/log/rmmod.log"
[ -e "$GBT/ctl/rmmod_fail_$n" ] && { echo "rmmod: fake failure" >&2; exit 1; }
[ -e "$GBT/ctl/rmmod_hang_$n" ] && exec /bin/sleep 300           # the module's exit never returns
[ -d "$GB_ROOT/sys/module/$n" ] || { echo "rmmod: $1: not loaded" >&2; exit 1; }
rm -rf "$GB_ROOT/sys/module/$n"
EOF

w mount <<'EOF'
#!/bin/sh
type=
opts=
while [ $# -gt 0 ]; do
  case $1 in
    -t) type=$2; shift 2 ;;
    -o) opts=$2; shift 2 ;;
    *) break ;;
  esac
done
src=$1
dst=$2
printf 'mount type=%s opts=%s src=%s dst=%s\n' "$type" "$opts" "${src#"$GB_ROOT"}" "${dst#"$GB_ROOT"}" >> "$GBT/log/mount.log"
kind=$type
case ",$opts," in *,bind,*) kind=bind ;; esac
[ -e "$GBT/ctl/mount_fail_$kind" ] && { echo "mount: fake failure" >&2; exit 1; }
[ -d "$dst" ] || { echo "mount: $dst: no such directory" >&2; exit 1; }
msrc=${src#"$GB_ROOT"}
case $kind in
  vfat) [ -e "$src" ] || exit 1; cp -R "$GBT/esp/." "$dst/" ;;
  erofs)
    [ -f "$src" ] || exit 1
    cp -R "$GBT/payload_tree/." "$dst/"
    [ -e "$GBT/ctl/erofs_loop" ] && msrc=/dev/block/loop7 ;;
  bind) [ -d "$src" ] || exit 1; cp -R "$src/." "$dst/" ;;
  *) exit 1 ;;
esac
printf '%s %s %s %s 0 0\n' "$msrc" "${dst#"$GB_ROOT"}" "${type:-none}" "$opts" >> "$GB_ROOT/proc/mounts"
EOF

w umount <<'EOF'
#!/bin/sh
dst=$1
m=${dst#"$GB_ROOT"}
printf 'umount %s\n' "$m" >> "$GBT/log/mount.log"
[ -e "$GBT/ctl/umount_fail" ] && exit 1
awk -v m="$m" '$2 == m { f = 1 } END { exit !f }' "$GB_ROOT/proc/mounts" || { echo "umount: $m: not mounted" >&2; exit 1; }
awk -v m="$m" '$2 != m' "$GB_ROOT/proc/mounts" > "$GB_ROOT/proc/mounts.new"
mv "$GB_ROOT/proc/mounts.new" "$GB_ROOT/proc/mounts"
case $dst in "$GB_ROOT"/?*) find "$dst" -mindepth 1 -delete ;; esac
EOF

w dmesg <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$GBT/log/dmesg.log"
if [ "${1:-}" = -w ]; then
  cat "$GB_ROOT/dev/kmsg"
  [ -e "$GBT/ctl/dmesg_w_exit" ] && exit 0
  if [ -e "$GBT/ctl/dmesg_flood" ]; then head -c 17000000 /dev/zero | tr '\0' 'x'; echo; fi
  exec /bin/sleep 300
fi
if [ -e "$GBT/ctl/dmesg_drop_mark" ]; then grep -v 'insmod-mark' "$GB_ROOT/dev/kmsg"; else cat "$GB_ROOT/dev/kmsg"; fi
EOF

w sleep <<'EOF'
#!/bin/sh
d=${1:-1}
U=$GB_ROOT/proc/uptime
read -r a _ < "$U"
n=$(awk -v a="$a" -v d="$d" 'BEGIN { printf "%.2f", a + d }')
printf '%s %s\n' "$n" "$n" > "$U"
for u in "$GB_ROOT"/sys/bus/pci/drivers/*/unbind; do
  [ -f "$u" ] && [ -s "$u" ] || continue
  drv=${u%/unbind}
  drv=${drv##*/}
  while read -r b; do
    D=$(readlink -f "$GB_ROOT/sys/bus/pci/devices/$b")
    [ -n "$D" ] || continue
    rm -f "$D/driver"
    if [ -d "$D/drm" ]; then
      for nd in "$D"/drm/*; do nd=${nd##*/}; rm -f "$GB_ROOT/sys/class/drm/$nd" "$GB_ROOT/dev/dri/$nd"; done
      rm -rf "$D/drm"
    fi
    printf '%s %s\n' "$drv" "$b" >> "$GBT/log/unbind.log"
  done < "$u"
  : > "$u"
done
due() { awk -v n="$n" -v at="$1" 'BEGIN { exit !(n >= at) }'; }
bf=$GB_ROOT/sys/bus/pci/drivers/nouveau/bind          # v1.2 bind path: a regular file (a FIFO = a hung probe)
if [ -f "$bf" ] && [ -s "$bf" ]; then
  while read -r b; do
    mk=$(cat "$GB_ROOT/data/vendor/gb/nouveau.loading" 2>/dev/null)
    lk=$(cut -d' ' -f2 "$GB_ROOT/dev/gb_esp.lock/owner" 2>/dev/null)
    sy=0
    [ -f "$GBT/log/sync.log" ] && sy=$(wc -l < "$GBT/log/sync.log" | tr -d ' ')
    printf '%s marker=%s lock=%s syncs=%s panic=%s at=%s wake=%s/%s\n' "$b" "$mk" "$lk" "$sy" \
      "$(cat "$GB_ROOT/proc/sys/kernel/panic")" "$n" "$(cat "$GB_ROOT/sys/power/wake_lock" 2>/dev/null)" \
      "$(cat "$GB_ROOT/sys/power/wake_unlock" 2>/dev/null)" >> "$GBT/log/bind.log"
    "${0%/*}/_kbind" "$b"
  done < "$bf"
  : > "$bf"
fi
if [ -f "$GBT/ctl/at" ]; then                          # lines "<uptime> touch|rm|kbind <arg>", each applied once:
  : > "$GBT/ctl/at.new"                                # touch/rm a path under GB_ROOT, or the kernel binds <bdf>
  while read -r at op arg; do
    if due "$at"; then
      case $op in
        touch) : > "$GB_ROOT/$arg" ;;
        rm) rm -rf "${GB_ROOT:?}/$arg" ;;
        kbind) "${0%/*}/_kbind" "$arg" ;;
        d3cold)                                        # the bound dGPU runtime-suspends into D3cold
          DD=$(readlink -f "$GB_ROOT/sys/bus/pci/devices/$arg")
          echo suspended > "$DD/power/runtime_status"; echo D3cold > "$DD/power_state" ;;
        node)                                          # ueventd re-creates /dev/dri/<arg>: a new inode, 0666
          rm -f "$GB_ROOT/dev/dri/$arg"; : > "$GB_ROOT/dev/dri/$arg"; /bin/chmod 0666 "$GB_ROOT/dev/dri/$arg" ;;
        copy) cp "$GBT/ctl/copy_src" "$GB_ROOT/$arg" ;;  # another writer replaces a file (e.g. the marker)
        save) cat "$GB_ROOT/$arg" >> "$GBT/log/save.log" 2>/dev/null ;;   # (Wave 2b) record a file's content now
        unsteal)                                       # (Wave 2b) the stealer <arg> puts the moved lock back
          echo "unsteal $arg at $n" >> "$GBT/log/at.log"
          /bin/mv -T "$GB_ROOT/dev/gb_esp.lock.stale.$arg" "$GB_ROOT/dev/gb_esp.lock" ;;
      esac
    else
      printf '%s %s %s\n' "$at" "$op" "$arg" >> "$GBT/ctl/at.new"
    fi
  done < "$GBT/ctl/at"
  /bin/mv "$GBT/ctl/at.new" "$GBT/ctl/at"
fi
if [ -f "$GBT/ctl/prop_at" ]; then                     # lines "<uptime> <property> <value>", each applied once
  : > "$GBT/ctl/prop_at.new"
  while read -r at pn pv; do
    if due "$at"; then printf '%s' "$pv" > "$GBT/props/$pn"; else printf '%s %s %s\n' "$at" "$pn" "$pv" >> "$GBT/ctl/prop_at.new"; fi
  done < "$GBT/ctl/prop_at"
  /bin/mv "$GBT/ctl/prop_at.new" "$GBT/ctl/prop_at"
fi
if [ -f "$GBT/ctl/late_dmesg_at" ] && due "$(cat "$GBT/ctl/late_dmesg_at")"; then
  cat "$GBT/ctl/late_dmesg" >> "$GB_ROOT/dev/kmsg"
  rm -f "$GBT/ctl/late_dmesg_at"
fi
if [ -f "$GBT/ctl/resume_at" ]; then
  r=$(head -n 1 "$GBT/ctl/resume_at")
  if [ -n "$r" ] && due "$r"; then
    s=$(cat "$GB_ROOT/sys/power/suspend_stats/success")
    echo $((s + 1)) > "$GB_ROOT/sys/power/suspend_stats/success"
    sed -i 1d "$GBT/ctl/resume_at"
  fi
fi
if [ -f "$GBT/ctl/undetach_at" ] && due "$(cat "$GBT/ctl/undetach_at")"; then
  rm -f "$GB_ROOT/dev/gb_dgpu/detaching" "$GBT/ctl/undetach_at"
fi
if [ -f "$GBT/ctl/suspend_at" ]; then                  # "<uptime> <seconds>": a system suspend of <seconds>; the
  read -r sa ss < "$GBT/ctl/suspend_at"                # uptime (CLOCK_BOOTTIME) jumps, sleep itself does not count it
  if due "$sa"; then
    rm -f "$GBT/ctl/suspend_at"
    n=$(awk -v n="$n" -v d="$ss" 'BEGIN { printf "%.2f", n + d }')
    printf '%s %s\n' "$n" "$n" > "$U"
    s=$(cat "$GB_ROOT/sys/power/suspend_stats/success")
    echo $((s + 1)) > "$GB_ROOT/sys/power/suspend_stats/success"
    echo "suspend at $sa for $ss s" >> "$GBT/log/suspend.log"
  fi
fi
if [ -f "$GBT/ctl/term_at" ]; then                     # "<uptime> <sys.powerctl value | ->": init stops the service
  read -r ta tv < "$GBT/ctl/term_at"
  if due "$ta"; then
    rm -f "$GBT/ctl/term_at"
    [ "$tv" = - ] || printf '%s' "$tv" > "$GBT/props/sys.powerctl"
    echo "term at $n powerctl=$tv" >> "$GBT/log/term.log"
    kill -TERM "$PPID"
  fi
fi
if [ -f "$GBT/ctl/crash_at" ] && due "$(cat "$GBT/ctl/crash_at")"; then
  rm -f "$GBT/ctl/crash_at"
  echo "crash at $n" >> "$GBT/log/crash.log"
  kill -9 "$PPID"
fi
/bin/sleep 0.01
EOF

w logcat <<'EOF'
#!/bin/sh
# logcat (wave 3, item 18): with -d prints ctl/logcat (if any) and exits; without -d a real logcat streams forever,
# so the shim logs the call and fails (a test can see that every call used -d)
printf '%s
' "$*" >> "$GBT/log/logcat.log"
case " $* " in *" -d "*) [ -f "$GBT/ctl/logcat" ] && cat "$GBT/ctl/logcat"; exit 0 ;; esac
exit 3
EOF

w pgrep <<'EOF'
#!/bin/sh
# pgrep: logs its arguments; "found" (exit 0) while ctl/pgrep_n counts down from its value, then "none" (exit 1)
printf '%s\n' "$*" >> "$GBT/log/pgrep.log"
f=$GBT/ctl/pgrep_n
[ -f "$f" ] || exit 1
c=$(cat "$f")
case $c in '' | *[!0-9]*) exit 1 ;; esac
[ "$c" -gt 0 ] || exit 1
echo $((c - 1)) > "$f"
echo 4242
exit 0
EOF

w mv <<'EOF'
#!/bin/sh
# The real mv (next in PATH after the shims). Failure injection ctl/steal_race: a live holder (pid 4243) re-creates
# the ESP lock right before a stealer's rename of it (one shot); ctl/steal_race_noowner: the same without an owner
# file yet (Wave 2b); ctl/steal_intrude (Wave 2b): right after that rename a newcomer's empty directory takes the
# lock path (ctl/at "rm dev/gb_esp.lock" makes it back off).
if [ "$1" = -T ] && [ "$2" = "$GB_ROOT/dev/gb_esp.lock" ]; then
  if [ -e "$GBT/ctl/steal_race" ] || [ -e "$GBT/ctl/steal_race_noowner" ]; then
    rm -rf "$2"
    mkdir "$2"
    [ -e "$GBT/ctl/steal_race" ] && echo "4243 gb_bootlog 30" > "$2/owner"
    rm -f "$GBT/ctl/steal_race" "$GBT/ctl/steal_race_noowner"
  fi
  if [ -e "$GBT/ctl/steal_intrude" ]; then
    rm -f "$GBT/ctl/steal_intrude"
    PATH=${PATH#*:} mv "$@" || exit 1
    mkdir "$2"
    exit 0
  fi
fi
PATH=${PATH#*:}
exec mv "$@"
EOF

w mkdir <<'EOF'
#!/bin/sh
# The real mkdir (next in PATH after the shims). Failure injection (Wave 2b) ctl/mkdir_race: right after the script's
# mkdir of the ESP lock, an owner file with the ctl file's line appears in it (a lock put back in its place; one shot;
# (wave 3) the line "SELF" stands for "<pid of the calling script> gb_nouveau 3": a late put-back of its own lock).
# (wave 3) Every mkdir of the ESP lock is logged to log/lockmkdir.log (INTERFACES 5.4 r4: none while a steal is busy).
[ "$1" = "$GB_ROOT/dev/gb_esp.lock" ] && echo "mkdir lock" >> "$GBT/log/lockmkdir.log"
if [ -f "$GBT/ctl/mkdir_race" ] && [ "$1" = "$GB_ROOT/dev/gb_esp.lock" ]; then
  PATH=${PATH#*:} mkdir "$@" || exit 1
  if [ "$(cat "$GBT/ctl/mkdir_race")" = SELF ]; then echo "$PPID gb_nouveau 3" > "$1/owner"
  else cat "$GBT/ctl/mkdir_race" > "$1/owner"; fi
  rm -f "$GBT/ctl/mkdir_race"
  exit 0
fi
PATH=${PATH#*:}
exec mkdir "$@"
EOF

w sync <<'EOF'
#!/bin/sh
echo sync >> "$GBT/log/sync.log"
# ctl/detach_on_bind_marker: gb_power starts a detach right after the bind path synced its marker (one shot)
if [ -e "$GBT/ctl/detach_on_bind_marker" ] && grep -q '^mode=bind ' "$GB_ROOT/data/vendor/gb/nouveau.loading" 2>/dev/null; then
  rm -f "$GBT/ctl/detach_on_bind_marker"
  : > "$GB_ROOT/dev/gb_dgpu/detaching"
fi
exit 0
EOF

w stat <<'EOF'
#!/bin/sh
# "stat -c %u <file>" (the form gb_notify uses for its owner check): files of the fake tree count as root-owned (0)
# unless ctl/uid_<basename> holds another uid. Every other form is the real stat (next in PATH).
if [ $# -eq 3 ] && [ "$1" = -c ] && [ "$2" = %u ]; then
  f=${3##*/}
  if [ -f "$GBT/ctl/uid_$f" ]; then cat "$GBT/ctl/uid_$f"; else echo 0; fi
  exit 0
fi
PATH=${PATH#*:}
exec stat "$@"
EOF

w chmod <<'EOF'
#!/bin/sh
printf '%s %s\n' "$1" "${2#"$GB_ROOT"}" >> "$GBT/log/chmod.log"
exec /bin/chmod "$@"
EOF

w chown <<'EOF'
#!/bin/sh
printf '%s %s\n' "${2#"$GB_ROOT"}" "$1" >> "$GBT/log/chown.log"
EOF

w uname <<'EOF'
#!/bin/sh
if [ "${1:-}" = -r ]; then cat "$GBT/krel"; echo; else exec /bin/uname "$@"; fi
EOF

w cmd <<'EOF'
#!/bin/sh
if [ "$1 $2" = "gpu vkjson" ]; then
  printf '{"devices":[{"properties":{"apiVersion":4206847,"deviceName": "Intel(R) Iris(R) Plus Graphics (ICL GT2)"}}]}\n'
  exit 0
fi
for a in "$@"; do printf '%s|' "$a"; done >> "$GBT/log/cmd.log"
echo >> "$GBT/log/cmd.log"
[ -e "$GBT/ctl/cmd_fail" ] && exit 1
exit 0
EOF

w am <<'EOF'
#!/bin/sh
for a in "$@"; do printf '%s|' "$a"; done >> "$GBT/log/am.log"
echo >> "$GBT/log/am.log"
EOF

w log <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$GBT/log/alog.log"
EOF

w getenforce <<'EOF'
#!/bin/sh
echo Enforcing
EOF

w dumpsys <<'EOF'
#!/bin/sh
echo "RenderEngine: SkiaVk (Vulkan)"
EOF

w gb_power <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$GBT/log/gb_power.log"
echo "combined: fake BAT1+BAT2"
echo "present=1 nbat=2 capacity=80 status=Discharging ac=0 voltage_uV=8000000 current_uA=-500000 counter_uAh=0 full_uAh=0 design_uAh=0 e_now_uWh=0 e_full_uWh=0 net_uW=-4123456"
EOF

w nvk_probe <<'EOF'
#!/bin/sh
# Exit code per mode: ctl/nvk_rc_<mode>, else ctl/nvk_rc, else 0. ctl/nvk_detach_<mode>: a detach starts during this
# run; ctl/nvk_term_<mode>: the self-test service is stopped (SIGTERM) while this run is in progress.
printf '%s\n' "$*" >> "$GBT/log/nvk_probe.log"
m=
[ "${1:-}" = -t ] && m=${2:-}
fl=$GB_ROOT/dev/gb_dgpu
ff=0
[ -e "$fl/foreign" ] && ff=1
printf 't=%s foreign=%s/%s marker=%s lock=%s\n' "$m" "$(cat "$GBT/props/vendor.gb.nouveau.foreign" 2>/dev/null)" "$ff" \
  "$(cat "$GB_ROOT/data/vendor/gb/selftest/running" 2>/dev/null)" \
  "$(cut -d' ' -f2 "$GB_ROOT/dev/gb_esp.lock/owner" 2>/dev/null)" >> "$GBT/log/nvk_state.log"
printf 't=%s wake=%s/%s\n' "$m" "$(cat "$GB_ROOT/sys/power/wake_lock" 2>/dev/null)" \
  "$(cat "$GB_ROOT/sys/power/wake_unlock" 2>/dev/null)" >> "$GBT/log/nvk_wake.log"
echo "nvk_probe (fake): $*"
[ -n "$m" ] && [ -e "$GBT/ctl/nvk_detach_$m" ] && : > "$fl/detaching"
if [ -n "$m" ] && [ -e "$GBT/ctl/nvk_term_$m" ]; then
  p=$PPID                                # the nearest ancestor that is the self-test script
  i=0
  while [ $i -lt 6 ] && [ -n "$p" ] && [ "$p" != 1 ]; do
    if tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q 'gb_dgpu_report.sh selftest'; then
      kill -TERM "$p"
      break
    fi
    p=$(sed 's/.*) //' "/proc/$p/stat" 2>/dev/null | cut -d' ' -f2)
    i=$((i + 1))
  done
  printf 'term sent to %s\n' "$p" >> "$GBT/log/nvk_state.log"
  exec /bin/sleep 7
fi
if [ -n "$m" ] && [ -f "$GBT/ctl/nvk_rc_$m" ]; then exit "$(cat "$GBT/ctl/nvk_rc_$m")"; fi
exit "$(cat "$GBT/ctl/nvk_rc" 2>/dev/null || echo 0)"
EOF
