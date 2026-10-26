#!/bin/bash
# Kernel build #15 (k15) = k14 + two committed patches (wave 3, WAVE3_PLAN.md 3.1-3.3):
#   0110  x86/traps: GB_VNNI_EMULATION v4 (IRQs on, SSE/YMM/ZMM_Hi256 init state, MAXVL, insn_fetch_from_user,
#         /sys/kernel/gb_vnni counter); touches only arch/x86/kernel/traps.c + the new arch/x86/kernel/gb_vnni_core.h
#   0111  drm/i915/display: optional gb_ignore_user_gamma (default 0); touches only drivers/gpu/drm/i915/display/
# The 40 k14 nouveau patches are not re-applied: k15-nouveau branches from the existing k14-nouveau (k13-native +
# the 40 with fixed identity and dates, so its id is reproducible) [W3R-13]. .config and vmlinux.symvers must equal
# k14's (k15_accept.sh K-02/K-03).
#
# Source tree: /build/gki/common carries k13 as UNCOMMITTED changes on a detached HEAD (recorded as k13-native by
# kbuild14_native.sh). This script
#   1. saves that state to /build/k15/k13-state (HEAD, status, binary diff, untracked files + sha256) and compares it
#      with k14's saved state,
#   2. points HEAD at k13-native (index only; a clean status proves k13-native == the working tree), switches to
#      branch k15-nouveau = k14-nouveau (a clean checkout: the nouveau files become k14's) and applies 0110/0111 with
#      git am,
#   3. builds into /build/dist15,
#   4. ALWAYS restores the original detached HEAD + uncommitted k13 working tree and proves it equals the saved state.
# /build/dist (k13) and /build/dist14 (k14) are never written.
# Usage (builder, as builder):  nohup bash /build/k15/kbuild15_native.sh > /dev/null 2>&1 &
#        bash /build/k15/kbuild15_native.sh --preflight   checks only (space, load, gbiso, patch set, K-01 on files)
#        bash /build/k15/kbuild15_native.sh --restore     only restore the k13 working tree (after an interrupted run)
# Log /build/kbuild15.log, markers /build/kbuild15.{done,failed}. Idempotent: a re-run rebuilds.
# Test hook (patches/k15/tools/test_kbuild15_state.sh): KB15_TEST_SRC=<fake repo> KB15_TEST_HOME=<dir> runs the same
# save/branch/am/restore logic against a throw-away repository; no Kleaf build, no resource or /build/dist checks.
set -u
MODE=${1:-build}
TEST=${KB15_TEST_SRC:+1}
K15=${KB15_TEST_HOME:-/build/k15}
SRC=${KB15_TEST_SRC:-/build/gki/common}
P=$K15/patches
S=$K15/k13-state
S14=${KB15_K14_STATE:-/build/k14/k13-state}
DIST=/build/dist15
LOGDIR=/build; [ -n "$TEST" ] && LOGDIR=$K15
export BUILD_NUMBER=16302715
BAZEL=(./tools/bazel --output_user_root=/build/.bazel)
NPATCH_NOUVEAU=${KB15_TEST_NNOUVEAU:-40}
NPATCH_K15=2
K14_ID=${KB15_K14_ID-9629ced3e50e}                 # k14-nouveau as built (K14.md); empty = do not check
# fixed identity: git am --committer-date-is-author-date then makes the k15-nouveau ids (and the release) reproducible
export GIT_AUTHOR_NAME="GB-build k15" GIT_AUTHOR_EMAIL=k15@builder.invalid
export GIT_COMMITTER_NAME="GB-build k15" GIT_COMMITTER_EMAIL=k15@builder.invalid

if [ "$MODE" = build ]; then
  exec > "$LOGDIR/kbuild15.log" 2>&1 < /dev/null
  rm -f "$LOGDIR/kbuild15.done" "$LOGDIR/kbuild15.failed"
fi
echo "=== kbuild15 $MODE start $(date -Is)"
fail() { echo "FAILED: $*"; [ "$MODE" = build ] && touch "$LOGDIR/kbuild15.failed"; exit 1; }
G() { git -C "$SRC" "$@"; }

