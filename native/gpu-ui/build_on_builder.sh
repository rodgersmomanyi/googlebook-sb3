#!/bin/bash
# Windows (Git Bash) helper. Copies this component to builder:/build/native/dgpu/gpu-ui/src, plus read-only copies of
# C:\GB-build\native\dgpu\INTERFACES.md (spec/) and of the power component's gb_power.c + uapi/ (ext/power/, for the
# real-server tests), runs build_apk.sh and/or run_tests.sh there, and copies out/ back here.
#   ./build_on_builder.sh [all|build|test]      (default: all)
# Nothing outside the builder scratch dir is written on the builder (the signing key lives in ~/keys there).
set -euo pipefail
G=$(cd "$(dirname "$0")" && pwd)
KEY=${KEY:-C:/GB-build/builder_key}
HOST=${HOST:-builder@192.168.115.132}
SCRATCH=/build/native/dgpu/gpu-ui
what=${1:-all}
ssh_b() { ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" "$@"; }

# Builder etiquette (INTERFACES 1.4): report load, free space and any running ISO build before starting.
ssh_b "cat /proc/loadavg; df -BG / /build | tail -n 2; if pgrep -af '[g]biso'; then echo 'note: an ISO build is running'; fi"
(cd "$G" && tar --exclude=./out/test --exclude=./build -cf - .) |
  ssh_b "rm -rf $SCRATCH/src && mkdir -p $SCRATCH/src && tar -xf - -C $SCRATCH/src"
(cd "$G/../dgpu" && tar -cf - INTERFACES.md) | ssh_b "mkdir -p $SCRATCH/spec && tar -xf - -C $SCRATCH/spec"
(cd "$G/../power" && tar -cf - gb_power.c uapi) |
  ssh_b "rm -rf $SCRATCH/ext/power && mkdir -p $SCRATCH/ext/power && tar -xf - -C $SCRATCH/ext/power"
rc=0
case $what in
  all | build) ssh_b "cd $SCRATCH/src && bash ./build_apk.sh" || rc=$? ;;
esac
if [ "$rc" -eq 0 ]; then
  case $what in
    all | test) ssh_b "cd $SCRATCH/src && bash ./run_tests.sh" || rc=$? ;;
  esac
fi
ssh_b "cd $SCRATCH/src && tar -cf - out" | (cd "$G" && tar -xf -)
echo "out/ copied back to $G/out (exit $rc)"
exit "$rc"
