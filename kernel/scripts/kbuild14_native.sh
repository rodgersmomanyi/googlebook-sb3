#!/bin/bash
# Kernel build #14 (k14) = k13 + nouveau-only changes:
#   0001-0037  the linux-6.18.y (gregkh stable) commits v6.18.21..v6.18.57 that touch drivers/gpu/drm/nouveau,
#              minus the DRM-core "drm/client: Remove holds_console_lock parameter from suspend/resume"
#   0101-0103  local patches N1 (no ignore_hotplug with _PR3), N2 (presence guards), N3 (no power-off after a failed
#              runtime suspend)
# Nothing outside drivers/gpu/drm/nouveau/ changes: .config and vmlinux.symvers must equal k13's (k14_accept.sh).
#
# Source tree: /build/gki/common carries k13 as UNCOMMITTED changes on a detached HEAD. This script
#   1. saves that state to /build/k14/k13-state (HEAD, status, binary diff, untracked files + sha256),
#   2. records the k13 working tree as commit k13-native (temporary index: working tree and index untouched),
#   3. points HEAD at branch k14-nouveau (= k13-native), applies the patches with git am, builds,
#   4. ALWAYS restores the original detached HEAD + uncommitted k13 working tree and proves it equals the saved state.
# Output: /build/dist14 (Kleaf dist). /build/dist (k13) is never written.
# Usage (builder, as builder):  nohup bash /build/k14/kbuild14_native.sh > /dev/null 2>&1 &
#        bash /build/k14/kbuild14_native.sh --preflight   checks only (space, load, gbiso, patch set, K-01 on files)
#        bash /build/k14/kbuild14_native.sh --restore     only restore the k13 working tree (after an interrupted run)
# Log /build/kbuild14.log, markers /build/kbuild14.{done,failed}. Idempotent: a re-run rebuilds from scratch.
# Test hook (patches/k14/tools/test_kbuild14_state.sh): KB14_TEST_SRC=<fake repo> KB14_TEST_HOME=<dir> runs the same
# save/branch/am/restore logic against a throw-away repository; no Kleaf build, no resource or /build/dist checks.
set -u
MODE=${1:-build}
TEST=${KB14_TEST_SRC:+1}
K14=${KB14_TEST_HOME:-/build/k14}
SRC=${KB14_TEST_SRC:-/build/gki/common}
P=$K14/patches
S=$K14/k13-state
DIST=/build/dist14
LOGDIR=/build; [ -n "$TEST" ] && LOGDIR=$K14
export BUILD_NUMBER=16302714
BAZEL=(./tools/bazel --output_user_root=/build/.bazel)
NPATCH=${KB14_TEST_NPATCH:-40}
# fixed identity and dates: the k13-native and k14-nouveau commit ids (and so the kernel release) are reproducible
export GIT_AUTHOR_NAME="GB-build k14" GIT_AUTHOR_EMAIL=k14@builder.invalid
export GIT_COMMITTER_NAME="GB-build k14" GIT_COMMITTER_EMAIL=k14@builder.invalid
K13_DATE="2026-10-24T21:12:00+00:00"

if [ "$MODE" = build ]; then
  exec > "$LOGDIR/kbuild14.log" 2>&1 < /dev/null
  rm -f "$LOGDIR/kbuild14.done" "$LOGDIR/kbuild14.failed"
fi
echo "=== kbuild14 $MODE start $(date -Is)"
fail() { echo "FAILED: $*"; [ "$MODE" = build ] && touch "$LOGDIR/kbuild14.failed"; exit 1; }
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

same_state() {  # compare the live tree with the saved state; prints a verdict, rc 0 = identical
  local T; T=$(mktemp -d "$K14/state.XXXXXX")
  state_now "$T"
  local rc=0
  for f in head.txt head_ref.txt status.txt diff.sha256 untracked.txt untracked.sha256; do
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
  if [ "$cur" = k14-nouveau ] || [ "$cur" = k13-native ]; then
    [ -z "$(G status --porcelain=v1 --untracked-files=no)" ] || { echo "restore: tracked changes on $cur, refusing"; return 1; }
    G checkout -q k13-native || return 1           # clean -> clean switch; nouveau files back to k13
    if [ "$(cat "$S/head_ref.txt")" = DETACHED ]; then
      G update-ref --no-deref HEAD "$(cat "$S/head.txt")" || return 1
    else
      G symbolic-ref HEAD "$(cat "$S/head_ref.txt")" || return 1
    fi
    G reset -q --mixed || return 1                 # index := original HEAD tree; working tree untouched
  fi
  same_state
}

