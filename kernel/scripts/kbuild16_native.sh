#!/bin/bash
# Kernel build #16 (k16) = k15 + two committed patches (wave 8; backlog LOW-3 and C-SD):
#   0112  x86/traps: gb_vnni: escape the task name in the summary and kmsg (LOW-3, backlog\low3\); touches only
#         arch/x86/kernel/traps.c
#   0113  gki_defconfig, BUILD.bazel: the Realtek card-reader drivers as modules (MISC_RTSX_PCI/USB, MMC_REALTEK_PCI/USB
#         =m; rtsx_pci, rtsx_usb, rtsx_pci_sdmmc, rtsx_usb_sdmmc); touches only arch/x86/configs/gki_defconfig and
#         BUILD.bazel
# The k15 commits are not re-applied: k16-nouveau branches from the existing k15-nouveau (k14-nouveau + 0110 + 0111
# with fixed identity and dates, so its id is reproducible), the same base commit, patch series and config as k15.
# .config may differ from k15's only by the five card-reader lines and vmlinux.symvers only by added exports
# (k16_accept.sh K-02/K-03; the KMI symbol list itself is unchanged).
#
# Source tree: /build/gki/common carries k13 as UNCOMMITTED changes on a detached HEAD (recorded as k13-native by
# kbuild14_native.sh). This script
#   1. saves that state to /build/k16/k13-state (HEAD, status, binary diff, untracked files + sha256) and compares it
#      with k15's saved state,
#   2. points HEAD at k13-native (index only; a clean status proves k13-native == the working tree), switches to
#      branch k16-nouveau = k15-nouveau (a clean checkout: the nouveau files become k14's, traps.c/i915 k15's) and
#      applies 0112/0113 with git am,
#   3. builds into /build/dist16,
#   4. ALWAYS restores the original detached HEAD + uncommitted k13 working tree and proves it equals the saved state.
# /build/dist (k13), /build/dist14 (k14) and /build/dist15 (k15) are never written.
# Usage (builder, as builder):  nohup bash /build/k16/kbuild16_native.sh > /dev/null 2>&1 &
#        bash /build/k16/kbuild16_native.sh --preflight   checks only (space, load, gbiso, patch set, K-01 on files)
#        bash /build/k16/kbuild16_native.sh --restore     only restore the k13 working tree (after an interrupted run)
# Log /build/kbuild16.log, markers /build/kbuild16.{done,failed}. Idempotent: a re-run rebuilds.
# Test hook (w8prep\kernel-k16\tests\test_kbuild16_state.sh): KB16_TEST_SRC=<fake repo> KB16_TEST_HOME=<dir> runs the
# same save/branch/am/restore logic against a throw-away repository; no Kleaf build, no resource or /build/dist checks.
set -u
MODE=${1:-build}
TEST=${KB16_TEST_SRC:+1}
K16=${KB16_TEST_HOME:-/build/k16}
SRC=${KB16_TEST_SRC:-/build/gki/common}
P=$K16/patches
S=$K16/k13-state
S15=${KB16_K15_STATE:-/build/k15/k13-state}
DIST=/build/dist16
LOGDIR=/build; [ -n "$TEST" ] && LOGDIR=$K16
export BUILD_NUMBER=16302716
BAZEL=(./tools/bazel --output_user_root=/build/.bazel)
NPATCH_K15=${KB16_TEST_NK15:-2}
NPATCH_K16=2
K15_ID=${KB16_K15_ID-3c2c2f588d3f}                 # k15-nouveau as built (K15.md); empty = do not check
K14_ID=${KB16_K14_ID-9629ced3e50e}                 # k14-nouveau as built (K14.md); empty = do not check
# fixed identity: git am --committer-date-is-author-date then makes the k16-nouveau ids (and the release) reproducible
export GIT_AUTHOR_NAME="GB-build k16" GIT_AUTHOR_EMAIL=k16@builder.invalid
export GIT_COMMITTER_NAME="GB-build k16" GIT_COMMITTER_EMAIL=k16@builder.invalid

if [ "$MODE" = build ]; then
  exec > "$LOGDIR/kbuild16.log" 2>&1 < /dev/null
  rm -f "$LOGDIR/kbuild16.done" "$LOGDIR/kbuild16.failed"
