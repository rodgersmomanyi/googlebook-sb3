#!/bin/sh
# run_tests.sh -- runs every nouveau-policy check on the builder (Ubuntu with mksh + shellcheck):
#   NP-01 / NP-1B-24 shellcheck -s sh on every .sh, NP-24 / NP-1B-16 rc lint, the mksh suite (NP-02..NP-30,
#   dmesg-stream, and the v1.2 cases NP-1B-01..NP-1B-23 + NP-1B-VM). GB_INTERFACES = INTERFACES.md v1.2.
# Usage (builder): sh test/run_tests.sh [work_dir]     (default /build/native/dgpu/nouveau-policy/work)
# From Windows: see ../README.md ("Build and test"). Heavy-job etiquette: runs at nice 19 / idle I/O class.
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-/build/native/dgpu/nouveau-policy/work}
[ -n "${GB_NICE:-}" ] || { GB_NICE=1; export GB_NICE; exec nice -n 19 ionice -c3 sh "$0" "$@"; }
# shellcheck disable=SC2016  # $KSH_VERSION is expanded by mksh, not here
echo "== mksh: $(mksh -c 'echo "$KSH_VERSION"')"
echo "== shellcheck: $(shellcheck --version | sed -n 's/^version: //p')"
echo "== NP-01 shellcheck -s sh"
shellcheck -s sh "$HERE"/../native/*.sh "$HERE"/*.sh
r1=$?
echo "shellcheck exit=$r1"
echo "== NP-24 rc lint"
sh "$HERE/rc_lint.sh" "$HERE/../native/gb_nouveau.rc"
r2=$?
echo "== mksh suite (GNU coreutils)"
mksh "$HERE/test_gb_nouveau.sh" "$WORK/gnu"
r3=$?
# Android runs toybox + the BWK ("one true") awk, not GNU tools: run the suite again with those first in PATH.
# Ubuntu's toybox 0.8.9 has no tr applet (Android's 0.8.13 has); busybox tr stands in.
r4=skipped
if command -v toybox > /dev/null && command -v original-awk > /dev/null && command -v busybox > /dev/null; then
  TB=$WORK/toybox-bin
  rm -rf "$TB"
  mkdir -p "$TB"
  for a in cat cut sed grep sort ls readlink stat sha256sum wc head tail mkdir rmdir rm mv cp ln date chmod \
    basename dirname printf timeout seq find id; do
    ln -s "$(command -v toybox)" "$TB/$a"
  done
  ln -s "$(command -v busybox)" "$TB/tr"
  ln -s "$(command -v original-awk)" "$TB/awk"
  echo "== mksh suite (toybox $(toybox --version | cut -d' ' -f2) + $(original-awk --version) + busybox tr)"
  GB_TEST_TOOLS=$TB mksh "$HERE/test_gb_nouveau.sh" "$WORK/toybox"
  r4=$?
fi
echo "== summary shellcheck=$r1 rc_lint=$r2 suite_gnu=$r3 suite_toybox=$r4"
[ $r1 = 0 ] && [ $r2 = 0 ] && [ $r3 = 0 ] && { [ "$r4" = 0 ] || [ "$r4" = skipped ]; }
