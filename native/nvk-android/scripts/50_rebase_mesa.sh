#!/bin/bash
# Step 5 (later): rebase the nvk-android NVK build onto a newer Mesa release tag (default mesa-26.3.0, the final release
# after 26.3.0-rc2; Mesa calendar: rc3 2026-10-28, rc4/final 2026-11-04).
#   50_rebase_mesa.sh [--check] [tag]
# Refuses (exit 3) when the tag does not exist on gitlab.freedesktop.org, and (exit 4) when it is younger than 14 days
# (project rule for third-party pins). --check stops after those two checks and the patch dry run (exit 5 = a patch
# needs a manual rebase). Otherwise it builds a NEW tree $W/src/<tag> (src/mesa is never touched), a matching host
# mesa_clc ($W/host-clc-<tag>), and the 8a profile into $W/b-nvk-8a-<tag> / $W/stage-8a-<tag>. Afterwards re-run the
# NV-02/NV-03 chroot checks (40_bionic_chroot_test.sh with W/STAGE pointing there) before replacing bin/.
# Heavy: run at nice 19 / ionice idle and only when the builder is idle (1-min load < 2, no gbiso job).
set -euo pipefail
CHECK=0
[ "${1:-}" = --check ] && { CHECK=1; shift; }
TAG=${1:-mesa-26.3.0}
. "$(dirname "$0")/00_env.sh"
URL=https://gitlab.freedesktop.org/mesa/mesa.git
CLONE=/build/mesa-android/mesa      # blobless partial clone: fetch/archive only, no log -S / blame
echo "=== tag $TAG"
REF=$(git ls-remote --tags "$URL" "refs/tags/$TAG" "refs/tags/$TAG^{}" | awk '{print $1, $2}')
if [ -z "$REF" ]; then
  echo "REFUSED: $TAG does not exist on $URL (latest 26.3 tags:" \
       "$(git ls-remote --tags "$URL" 'refs/tags/mesa-26.3*' | sed 's#.*refs/tags/##' | grep -v '\^{}' | sort -V | tail -3 | tr '\n' ' '))"
  exit 3
fi
echo "$REF"
git -C "$CLONE" fetch -q --filter=blob:none "$URL" "refs/tags/$TAG:refs/tags/$TAG"
TS=$(git -C "$CLONE" for-each-ref --format='%(taggerdate:unix)' "refs/tags/$TAG")
[ -n "$TS" ] || TS=$(git -C "$CLONE" log -1 --format=%ct "$TAG^{commit}")
AGE=$(( ($(date +%s) - TS) / 86400 ))
echo "tag date $(date -u -d "@$TS" +%F), age $AGE day(s)"
if [ "$AGE" -lt 14 ]; then
  echo "REFUSED: $TAG is $AGE day(s) old (< 14)"
  exit 4
fi
NEW=$W/src/$TAG
if [ ! -d "$NEW" ]; then
  mkdir -p "$NEW"
  git -C "$CLONE" archive "$TAG" | tar -x -C "$NEW"
fi
echo "=== patch dry run (set 8b = every patch)"
if ! python3 -I "$(dirname "$0")/apply_patches.py" --set 8b --check "$NEW" "$W/patches-$TAG"; then
  echo "A patch no longer applies to $TAG: rebase it in scripts/apply_patches.py (edit the old/new text), then re-run."
  exit 5
fi
[ "$CHECK" = 1 ] && { echo "check OK ($TAG applies cleanly)"; exit 0; }
export MESA_SRC=$NEW MESA_TAG=$TAG HOST_CLC=$W/host-clc-$TAG PATCHSET=8a B=$W/b-nvk-8a-$TAG STAGE=$W/stage-8a-$TAG
[ -x "$HOST_CLC/bin/mesa_clc" ] || nice -n 19 ionice -c3 bash "$(dirname "$0")/10_build_host_clc.sh"
nice -n 19 ionice -c3 bash "$(dirname "$0")/20_build_nvk_android.sh"
sha256sum "$STAGE"/*.so
echo "=== done: compare with bin/, run the NV-02/NV-03 checks, then update bin/ + INTEGRATION_NOTES"
