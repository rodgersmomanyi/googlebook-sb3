#!/usr/bin/env bash
# Wait (max $1 s, default 1200) for the lapis guest's sys.boot_completed=1 via the serial console; print uptime.
export MSYS_NO_PATHCONV=1
PY="${GB_PYTHON:-python}"
end=$(( $(date +%s) + ${1:-1200} ))
cd "${GB_HOME:-$(dirname "$0")}"
until grep -aq "starting service 'gb_console'" serial0.log 2>/dev/null; do
  [ "$(date +%s)" -gt "$end" ] && { echo "TIMEOUT waiting for console"; exit 1; }; sleep 5; done
while :; do
  o=$($PY -I gbsh.py -t 15 'getprop sys.boot_completed' 2>/dev/null | tr -d '\r\n ')
  [ "$o" = "1" ] && break
  [ "$(date +%s)" -gt "$end" ] && { echo "TIMEOUT waiting for boot_completed"; exit 1; }
  sleep 10
done
$PY -I gbsh.py -t 20 'echo BOOT_COMPLETED uptime=$(cut -d" " -f1 /proc/uptime)'