state_now() {  # <dir>: write the comparable k13 working-tree state
  mkdir -p "$1"
  G rev-parse HEAD > "$1/head.txt"
  { G symbolic-ref -q HEAD || echo DETACHED; } > "$1/head_ref.txt"
  G status --porcelain=v1 --untracked-files=all > "$1/status.txt"
  G diff --binary HEAD > "$1/diff.patch"
  sha256sum "$1/diff.patch" | cut -d' ' -f1 > "$1/diff.sha256"
  G ls-files --others --exclude-standard -z > "$1/untracked.z"
  tr '\0' '\n' < "$1/untracked.z" > "$1/untracked.txt"
  (cd "$SRC" && xargs -0 -r sha256sum < "$1/untracked.z") > "$1/untracked.sha256"
}

STATE_FILES="head.txt head_ref.txt status.txt diff.sha256 untracked.txt untracked.sha256"
same_state() {  # compare the live tree with the saved state; prints a verdict, rc 0 = identical
  local T; T=$(mktemp -d "$K15/state.XXXXXX")
  state_now "$T"
  local rc=0
  for f in $STATE_FILES; do
    if ! cmp -s "$S/$f" "$T/$f"; then echo "k13-state differs: $f"; diff "$S/$f" "$T/$f" | head -20; rc=1; fi
  done
  rm -rf "$T"
  [ $rc = 0 ] && echo "k13-state: identical to saved state (HEAD $(cat "$S/head.txt"), $(wc -l < "$S/status.txt") status lines)"
  return $rc
}

restore_k13() {  # back to the original detached HEAD (or branch) with the uncommitted k13 working tree
  [ -f "$S/head.txt" ] || { echo "restore: no saved k13 state, nothing to do"; return 0; }
  local cur; cur=$(G symbolic-ref -q --short HEAD || echo DETACHED)
  if [ -d "$(G rev-parse --absolute-git-dir)/rebase-apply" ]; then G am --abort || return 1; fi
  case "$cur" in
    k15-nouveau|k14-nouveau|k13-native)
      [ -z "$(G status --porcelain=v1 --untracked-files=no)" ] || { echo "restore: tracked changes on $cur, refusing"; return 1; }
      G checkout -q k13-native || return 1           # clean -> clean switch; traps.c, i915, nouveau back to k13
      if [ "$(cat "$S/head_ref.txt")" = DETACHED ]; then
        G update-ref --no-deref HEAD "$(cat "$S/head.txt")" || return 1
      else
        G symbolic-ref HEAD "$(cat "$S/head_ref.txt")" || return 1
      fi
      G reset -q --mixed || return 1                 # index := original HEAD tree; working tree untouched
      ;;
  esac
  same_state
}

scope_bad() {  # <regex of allowed paths> <patch files...>: print the paths outside the allowed set
  local re=$1; shift
  grep -h '^diff --git ' "$@" | awk '{print substr($3,3); print substr($4,3)}' | grep -Ev "$re" | sort -u
}
RE_0110='^arch/x86/kernel/(traps\.c|gb_vnni_core\.h)$'
RE_0111='^drivers/gpu/drm/i915/display/'

