#!/bin/bash
# The automatic self-test's probe invocations (INTERFACES v1.2 25.3 step 6/7/9) against the real bionic nvk_probe in a
# read-only chroot of lapis 276.R4 (its own toybox `timeout`, the one gb_dgpu_report.sh gets as /system/bin/timeout),
# with the nouveau no-op drm-shim faking a TU117 (builder only, root via chroot_dir.sh). No GPU: both gate modes reach
# their first device call, then the gralloc allocation blocks (no allocator service in a chroot), which is exactly a
# probe stuck inside the stack - the case the -T watchdog, `timeout` and the stop paths must handle.
#   S1/S2  exact invocations `timeout 150 nvk_probe -t <mode> -T 60` -> exit 1 at the 60 s watchdog -> verdict
#          "fail <mode>"; nothing left running
#   S3     `timeout 5` -> 124 -> "fail <mode> timeout"; nothing left (lapis' toybox timeout puts the command in its
#          own process group and signals the group, so the v1.1 probe was already safe here)
#   S4     `timeout -s KILL 5` -> 137; nothing left
#   S5     the supervisor (`timeout`) itself SIGKILLed -> the probe gets SIGTERM (PDEATHSIG), kills its test child, exits
#   S6     SIGTERM to the probe's pid alone -> killed by SIGTERM (sh: 143), test child killed first
#   S9     exactly gb_dgpu_report.sh's st_probe()/st_interrupted(): `timeout 150 nvk_probe ... &`, then `kill $!`
#          (SIGTERM to the `timeout` pid) -> nothing left within 2 s
#   S10    INTERFACES 29 R-11: gb_dgpu_report.sh's st_interrupted() as of the review fix pass: `kill $!`, then wait
#          while `pgrep -x nvk_probe` finds one (at most 12 polls of 0.5 s) before the marker and lock are released;
#          the number of polls it needed and what is left
#   S5-v11 / S6-v11 / S9-v11 (optional v1.1 probe): the same with the v1.1 probe, which leaves processes running
#   S7/S8  no device (no drm-shim) -> 3 "error nodevice"; an explicit -d that does not exist -> 2 "error icd" (no
#          silent fallback to $NVK_ICD, which still names a valid ICD here)
# The ICD comes from $NVK_ICD (the first default of nvk_probe), so the argv of S1/S2 is exactly 25.3's.
# usage: selftest_invocation.sh <probe binary> <vulkan.nouveau.so> <drm-shim .so> [v1.1 probe for S5/S6-v11] [rootfs]
set -uo pipefail
PROBE=$(readlink -f "$1"); ICD=$(readlink -f "$2"); SHIM=$(readlink -f "$3")
OLD=${4:-}
ROOTFS=${5:-/build/native/dgpu/nvk-sync/rootfs-276R4}
CH=/build/native/dgpu/nvk-sync/chroot_dir.sh
R=$(mktemp -d /build/native/dgpu/nvk-sync/selftest.XXXXXX)
trap 'rm -rf "$R"' EXIT
cp "$PROBE" "$R/nvk_probe"
cp "$ICD" "$R/vulkan.nouveau.so"
cp "$SHIM" "$R/drmshim.so"
[ -n "$OLD" ] && cp "$OLD" "$R/nvk_probe_v11"
chmod 0755 "$R"
sha256sum "$R"/*
# prologue of every spec (runs inside the chroot under /system/bin/sh): toybox applets; left() = number of processes
# whose name (comm) is $1, i.e. probe parents plus their forked test children; verdict() = the 25.3 step 7 mapping
read -r -d '' P <<'EOF'
T=/system/bin/toybox; export NVK_ICD=/data/vulkan.nouveau.so LD_PRELOAD=/data/drmshim.so NOUVEAU_CHIPSET=167
left() { $T pgrep -x "$1" | $T wc -l; }
verdict() { case $1 in 0) echo pass;; 1) echo "fail $2";; 124|137) echo "fail $2 timeout";; 2) echo "error icd";; 3) echo "error nodevice";; *) echo "fail $2 rc $1";; esac; }
EOF
read -r -d '' S1 <<'EOF'
s=$($T date +%s); $T timeout 150 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s1.txt 2>&1; rc=$?; e=$($T date +%s)
echo "S1 rc=$rc after $((e-s)) s verdict='$(verdict $rc ahb-i915-readback)' left=$(left nvk_probe)"
$T grep -E 'TIMED OUT|summary|RESULT|using device' /data/s1.txt
EOF
read -r -d '' S2 <<'EOF'
s=$($T date +%s); $T timeout 150 /data/nvk_probe -t a17-swapchain-model -T 60 > /data/s2.txt 2>&1; rc=$?; e=$($T date +%s)
echo "S2 rc=$rc after $((e-s)) s verdict='$(verdict $rc a17-swapchain-model)' left=$(left nvk_probe)"
$T grep -E 'TIMED OUT|summary|RESULT|GrallocUsage2' /data/s2.txt
EOF
read -r -d '' S3 <<'EOF'
$T timeout 5 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s3.txt 2>&1; rc=$?; $T sleep 1
echo "S3 rc=$rc verdict='$(verdict $rc ahb-i915-readback)' left=$(left nvk_probe)"
$T grep -E 'INTERRUPTED|RESULT' /data/s3.txt
EOF
read -r -d '' S5V11 <<'EOF'
$T timeout 60 /data/nvk_probe_v11 -t ahb-i915-readback -T 60 > /data/s5o.txt 2>&1 &
tp=$!; $T sleep 4; echo "S5-v11 before: left=$(left nvk_probe_v11)"; kill -9 $tp; $T sleep 2
echo "S5-v11 supervisor SIGKILLed: left=$(left nvk_probe_v11) (v1.1 probe)"
$T pkill -9 -x nvk_probe_v11; $T sleep 1; echo "S5-v11 after pkill -9: left=$(left nvk_probe_v11)"
EOF
read -r -d '' S6V11 <<'EOF'
/data/nvk_probe_v11 -t ahb-i915-readback -T 60 > /data/s6o.txt 2>&1 &
p=$!; $T sleep 4; echo "S6-v11 before: left=$(left nvk_probe_v11)"; kill -TERM $p; wait $p; rc=$?; $T sleep 1
echo "S6-v11 rc=$rc left=$(left nvk_probe_v11) (v1.1 probe) ppid of what is left: $($T ps -o PPID= -p "$($T pgrep -x nvk_probe_v11 | $T head -1)" 2>/dev/null)"
$T pkill -9 -x nvk_probe_v11; $T sleep 1; echo "S6-v11 after pkill -9: left=$(left nvk_probe_v11)"
EOF
read -r -d '' S4 <<'EOF'
$T timeout -s KILL 5 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s4.txt 2>&1; rc=$?; $T sleep 1
echo "S4 rc=$rc verdict='$(verdict $rc ahb-i915-readback)' left=$(left nvk_probe)"
EOF
read -r -d '' S5 <<'EOF'
$T timeout 60 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s5.txt 2>&1 &
tp=$!; $T sleep 4; echo "S5 before: left=$(left nvk_probe)"; kill -9 $tp; $T sleep 2
echo "S5 supervisor SIGKILLed: left=$(left nvk_probe)"; $T grep -E 'INTERRUPTED|RESULT' /data/s5.txt
EOF
read -r -d '' S6 <<'EOF'
/data/nvk_probe -t ahb-i915-readback -T 60 > /data/s6.txt 2>&1 &
p=$!; $T sleep 4; echo "S6 before: left=$(left nvk_probe)"; kill -TERM $p; wait $p; rc=$?; $T sleep 1
echo "S6 rc=$rc left=$(left nvk_probe)"; $T grep -E 'INTERRUPTED|RESULT' /data/s6.txt
EOF
read -r -d '' S7 <<'EOF'
unset LD_PRELOAD; $T timeout 150 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s7.txt 2>&1; rc=$?
echo "S7 rc=$rc verdict='$(verdict $rc ahb-i915-readback)'"; $T grep RESULT /data/s7.txt
EOF
read -r -d '' S8 <<'EOF'
$T timeout 150 /data/nvk_probe -t ahb-i915-readback -T 60 -d /data/missing.so > /data/s8.txt 2>&1; rc=$?
echo "S8 rc=$rc verdict='$(verdict $rc ahb-i915-readback)'"; $T grep ERROR /data/s8.txt
EOF
read -r -d '' S9 <<'EOF'
$T timeout 150 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s9.txt 2>&1 &
PROBE_PID=$!; $T sleep 4; echo "S9 before: left=$(left nvk_probe)"; kill "$PROBE_PID"
for i in 1 2 3 4 5 6 7 8 9 10; do [ "$(left nvk_probe)" = 0 ] && break; $T usleep 200000; done
echo "S9 trap killed the timeout pid: left=$(left nvk_probe) after <= $((i * 200)) ms"; $T grep -E 'INTERRUPTED|RESULT' /data/s9.txt
EOF
read -r -d '' S10 <<'EOF'
$T timeout 150 /data/nvk_probe -t ahb-i915-readback -T 60 > /data/s10.txt 2>&1 &
PROBE_PID=$!; $T sleep 4; echo "S10 before: left=$(left nvk_probe)"; kill "$PROBE_PID"
_pw=0; while [ "$_pw" -lt 12 ] && $T pgrep -x nvk_probe > /dev/null 2>&1; do $T sleep 0.5; _pw=$((_pw + 1)); done
echo "S10 R-11 wait: polls=$_pw (max 12) left=$(left nvk_probe)"
EOF
read -r -d '' S9V11 <<'EOF'
$T timeout 150 /data/nvk_probe_v11 -t ahb-i915-readback -T 60 > /data/s9o.txt 2>&1 &
PROBE_PID=$!; $T sleep 4; echo "S9-v11 before: left=$(left nvk_probe_v11)"; kill "$PROBE_PID"; $T sleep 2
echo "S9-v11 trap killed the timeout pid: left=$(left nvk_probe_v11) (v1.1 probe)"
$T pkill -9 -x nvk_probe_v11; $T sleep 1; echo "S9-v11 after pkill -9: left=$(left nvk_probe_v11)"
EOF
SPECS=("$P"$'\n'"$S1" "$P"$'\n'"$S2" "$P"$'\n'"$S3" "$P"$'\n'"$S4" "$P"$'\n'"$S5" "$P"$'\n'"$S6" "$P"$'\n'"$S9" "$P"$'\n'"$S10")
[ -n "$OLD" ] && SPECS+=("$P"$'\n'"$S5V11" "$P"$'\n'"$S6V11" "$P"$'\n'"$S9V11")
SPECS+=("$P"$'\n'"$S7" "$P"$'\n'"$S8")
"$CH" "$ROOTFS" "$R" "${SPECS[@]}" 2>&1 | grep -vE 'linker: Warning|WARNING: linker'
