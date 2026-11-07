#!/system/bin/sh
# gb_camtest.sh - opt-in camera self-test for the Surface Book 3 native boot (installed as /odm/bin/gb_camtest.sh,
# native tree only). Source: C:\GB-build\native\camera-userspace\integration\gb_camtest.sh (wave 11, w11prep
# camera-hw3: the third hardware test showed a black preview on both cameras and left no trace in the logs).
#
# OFF unless vendor.gb.camtest is 1. For one boot: gb\gb.cfg on the stick's FAT32 partition with the line
# "GB_PROP_1=vendor.gb.camtest=1" and gb.overlay=1 typed at the boot menu (FLASHING.md section 8). The camera
# component's rc starts it once sys.boot_completed=1. Once per boot it
#   1. waits GB_CAMTEST_DELAY s (default 30, 0-300): the camera provider has enumerated, no app has a camera open,
#   2. lists the camera device nodes (mode, owner, group, SELinux label, as counts) and the media entity names,
#   3. runs /odm/bin/lcam-probe -l, then a short capture of each listed camera (-s 640x480 -n 30, NV12 from the
#      software ISP) with libcamera's INFO log in a private file; no frame is saved (no -o): lcam-probe reports the
#      frames, the frame rate and the mean luma of the last frame only,
#   4. writes one verdict per camera (frames-ok, frames-dark, no-frames, configure-failed, start-failed, busy, hung,
#      ...) and vendor.gb.camtest_result,
#   5. at uptime >= GB_CAMTEST_LATE s (default 400, 0 = skip, at most 1800) prints a filtered 'dumpsys media.camera'
#      (camera devices, clients, the service's events log, stream counters, errors): the state after the person's
#      own Camera-app session, just before gb_bootlog's t420 snapshot.
# Every line goes to the kernel log as "gb_camtest: ..." (stdio_to_kmsg), so every later gb_bootlog snapshot has it;
# the kernel's own camera lines of the same seconds sit between "gb_camtest: begin" and "gb_camtest: end".
# Read-only except the captures themselves (the privacy LED lights for about 3 s per camera: that is the test) and
# vendor.gb.camtest_result. Every step is bounded (timeout), never fatal, exit 0.
# GB_CAMTEST_ROOT, GB_CAMTEST_PROBE, GB_CAMTEST_WORK, GB_CAMTEST_DELAY, GB_CAMTEST_LATE, GB_CAMTEST_TIMEOUT: test
# harness / bring-up overrides (init sets none of them).
R=${GB_CAMTEST_ROOT:-}
PROBE=${GB_CAMTEST_PROBE:-/odm/bin/lcam-probe}
W=${GB_CAMTEST_WORK:-/dev/gb_camtest}
num() { case $1 in ''|*[!0-9]*) echo "$2";; *) if [ "$1" -gt "$3" ]; then echo "$3"; else echo "$1"; fi;; esac; }
D=$(num "${GB_CAMTEST_DELAY:-30}" 30 300)
LATE=$(num "${GB_CAMTEST_LATE:-400}" 400 1800)
TO=$(num "${GB_CAMTEST_TIMEOUT:-40}" 40 120); [ "$TO" -lt 5 ] && TO=5
LMAX=150        # libcamera log lines shown per lcam-probe run (the rest is counted)
OMAX=20         # lcam-probe output lines shown per run
CSPAT='Number of camera devices|Device [0-9]+ (maps|is)|Active Camera Clients|events log|CONNECT|DISCONNECT|EVICT|In-flight|Stream\[|Frames produced|frames|State:|[Ee]rror|ERROR|[Ff]ailed|[Tt]imed out|timeout'

say() { printf 'gb_camtest: %.380s\n' "$*"; }
up() { u=$(cut -d. -f1 "$R/proc/uptime" 2>/dev/null); case $u in ''|*[!0-9]*) u=0;; esac; echo "$u"; }

[ "$(getprop vendor.gb.camtest)" = 1 ] || exit 0
[ -n "$(getprop vendor.gb.camtest_result)" ] && { say "already ran this boot: $(getprop vendor.gb.camtest_result)"; exit 0; }
setprop vendor.gb.camtest_result running
rm -rf "$W"
mkdir -m 0700 "$W" 2>/dev/null || { say "cannot create $W: nothing tested"; setprop vendor.gb.camtest_result "error nowork"; exit 0; }

say "begin: opt-in camera self-test (vendor.gb.camtest=1), uptime $(up) s, waiting $D s; the privacy LED lights during each capture"
sleep "$D"
say "services: camera-provider=$(getprop init.svc.vendor.camera-provider-2-7-legacy-wrapper) cameraserver=$(getprop init.svc.cameraserver) ro.hardware.camera=$(getprop ro.hardware.camera) vcm=$(getprop vendor.gb.vcm)"

# ---- 2. device nodes (counted by mode/owner/group/label) and the media entities
for g in media video v4l-subdev; do
  set -- "$R"/dev/$g[0-9]*
  if [ ! -e "$1" ]; then say "nodes /dev/$g*: none"; continue; fi
  s=$(for f in "$@"; do stat -c '%a %U:%G %C' "$f" 2>/dev/null || stat -c '%a %U:%G' "$f" 2>/dev/null; done | sort | uniq -c | tr -s ' ' | tr '\n' ';')
  say "nodes /dev/$g*: $# node(s):${s%;}"