preflight() {
  if [ -z "$TEST" ]; then
    [ "$(id -un)" = builder ] || fail "run as builder"
    pgrep -f '/var/tmp/gbiso' > /dev/null && fail "an ISO build (/var/tmp/gbiso) is running; not starting"
    local free_b free_r
    free_b=$(df -BG --output=avail /build | tail -1 | tr -dc 0-9); free_r=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
    echo "free: /build ${free_b}G, / ${free_r}G; load $(cut -d' ' -f1-3 /proc/loadavg)"
    [ "$free_b" -ge 10 ] || fail "/build has ${free_b}G free, need >= 10G"
    [ "$free_r" -ge 5 ] || fail "/ has ${free_r}G free, need >= 5G"
    [ -e /build/dist/bzImage ] || fail "k13 /build/dist missing (needed for the comparisons)"
  fi
  (cd "$P" && sha256sum --quiet -c SHA256SUMS) || fail "patch set does not match $P/SHA256SUMS"
  local n; n=$(find "$P" -maxdepth 1 -name '*.patch' | wc -l)
  [ "$n" = "$NPATCH" ] || fail "expected $NPATCH patches in $P, found $n"
  # K-01 on the patch files: every path of every diff is under drivers/gpu/drm/nouveau/
  local bad
  bad=$(grep -h '^diff --git ' "$P"/*.patch | awk '{print substr($3,3); print substr($4,3)}' | grep -v '^drivers/gpu/drm/nouveau/' | sort -u)
  [ -z "$bad" ] || fail "patches touch files outside drivers/gpu/drm/nouveau/: $bad"
  echo "K-01 (patch files): $(grep -c '^diff --git ' "$P"/*.patch | awk -F: '{s+=$2} END {print s}') file diffs, all under drivers/gpu/drm/nouveau/"
}

case $MODE in
  --restore) restore_k13; exit $? ;;
  --preflight) preflight; echo "preflight OK"; exit 0 ;;
  build) ;;
  *) echo "usage: $0 [--preflight|--restore]"; exit 2 ;;
esac

preflight
w=0
while [ -z "$TEST" ] && [ "$(cut -d. -f1 /proc/loadavg)" -ge 2 ]; do      # spec 1.4: 1-min load < 2 before a heavy job
  [ $w -ge 120 ] && fail "load stayed >= 2 for 60 min"
  w=$((w+1)); sleep 30
done

# --- 1/2: save the k13 state once, record it as k13-native (no working-tree or index change)
cur=$(G symbolic-ref -q --short HEAD || echo DETACHED)
if [ "$cur" = k14-nouveau ] || [ "$cur" = k13-native ]; then
  echo "=== previous run left HEAD on $cur; restoring k13 first"
  restore_k13 || fail "could not restore the k13 state left by a previous run"
fi
if [ ! -f "$S/head.txt" ]; then
  state_now "$S"
  tar -C "$SRC" --null -T "$S/untracked.z" -cf "$S/untracked.tar"
  echo "saved k13 state: HEAD $(cat "$S/head.txt") ($(cat "$S/head_ref.txt")), $(wc -l < "$S/status.txt") status lines," \
       "$(wc -l < "$S/untracked.txt") untracked files, diff sha256 $(cat "$S/diff.sha256")"
else
  same_state || fail "working tree differs from the saved k13 state $S; investigate before building"
fi
if ! G rev-parse -q --verify refs/heads/k13-native > /dev/null; then
  IDX=$K14/k13.index; rm -f "$IDX"
  GIT_INDEX_FILE=$IDX G read-tree "$(cat "$S/head.txt")" || fail "read-tree"
  GIT_INDEX_FILE=$IDX G add -A || fail "add -A (temporary index)"
  TREE=$(GIT_INDEX_FILE=$IDX G write-tree) || fail "write-tree"
  C=$(GIT_AUTHOR_DATE=$K13_DATE GIT_COMMITTER_DATE=$K13_DATE G commit-tree "$TREE" -p "$(cat "$S/head.txt")" \
      -m "k13-native: Googlebook SB3 kernel build #13 working tree (kbuild13_native.sh), recorded for k14") || fail "commit-tree"
  G branch k13-native "$C" || fail "branch k13-native"
  rm -f "$IDX"
fi
echo "k13-native = $(G rev-parse --short=12 k13-native)"

# --- 3: k14-nouveau = k13-native + patches; HEAD moves without touching files (tree == working tree)
trap 'echo "=== restoring k13 working tree (trap)"; restore_k13' EXIT
G branch -f k14-nouveau k13-native || fail "branch k14-nouveau"
G symbolic-ref HEAD refs/heads/k14-nouveau || fail "symbolic-ref"
G reset -q --mixed || fail "reset --mixed"
# a clean status here proves that k13-native records exactly the live k13 working tree
[ -z "$(G status --porcelain=v1 --untracked-files=all)" ] || fail "tree not clean on k14-nouveau before git am"
G am -3 --committer-date-is-author-date "$P"/*.patch || { G am --abort; fail "git am"; }
n=$(G rev-list --count k13-native..k14-nouveau)
[ "$n" = "$NPATCH" ] || fail "expected $NPATCH new commits, got $n"
# K-01 on the commits
bad=0
for c in $(G rev-list --reverse k13-native..k14-nouveau); do
  o=$(G show --format= --name-only "$c" | grep -v '^drivers/gpu/drm/nouveau/' || true)
  if [ -n "$o" ]; then echo "K-01 FAIL $(G log -1 --format='%h %s' "$c"): $o"; bad=1; fi
done
[ $bad = 0 ] || fail "K-01"
echo "K-01 (commits): $n commits, $(G diff --name-only k13-native k14-nouveau | wc -l) files, all under drivers/gpu/drm/nouveau/"
G diff --quiet k13-native k14-nouveau -- arch/x86/configs/gki_defconfig BUILD.bazel || fail "defconfig/BUILD.bazel changed"
echo "k14-nouveau = $(G rev-parse --short=12 HEAD)"

# --- build into /build/dist14 (a stale dist14 from a previous k14 run is ours to replace)
if [ -n "$TEST" ]; then
  echo "TEST: no Kleaf build; tree at $(G rev-parse --short=12 HEAD)"; RC=${KB14_TEST_RC:-0}
else
  case "$DIST" in /build/dist14) rm -rf -- "$DIST" ;; *) fail "unexpected DIST $DIST" ;; esac
  cd /build/gki || fail "cd /build/gki"
  nice -n 19 ionice -c3 "${BAZEL[@]}" run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=$DIST
  RC=$?
  echo "=== build rc=$RC $(date -Is)"
  grep -q "savedefconfig does not match" "$LOGDIR/kbuild14.log" && echo "NOTE: savedefconfig mismatch (k14 must not change the config)"
fi

# --- 4: restore k13 before anything else can fail
trap - EXIT
if restore_k13; then echo "K-09: k13 working tree restored"; else echo "K-09 FAIL: k13 working tree NOT restored"; RC=97; fi

if [ "$RC" -eq 0 ] && [ -z "$TEST" ]; then
  KR=$(sed -n 's/^kernel_release=//p' $DIST/gki-info.txt)
  [ -n "$KR" ] || { echo "no kernel_release in gki-info.txt"; RC=98; }
  printf '%s\n' "$KR" > $DIST/kernel_release.txt
  K13R=$(sed -n 's/^kernel_release=//p' /build/dist/gki-info.txt)
  echo "k14 kernel release: $KR (k13: $K13R)"
  [ "$KR" != "$K13R" ] || { echo "kernel release equals k13's"; RC=98; }
  ls -la --time-style=+%m-%d_%H:%M $DIST/bzImage $DIST/vmlinux $DIST/nouveau.ko $DIST/drm_gpuvm.ko $DIST/gpu-sched.ko
  if cmp -s /build/dist/kernel_x86_64_dot_config $DIST/kernel_x86_64_dot_config; then echo "config: identical to k13"
  else echo "config: DIFFERS from k13"; RC=99; fi
  if cmp -s /build/dist/vmlinux.symvers $DIST/vmlinux.symvers; then echo "vmlinux.symvers: identical to k13"
  else echo "vmlinux.symvers: DIFFERS from k13"; diff /build/dist/vmlinux.symvers $DIST/vmlinux.symvers | head -20; RC=99; fi
fi
if [ "$RC" -eq 0 ]; then touch "$LOGDIR/kbuild14.done"; else touch "$LOGDIR/kbuild14.failed"; fi
echo "=== kbuild14 end rc=$RC $(date -Is)"