fi
echo "=== kbuild16 $MODE start $(date -Is)"
fail() { echo "FAILED: $*"; [ "$MODE" = build ] && touch "$LOGDIR/kbuild16.failed"; exit 1; }
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
  local T; T=$(mktemp -d "$K16/state.XXXXXX")
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
    k16-nouveau|k15-nouveau|k14-nouveau|k13-native)
      [ -z "$(G status --porcelain=v1 --untracked-files=no)" ] || { echo "restore: tracked changes on $cur, refusing"; return 1; }
      G checkout -q k13-native || return 1           # clean -> clean switch; traps.c, i915, nouveau, config back to k13
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
RE_0112='^arch/x86/kernel/traps\.c$'
RE_0113='^(arch/x86/configs/gki_defconfig|BUILD\.bazel)$'

preflight() {
  if [ -z "$TEST" ]; then
    [ "$(id -un)" = builder ] || fail "run as builder"
    pgrep -f '/var/tmp/gbiso' > /dev/null && fail "an ISO build (/var/tmp/gbiso) is running; not starting"
    local free_b free_r
    free_b=$(df -BG --output=avail /build | tail -1 | tr -dc 0-9); free_r=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
    echo "free: /build ${free_b}G, / ${free_r}G; load $(cut -d' ' -f1-3 /proc/loadavg)"
    df -h /build / | sed 's/^/df: /'
    # k15 used 1 GB of /build (dist15 0.84 GB + the incremental cache); floors: /build >= 1 GB and / >= 4 GB after it
    [ "$free_b" -ge 4 ] || fail "/build has ${free_b}G free, need >= 4G"
    [ "$free_r" -ge 8 ] || fail "/ has ${free_r}G free, need >= 8G"
    [ -e /build/dist/bzImage ] || fail "k13 /build/dist missing"
    [ -e /build/dist14/bzImage ] || fail "k14 /build/dist14 missing"
    [ -e /build/dist15/bzImage ] || fail "k15 /build/dist15 missing (needed for the comparisons)"
  fi
  (cd "$P" && sha256sum --quiet -c SHA256SUMS) || fail "patch set does not match $P/SHA256SUMS"
  local n; n=$(find "$P" -maxdepth 1 -name '*.patch' | wc -l)
  [ "$n" = "$NPATCH_K16" ] || fail "expected $NPATCH_K16 patches in $P, found $n"
  local f
  for f in 0112 0113; do compgen -G "$P/$f-*.patch" > /dev/null || fail "$f missing in $P"; done
  # K-01 on the patch files
  local bad
  bad=$(scope_bad "$RE_0112" "$P"/0112-*.patch); [ -z "$bad" ] || fail "0112 touches files outside traps.c: $bad"
  bad=$(scope_bad "$RE_0113" "$P"/0113-*.patch); [ -z "$bad" ] || fail "0113 touches files outside gki_defconfig/BUILD.bazel: $bad"
  echo "K-01 (patch files): 0112 $(grep -c '^diff --git ' "$P"/0112-*.patch) file diff in arch/x86/kernel/traps.c," \
       "0113 $(grep -c '^diff --git ' "$P"/0113-*.patch) file diffs in arch/x86/configs/gki_defconfig + BUILD.bazel"
  # the k15 base: k15-nouveau = k14-nouveau + NPATCH_K15 commits (0110, 0111), as built
  G rev-parse -q --verify refs/heads/k13-native > /dev/null || fail "branch k13-native missing (kbuild14 records it)"
  G rev-parse -q --verify refs/heads/k14-nouveau > /dev/null || fail "branch k14-nouveau missing (kbuild14 builds it)"
  G rev-parse -q --verify refs/heads/k15-nouveau > /dev/null || fail "branch k15-nouveau missing (kbuild15 builds it)"
  if [ -n "$K14_ID" ]; then
    [ "$(G rev-parse --short=12 k14-nouveau)" = "$K14_ID" ] || fail "k14-nouveau is $(G rev-parse --short=12 k14-nouveau), expected $K14_ID"
  fi
  if [ -n "$K15_ID" ]; then
    [ "$(G rev-parse --short=12 k15-nouveau)" = "$K15_ID" ] || fail "k15-nouveau is $(G rev-parse --short=12 k15-nouveau), expected $K15_ID"
  fi
  n=$(G rev-list --count k14-nouveau..k15-nouveau)
  [ "$n" = "$NPATCH_K15" ] || fail "k15-nouveau has $n commits over k14-nouveau, expected $NPATCH_K15"
  echo "k15 base: k15-nouveau $(G rev-parse --short=12 k15-nouveau) = k14-nouveau $(G rev-parse --short=12 k14-nouveau) + $n commits"
}