done
[ -e "$R/dev/dma_heap/system" ] && say "nodes /dev/dma_heap/system: $(stat -c '%a %U:%G %C' "$R/dev/dma_heap/system" 2>/dev/null || stat -c '%a %U:%G' "$R/dev/dma_heap/system" 2>/dev/null)"
for d in "$R"/sys/bus/media/devices/media*; do
  [ -e "$d" ] && say "media: ${d##*/} model=$(cat "$d/model" 2>/dev/null)"
done
cap=0; n=0
for d in "$R"/sys/class/video4linux/*; do
  [ -e "$d/name" ] || continue
  nm=$(cat "$d/name" 2>/dev/null)
  case $nm in
    *[Cc]apture*) cap=$((cap + 1));;
    *) n=$((n + 1)); [ $n -le 40 ] && say "entity: ${d##*/} $nm";;
  esac
done
say "entity: $cap capture node(s), $n other node(s)"

# ---- 3. lcam-probe: list, then one short capture per camera
# run TAG ARGS...: lcam-probe with libcamera's INFO log in $W/lc; shows up to OMAX output and LMAX log lines; RC=exit
run() {
  t=$1; shift
  rm -f "$W/lc" "$W/out"
  LIBCAMERA_LOG_FILE=$W/lc LIBCAMERA_LOG_LEVELS='*:INFO' LIBCAMERA_LOG_NO_COLOR=1 \
    timeout "$TO" "$PROBE" "$@" > "$W/out" 2>&1 < /dev/null
  RC=$?
  for f in out lc; do
    [ -f "$W/$f" ] || continue
    if [ $f = out ]; then mx=$OMAX; p="$t:"; else mx=$LMAX; p="$t: lc:"; fi
    j=0
    while IFS= read -r l; do
      j=$((j + 1)); [ $j -le $mx ] && say "$p $l"
    done < "$W/$f"
    [ $j -gt $mx ] && say "$p ($((j - mx)) more line(s) not shown)"
  done
  say "$t: exit $RC"
}
# verdict of one capture from lcam-probe's output (tools\lcam_probe.cpp) and its exit code
verdict() {
  o=$W/out
  if grep -q 'frames completed' "$o" 2>/dev/null; then
    c=$(sed -n 's/^\([0-9][0-9]*\) frames completed.*/\1/p' "$o" | head -n 1)
    if [ "${c:-0}" -gt 0 ]; then
      m=$(sed -n 's/.*mean(plane0 bytes)=\([0-9][0-9]*\).*/\1/p' "$o" | head -n 1)
      if [ -z "$m" ]; then echo frames-nomean; elif [ "$m" -lt 20 ]; then echo frames-dark; else echo frames-ok; fi
    else
      echo no-frames
    fi
    return
  fi
  if grep -q 'acquire failed' "$o" 2>/dev/null; then echo busy
  elif grep -q 'configure failed' "$o" 2>/dev/null; then echo configure-failed
  elif grep -q 'buffer allocation failed' "$o" 2>/dev/null; then echo alloc-failed
  elif grep -q 'start failed' "$o" 2>/dev/null; then echo start-failed
  elif grep -q 'no camera' "$o" 2>/dev/null; then echo missing
  elif grep -q 'CANNOT LINK' "$o" 2>/dev/null; then echo link-failed
  else case $RC in 124|137|143) echo hung;; *) echo "error-$RC";; esac
  fi
}

if [ ! -x "$R$PROBE" ] && [ ! -x "$PROBE" ]; then
  say "list: $PROBE is missing: nothing tested"; setprop vendor.gb.camtest_result "error noprobe"
  rm -rf "$W"; exit 0
fi
run list -l
N=$(sed -n 's/^\([0-9][0-9]*\) camera(s)$/\1/p' "$W/out" 2>/dev/null | head -n 1)
case $N in ''|*[!0-9]*) N=0;; esac
[ "$N" -gt 4 ] && N=4
res="n=$N"
ci=0
while [ $ci -lt "$N" ]; do
  run "cam$ci" -c $ci -s 640x480 -n 30
  v=$(verdict)
  if [ "$v" = busy ]; then      # an app opened the camera meanwhile: one retry
    say "cam$ci: busy, retry in 15 s"; sleep 15
    run "cam$ci" -c $ci -s 640x480 -n 30
    v=$(verdict)
  fi
  say "cam$ci: verdict $v"
  res="$res c$ci=$v"
  ci=$((ci + 1))
  sleep 2
done
[ "$N" = 0 ] && say "list: libcamera lists no camera (see the lc: lines above)"
setprop vendor.gb.camtest_result "$(printf '%.91s' "$res")"
say "result: $res"
say "end: uptime $(up) s"

# ---- 5. the camera service after the person's own session (filtered, bounded)
if [ "$LATE" -gt 0 ]; then
  k=0
  while [ "$(up)" -lt "$LATE" ] && [ $k -lt 400 ]; do sleep 5; k=$((k + 1)); done
  say "late: uptime $(up) s: dumpsys media.camera (devices, clients, events, streams, errors only)"
  timeout 20 dumpsys media.camera > "$W/cs" 2>&1 < /dev/null
  i=0
  grep -E "$CSPAT" "$W/cs" 2>/dev/null | head -n 120 > "$W/csf"
  while IFS= read -r l; do i=$((i + 1)); say "cs: $l"; done < "$W/csf"
  say "late: $i line(s) of $(wc -l < "$W/cs" 2>/dev/null | tr -cd 0-9) shown"
fi
rm -rf "$W"
exit 0
