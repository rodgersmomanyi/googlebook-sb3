#!/system/bin/sh
# W5 sampler (test-only, throwaway VMs): reads /sys/kernel/gb_vnni/summary (0400, root) at fixed guest uptimes
# (60, 200, 420, 600, 900, 1200, 1800 s), right after sys.boot_completed=1, on request (touch /dev/gbw5/snap-<label>)
# and every 10 s (first line only). Key points go to /dev/kmsg as "GBW5S <label> t=<uptime> <first line>" (so they
# land in the host's serial0.log with the kernel time) and, with the comm/RIP LRU, to /dev/gbw5/full.txt.
# Started detached from the root console; stops on /dev/gbw5/stop or at uptime 3600 s.
D=/dev/gbw5
S=/sys/kernel/gb_vnni/summary
TB=/system/bin/toybox
$TB mkdir -p $D
snap() {
  read t rest < /proc/uptime
  l=$($TB head -n 1 $S)
  echo "$1 t=$t $l" >> $D/series.txt
  echo "GBW5S $1 t=$t $l" > /dev/kmsg
  { echo "== $1 t=$t"; $TB cat $S; } >> $D/full.txt
}
snap start
todo="60 200 420 600 900 1200 1800"
bc=0
last=0
while :; do
  read u rest < /proc/uptime
  s=${u%.*}
  if [ $bc = 0 ] && [ "$(/system/bin/getprop sys.boot_completed)" = 1 ]; then snap boot_completed; bc=1; fi
  new=""
  for T in $todo; do
    if [ $s -ge $T ]; then snap t$T; else new="$new $T"; fi
  done
  todo=$new
  for f in $D/snap-*; do
    [ -f "$f" ] || continue
    n=${f#$D/snap-}
    $TB rm -f "$f"
    snap "$n"
  done
  if [ $((s - last)) -ge 10 ]; then
    last=$((s / 10 * 10))
    echo "p t=$u $($TB head -n 1 $S)" >> $D/series.txt
  fi
  [ -f $D/stop ] && { snap stop; exit 0; }
  [ $s -ge 3600 ] && exit 0
  $TB sleep 0.5
done