preflight() {
  if [ -z "$TEST" ]; then
    [ "$(id -un)" = builder ] || fail "run as builder"
    pgrep -f '/var/tmp/gbiso' > /dev/null && fail "an ISO build (/var/tmp/gbiso) is running; not starting"
    local free_b free_r
    free_b=$(df -BG --output=avail /build | tail -1 | tr -dc 0-9); free_r=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
    echo "free: /build ${free_b}G, / ${free_r}G; load $(cut -d' ' -f1-3 /proc/loadavg)"
    df -h /build / | sed 's/^/df: /'
    [ "$free_b" -ge 10 ] || fail "/build has ${free_b}G free, need >= 10G"
    [ "$free_r" -ge 5 ] || fail "/ has ${free_r}G free, need >= 5G"
    [ -e /build/dist/bzImage ] || fail "k13 /build/dist missing"
    [ -e /build/dist14/bzImage ] || fail "k14 /build/dist14 missing (needed for the comparisons)"
  fi
  (cd "$P" && sha256sum --quiet -c SHA256SUMS) || fail "patch set does not match $P/SHA256SUMS"
  local n; n=$(find "$P" -maxdepth 1 -name '*.patch' | wc -l)
  [ "$n" = "$NPATCH_K15" ] || fail "expected $NPATCH_K15 patches in $P, found $n"
  local f
  for f in 0110 0111; do compgen -G "$P/$f-*.patch" > /dev/null || fail "$f missing in $P"; done
  # K-01 on the patch files
  local bad
  bad=$(scope_bad "$RE_0110" "$P"/0110-*.patch); [ -z "$bad" ] || fail "0110 touches files outside traps.c/gb_vnni_core.h: $bad"
  bad=$(scope_bad "$RE_0111" "$P"/0111-*.patch); [ -z "$bad" ] || fail "0111 touches files outside drivers/gpu/drm/i915/display/: $bad"
  echo "K-01 (patch files): 0110 $(grep -c '^diff --git ' "$P"/0110-*.patch) file diffs in arch/x86/kernel/{traps.c,gb_vnni_core.h}," \
       "0111 $(grep -c '^diff --git ' "$P"/0111-*.patch) file diffs in drivers/gpu/drm/i915/display/"
  # the k14 base: k14-nouveau = k13-native + NPATCH_NOUVEAU commits, all inside drivers/gpu/drm/nouveau/
  G rev-parse -q --verify refs/heads/k13-native > /dev/null || fail "branch k13-native missing (kbuild14 records it)"
  G rev-parse -q --verify refs/heads/k14-nouveau > /dev/null || fail "branch k14-nouveau missing (kbuild14 builds it)"
  if [ -n "$K14_ID" ]; then
    [ "$(G rev-parse --short=12 k14-nouveau)" = "$K14_ID" ] || fail "k14-nouveau is $(G rev-parse --short=12 k14-nouveau), expected $K14_ID"
  fi
  n=$(G rev-list --count k13-native..k14-nouveau)
  [ "$n" = "$NPATCH_NOUVEAU" ] || fail "k14-nouveau has $n commits over k13-native, expected $NPATCH_NOUVEAU"
  bad=$(G diff --name-only k13-native k14-nouveau | grep -v '^drivers/gpu/drm/nouveau/' || true)
  [ -z "$bad" ] || fail "k14-nouveau touches files outside drivers/gpu/drm/nouveau/: $bad"
  echo "k14 base: k14-nouveau $(G rev-parse --short=12 k14-nouveau) = k13-native $(G rev-parse --short=12 k13-native) + $n nouveau commits"
}

case $MODE in
  --restore) restore_k13; exit $? ;;
  --preflight) preflight; echo "preflight OK"; exit 0 ;;
  build) ;;
  *) echo "usage: $0 [--preflight|--restore]"; exit 2 ;;
esac

cur=$(G symbolic-ref -q --short HEAD || echo DETACHED)
case "$cur" in
  k15-nouveau|k14-nouveau|k13-native)
    echo "=== previous run left HEAD on $cur; restoring k13 first"
    restore_k13 || fail "could not restore the k13 state left by a previous run" ;;
esac
preflight
w=0
while [ -z "$TEST" ] && [ "$(cut -d. -f1 /proc/loadavg)" -ge 2 ]; do      # spec 1.4: 1-min load < 2 before a heavy job
  [ $w -ge 120 ] && fail "load stayed >= 2 for 60 min"
  w=$((w+1)); sleep 30
done

# --- 1: save the k13 state once (no working-tree or index change); it must equal k14's saved state
if [ ! -f "$S/head.txt" ]; then
  state_now "$S"
  tar -C "$SRC" --null -T "$S/untracked.z" -cf "$S/untracked.tar"
  echo "saved k13 state: HEAD $(cat "$S/head.txt") ($(cat "$S/head_ref.txt")), $(wc -l < "$S/status.txt") status lines," \
       "$(wc -l < "$S/untracked.txt") untracked files, diff sha256 $(cat "$S/diff.sha256")"
else
  same_state || fail "working tree differs from the saved k13 state $S; investigate before building"
fi
if [ -f "$S14/head.txt" ]; then
  d=0; for f in $STATE_FILES; do cmp -s "$S/$f" "$S14/$f" || { echo "k13-state differs from k14's: $f"; d=1; }; done
  if [ $d = 0 ]; then echo "k13-state: identical to k14's saved state ($S14)"; else fail "k13 state differs from k14's ($S14)"; fi
fi

