#!/usr/bin/env bash
# Log in (if needed), wait for the session to settle, then: host CPU perf level, Settings cold start x2, scroll test x2.
export MSYS_NO_PATHCONV=1
PY="${GB_PYTHON:-python}"
cd "${GB_HOME:-$(dirname "$0")}"
perf() { powershell -c "[math]::Round((Get-Counter '\Processor Information(_Total)\% Processor Performance').CounterSamples.CookedValue)"; }
u=$($PY -I gbsh.py -t 30 "am get-current-user" 2>/dev/null | tr -d '\r\n ')
if [ "$u" != "10" ]; then $PY -I gbsh.py -t 90 "input tap 639 333; sleep 25; am get-current-user" >/dev/null; fi
end=$(( $(date +%s) + 1500 ))
while :; do
  l=$($PY -I gbsh.py -t 30 "cut -d' ' -f1 /proc/loadavg" 2>/dev/null | tr -d '\r\n ')
  [ -n "$l" ] && awk "BEGIN{exit !($l+0 < 1.5)}" && break
  [ $(date +%s) -gt $end ] && { echo "settle timeout (load $l)"; break; }
  sleep 30
done
echo "host_perf_before=$(perf)%"
for i in 1 2; do $PY -I gbsh.py -t 120 "am force-stop --user 10 com.android.settings; sleep 3; am start --user 10 -W -a android.settings.SETTINGS 2>&1 | grep TotalTime"; done
for r in 1 2; do $PY -I gbsh.py -t 300 "sh /data/local/tmp/jank.sh 2>&1 | grep -E 'Total frames|Janky frames:|50th percentile|90th percentile|SF missed' | tr '\n' ' '; echo"; done
echo "host_perf_after=$(perf)%"