case $MODE in
  --restore) restore_k13; exit $? ;;
  --preflight) preflight; echo "preflight OK"; exit 0 ;;
  build) ;;
  *) echo "usage: $0 [--preflight|--restore]"; exit 2 ;;
esac

cur=$(G symbolic-ref -q --short HEAD || echo DETACHED)
case "$cur" in
  k16-nouveau|k15-nouveau|k14-nouveau|k13-native)
    echo "=== previous run left HEAD on $cur; restoring k13 first"
    restore_k13 || fail "could not restore the k13 state left by a previous run" ;;
esac
preflight
w=0
while [ -z "$TEST" ] && [ "$(cut -d. -f1 /proc/loadavg)" -ge 2 ]; do      # spec 1.4: 1-min load < 2 before a heavy job
  [ $w -ge 120 ] && fail "load stayed >= 2 for 60 min"
  w=$((w+1)); sleep 30
done

# --- 1: save the k13 state once (no working-tree or index change); it must equal k15's saved state
if [ ! -f "$S/head.txt" ]; then
  state_now "$S"
  tar -C "$SRC" --null -T "$S/untracked.z" -cf "$S/untracked.tar"
  echo "saved k13 state: HEAD $(cat "$S/head.txt") ($(cat "$S/head_ref.txt")), $(wc -l < "$S/status.txt") status lines," \
       "$(wc -l < "$S/untracked.txt") untracked files, diff sha256 $(cat "$S/diff.sha256")"
else
  same_state || fail "working tree differs from the saved k13 state $S; investigate before building"
fi
if [ -f "$S15/head.txt" ]; then
  d=0; for f in $STATE_FILES; do cmp -s "$S/$f" "$S15/$f" || { echo "k13-state differs from k15's: $f"; d=1; }; done
  if [ $d = 0 ]; then echo "k13-state: identical to k15's saved state ($S15)"; else fail "k13 state differs from k15's ($S15)"; fi
fi

# --- 2: k16-nouveau = k15-nouveau + 0112 + 0113
trap 'echo "=== restoring k13 working tree (trap)"; restore_k13' EXIT
G symbolic-ref HEAD refs/heads/k13-native || fail "symbolic-ref k13-native"
G reset -q --mixed || fail "reset --mixed"
# a clean status here proves that k13-native records exactly the live k13 working tree
[ -z "$(G status --porcelain=v1 --untracked-files=all)" ] || fail "tree not clean on k13-native"
G checkout -q -B k16-nouveau k15-nouveau || fail "checkout k16-nouveau"
[ -z "$(G status --porcelain=v1 --untracked-files=all)" ] || fail "tree not clean on k16-nouveau before git am"
G am -3 --committer-date-is-author-date "$P"/0112-*.patch "$P"/0113-*.patch || { G am --abort; fail "git am"; }
n=$(G rev-list --count k15-nouveau..k16-nouveau)
[ "$n" = "$NPATCH_K16" ] || fail "expected $NPATCH_K16 new commits, got $n"
# K-01 on the commits
c112=$(G rev-list --reverse k15-nouveau..k16-nouveau | sed -n 1p); c113=$(G rev-list --reverse k15-nouveau..k16-nouveau | sed -n 2p)
o=$(G show --format= --name-only "$c112" | grep -Ev "$RE_0112" || true); [ -z "$o" ] || fail "K-01: 0112 commit touches $o"
o=$(G show --format= --name-only "$c113" | grep -Ev "$RE_0113" || true); [ -z "$o" ] || fail "K-01: 0113 commit touches $o"
echo "K-01 (commits): 0112 $(G log -1 --format='%h %s' "$c112") [$(G show --format= --name-only "$c112" | tr '\n' ' ')]"
echo "K-01 (commits): 0113 $(G log -1 --format='%h %s' "$c113") [$(G show --format= --name-only "$c113" | tr '\n' ' ')]"
# K-07 gate (source level): nouveau identical to k15 (and so to k14)
[ -z "$(G diff --name-only k15-nouveau k16-nouveau -- drivers/gpu/drm/nouveau)" ] || fail "K-07: nouveau differs from k15-nouveau"
echo "K-07 (source): git diff k15-nouveau k16-nouveau -- drivers/gpu/drm/nouveau is empty"
o=$(G diff --name-only k15-nouveau k16-nouveau | grep -Ev "$RE_0112|$RE_0113" || true)
[ -z "$o" ] || fail "k16-nouveau differs from k15-nouveau outside traps.c/gki_defconfig/BUILD.bazel: $o"
echo "k16-nouveau = $(G rev-parse --short=12 HEAD)"