# --- 2: k15-nouveau = k14-nouveau + 0110 + 0111
trap 'echo "=== restoring k13 working tree (trap)"; restore_k13' EXIT
G symbolic-ref HEAD refs/heads/k13-native || fail "symbolic-ref k13-native"
G reset -q --mixed || fail "reset --mixed"
# a clean status here proves that k13-native records exactly the live k13 working tree
[ -z "$(G status --porcelain=v1 --untracked-files=all)" ] || fail "tree not clean on k13-native"
G checkout -q -B k15-nouveau k14-nouveau || fail "checkout k15-nouveau"
[ -z "$(G status --porcelain=v1 --untracked-files=all)" ] || fail "tree not clean on k15-nouveau before git am"
G am -3 --committer-date-is-author-date "$P"/0110-*.patch "$P"/0111-*.patch || { G am --abort; fail "git am"; }
n=$(G rev-list --count k14-nouveau..k15-nouveau)
[ "$n" = "$NPATCH_K15" ] || fail "expected $NPATCH_K15 new commits, got $n"
# K-01 on the commits
c110=$(G rev-list --reverse k14-nouveau..k15-nouveau | sed -n 1p); c111=$(G rev-list --reverse k14-nouveau..k15-nouveau | sed -n 2p)
o=$(G show --format= --name-only "$c110" | grep -Ev "$RE_0110" || true); [ -z "$o" ] || fail "K-01: 0110 commit touches $o"
o=$(G show --format= --name-only "$c111" | grep -Ev "$RE_0111" || true); [ -z "$o" ] || fail "K-01: 0111 commit touches $o"
echo "K-01 (commits): 0110 $(G log -1 --format='%h %s' "$c110") [$(G show --format= --name-only "$c110" | tr '\n' ' ')]"
echo "K-01 (commits): 0111 $(G log -1 --format='%h %s' "$c111") [$(G show --format= --name-only "$c111" | wc -l) files]"
# K-07 gate (source level): nouveau identical to k14
[ -z "$(G diff --name-only k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau)" ] || fail "K-07: nouveau differs from k14-nouveau"
echo "K-07 (source): git diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau is empty"
G diff --quiet k14-nouveau k15-nouveau -- arch/x86/configs/gki_defconfig BUILD.bazel || fail "defconfig/BUILD.bazel changed"
echo "k15-nouveau = $(G rev-parse --short=12 HEAD)"

# --- 3: build into /build/dist15 (a stale dist15 from a previous k15 run is ours to replace)
if [ -n "$TEST" ]; then
  echo "TEST: no Kleaf build; tree at $(G rev-parse --short=12 HEAD)"; RC=${KB15_TEST_RC:-0}
else
  case "$DIST" in /build/dist15) rm -rf -- "$DIST" ;; *) fail "unexpected DIST $DIST" ;; esac
  cd /build/gki || fail "cd /build/gki"
  nice -n 19 ionice -c3 "${BAZEL[@]}" run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=$DIST
  RC=$?
  echo "=== build rc=$RC $(date -Is)"
  grep -q "savedefconfig does not match" "$LOGDIR/kbuild15.log" && echo "NOTE: savedefconfig mismatch (k15 must not change the config)"
fi

# --- 4: restore k13 before anything else can fail
trap - EXIT
if restore_k13; then echo "K-09: k13 working tree restored"; else echo "K-09 FAIL: k13 working tree NOT restored"; RC=97; fi

if [ "$RC" -eq 0 ] && [ -z "$TEST" ]; then
  KR=$(sed -n 's/^kernel_release=//p' $DIST/gki-info.txt)
  [ -n "$KR" ] || { echo "no kernel_release in gki-info.txt"; RC=98; }
  printf '%s\n' "$KR" > $DIST/kernel_release.txt
  K14R=$(cat /build/dist14/kernel_release.txt)
  echo "k15 kernel release: $KR (k14: $K14R)"
  [ "$KR" != "$K14R" ] || { echo "kernel release equals k14's"; RC=98; }
  ls -la --time-style=+%m-%d_%H:%M $DIST/bzImage $DIST/vmlinux $DIST/i915.ko $DIST/nouveau.ko
  if cmp -s /build/dist14/kernel_x86_64_dot_config $DIST/kernel_x86_64_dot_config; then echo "config: identical to k14"
  else echo "config: DIFFERS from k14"; RC=99; fi
  if cmp -s /build/dist14/vmlinux.symvers $DIST/vmlinux.symvers; then echo "vmlinux.symvers: identical to k14"
  else echo "vmlinux.symvers: DIFFERS from k14"; diff /build/dist14/vmlinux.symvers $DIST/vmlinux.symvers | head -20; RC=99; fi
  df -h /build / | sed 's/^/df after: /'
fi
if [ "$RC" -eq 0 ]; then touch "$LOGDIR/kbuild15.done"; else touch "$LOGDIR/kbuild15.failed"; fi
echo "=== kbuild15 end rc=$RC $(date -Is)"
