#!/bin/bash
# nvk_probe host (glibc) validation, v1.2 (builder): the wave-1 "probe host" checks plus the v1.2 exit rules and the
# supervision on glibc. lavapipe plays the Vulkan device (-a); the builder's Mesa NVK has no NVIDIA GPU (NO_DEVICE).
#   H1 lavapipe offscreen                          -> 0 PASS
#   H2 an Android-only mode asked for by name      -> SKIP, 1 (v1.2: never a PASS when a requested test did not run)
#   H3 -t all on the host build                    -> offscreen only, 0
#   H4 unknown -t value                            -> 2
#   H5 host NVK, no NVIDIA device                  -> 3 NO_DEVICE
#   H6 explicit -d that does not exist             -> 2 (no silent fallback to $NVK_ICD / the defaults)
#   H7 SIGTERM to the probe pid while a long test runs -> killed by SIGTERM (143), test child gone
#   H8 SIGKILL to the probe pid                    -> test child gone (PDEATHSIG)
# usage: probe_host_checks.sh <nvk_probe_host>
set -uo pipefail
P=$(readlink -f "$1")
LVP=/usr/lib/x86_64-linux-gnu/libvulkan_lvp.so
HNVK=/usr/lib/x86_64-linux-gnu/libvulkan_nouveau.so
pass=0 fail=0
chk() { if [ "$2" = "$3" ]; then echo "  PASS $1 (got $3)"; pass=$((pass + 1)); else echo "  FAIL $1 (got $3, want $2)"; fail=$((fail + 1)); fi; }
kids() { pgrep -x "$(basename "$P" | cut -c1-15)" | wc -l; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
"$P" -a -t offscreen -d "$LVP" > "$T/h1" 2>&1; chk "H1 lavapipe offscreen" 0 $?
grep -E 'coverage|offscreen: |RESULT' "$T/h1" | sed 's/^/     /'
"$P" -a -t ahb-i915-readback -d "$LVP" > "$T/h2" 2>&1; chk "H2 Android-only mode by name on the host build" 1 $?
grep -E 'SKIP|RESULT' "$T/h2" | sed 's/^/     /'
"$P" -a -t all -d "$LVP" > "$T/h3" 2>&1; chk "H3 -t all on the host build" 0 $?
grep -E 'summary|RESULT' "$T/h3" | sed 's/^/     /'
"$P" -t frobnicate -d "$LVP" > "$T/h4" 2>&1; chk "H4 unknown -t" 2 $?
"$P" -t offscreen -d "$HNVK" > "$T/h5" 2>&1; chk "H5 host NVK without an NVIDIA GPU" 3 $?
NVK_ICD=$LVP "$P" -a -t offscreen -d /nonexistent/vulkan.nouveau.so > "$T/h6" 2>&1; chk "H6 explicit -d missing, valid \$NVK_ICD" 2 $?
grep -E 'nonexistent|ERROR' "$T/h6" | sed 's/^/     /'
[ "$(kids)" = 0 ] || { echo "  (stray probe processes before H7)"; pkill -9 -x "$(basename "$P" | cut -c1-15)"; }
"$P" -a -t offscreen -n 100000000 -T 0 -d "$LVP" > "$T/h7" 2>&1 &
pid=$!; sleep 3; chk "H7 before: probe + test child running" 2 "$(kids)"
kill -TERM $pid; wait $pid; rc=$?; sleep 0.5
chk "H7 SIGTERM to the probe: exit status 143" 143 $rc
chk "H7 no process left" 0 "$(kids)"
grep -E 'INTERRUPTED' "$T/h7" | sed 's/^/     /'
"$P" -a -t offscreen -n 100000000 -T 0 -d "$LVP" > "$T/h8" 2>&1 &
pid=$!; sleep 3; chk "H8 before: probe + test child running" 2 "$(kids)"
kill -KILL $pid; wait $pid 2>/dev/null; sleep 0.5
chk "H8 SIGKILL to the probe: no test child left" 0 "$(kids)"
pkill -9 -x "$(basename "$P" | cut -c1-15)" 2>/dev/null
echo "RESULT: $([ $fail = 0 ] && echo PASS || echo FAIL) ($pass passed, $fail failed)"
[ $fail = 0 ]