# --- 3: build into /build/dist16 (a stale dist16 from a previous k16 run is ours to replace)
if [ -n "$TEST" ]; then
  echo "TEST: no Kleaf build; tree at $(G rev-parse --short=12 HEAD)"; RC=${KB16_TEST_RC:-0}
else
  case "$DIST" in /build/dist16) rm -rf -- "$DIST" ;; *) fail "unexpected DIST $DIST" ;; esac
  cd /build/gki || fail "cd /build/gki"
  nice -n 19 ionice -c3 "${BAZEL[@]}" run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=$DIST
  RC=$?
  echo "=== build rc=$RC $(date -Is)"
  grep -q "savedefconfig does not match" "$LOGDIR/kbuild16.log" && echo "NOTE: savedefconfig mismatch (0113's defconfig is not canonical)"
fi

# --- 4: restore k13 before anything else can fail
trap - EXIT
if restore_k13; then echo "K-09: k13 working tree restored"; else echo "K-09 FAIL: k13 working tree NOT restored"; RC=97; fi

if [ "$RC" -eq 0 ] && [ -z "$TEST" ]; then
  KR=$(sed -n 's/^kernel_release=//p' $DIST/gki-info.txt)
  [ -n "$KR" ] || { echo "no kernel_release in gki-info.txt"; RC=98; }
  printf '%s\n' "$KR" > $DIST/kernel_release.txt
  K15R=$(cat /build/dist15/kernel_release.txt)
  echo "k16 kernel release: $KR (k15: $K15R)"
  [ "$KR" != "$K15R" ] || { echo "kernel release equals k15's"; RC=98; }
  ls -la --time-style=+%m-%d_%H:%M $DIST/bzImage $DIST/vmlinux $DIST/i915.ko $DIST/nouveau.ko $DIST/rtsx_usb.ko $DIST/rtsx_usb_sdmmc.ko
  # .config: exactly the five card-reader lines may differ from k15 (K-02 in k16_accept.sh)
  cdiff=$(diff /build/dist15/kernel_x86_64_dot_config $DIST/kernel_x86_64_dot_config | grep '^[<>]' | sort)
  cwant=$(printf '%s\n' '< # CONFIG_MISC_RTSX_PCI is not set' '< # CONFIG_MISC_RTSX_USB is not set' '> CONFIG_MISC_RTSX=m' \
          '> CONFIG_MISC_RTSX_PCI=m' '> CONFIG_MISC_RTSX_USB=m' '> CONFIG_MMC_REALTEK_PCI=m' '> CONFIG_MMC_REALTEK_USB=m' | sort)
  if [ "$cdiff" = "$cwant" ]; then echo "config: k15 + exactly the five card-reader options"
  else echo "config: UNEXPECTED difference from k15"; printf '%s\n' "$cdiff" | head -20; RC=99; fi
  # vmlinux.symvers: every k15 line unchanged; additions are reported (K-03 checks they are the modules' imports)
  if [ -z "$(comm -23 <(sort /build/dist15/vmlinux.symvers) <(sort $DIST/vmlinux.symvers))" ]; then
    echo "vmlinux.symvers: every k15 export unchanged; $(comm -13 <(sort /build/dist15/vmlinux.symvers) <(sort $DIST/vmlinux.symvers) | wc -l) added:" \
         "$(comm -13 <(sort /build/dist15/vmlinux.symvers) <(sort $DIST/vmlinux.symvers) | cut -f2 | tr '\n' ' ')"
  else echo "vmlinux.symvers: a k15 export CHANGED or vanished"; comm -23 <(sort /build/dist15/vmlinux.symvers) <(sort $DIST/vmlinux.symvers) | head -20; RC=99; fi
  df -h /build / | sed 's/^/df after: /'
fi
if [ "$RC" -eq 0 ]; then touch "$LOGDIR/kbuild16.done"; else touch "$LOGDIR/kbuild16.failed"; fi
echo "=== kbuild16 end rc=$RC $(date -Is)"
