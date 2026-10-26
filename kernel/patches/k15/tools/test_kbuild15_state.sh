#!/bin/bash
# Host test for kbuild15_native.sh's source-tree handling (K-09 logic), against a throw-away git repository that
# looks like the k13 tree: detached HEAD, uncommitted modifications (text + binary), untracked files (one with a
# space), an ignored file. The k14 base (k13-native, k14-nouveau with 2 fake nouveau patches) is made by running
# the real kbuild14_native.sh in its own test mode. No Kleaf build.
# Usage: bash test_kbuild15_state.sh /path/to/kbuild15_native.sh /path/to/kbuild14_native.sh
# ok()/ko() always succeed, so 'A && ok || ko' never runs ko after ok.
# shellcheck disable=SC2015
set -u
KB=${1:?usage: $0 kbuild15_native.sh kbuild14_native.sh}
KB14=${2:?usage: $0 kbuild15_native.sh kbuild14_native.sh}
T=$(mktemp -d /tmp/kb15test.XXXXXX)
trap 'rm -rf "$T"' EXIT
pass=0; failn=0
ok() { echo "PASS: $*"; pass=$((pass+1)); }
ko() { echo "FAIL: $*"; failn=$((failn+1)); }
g() { git -C "$T/src" "$@"; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t.invalid

# --- fake "ACK" history and k13 working tree
mkdir -p "$T/src/drivers/gpu/drm/nouveau" "$T/src/arch/x86/configs" "$T/src/arch/x86/kernel" "$T/src/drivers/hid" \
         "$T/src/drivers/gpu/drm/i915/display"
cd "$T/src" || exit 1
git init -q .
printf 'int a(void) { return 1; }\n' > drivers/gpu/drm/nouveau/nouveau_drm.c
printf 'void handle_invalid_op(void) { }\n' > arch/x86/kernel/traps.c
printf 'int other;\n' > arch/x86/kernel/other.c
printf 'int gamma;\n' > drivers/gpu/drm/i915/display/intel_display.c
printf 'int drv;\n' > drivers/gpu/drm/i915/i915_drv.c
printf 'CONFIG_X=y\n' > arch/x86/configs/gki_defconfig
printf 'modules = ["a.ko"]\n' > BUILD.bazel
printf 'hid\n' > drivers/hid/Kconfig
printf 'out/\n*.o\n' > .gitignore
git add -A && git commit -q -m base && git commit -q --allow-empty -m tip
BASE=$(git rev-parse HEAD)
git checkout -q --detach "$BASE"
printf 'CONFIG_X=y\nCONFIG_DRM_NOUVEAU=m\n' > arch/x86/configs/gki_defconfig          # k13-style edits
printf 'modules = ["a.ko", "nouveau.ko"]\n' > BUILD.bazel
printf 'hid\nconfig HID_SURFACE\n' > drivers/hid/Kconfig
printf 'void handle_invalid_op(void) { /* v3 */ }\n' > arch/x86/kernel/traps.c         # the k13 emulator
head -c 300 /dev/urandom > drivers/hid/blob.bin; git add drivers/hid/blob.bin; git commit -q -m blob
git checkout -q --detach HEAD; BASE=$(git rev-parse HEAD)
head -c 200 /dev/urandom > drivers/hid/blob.bin                                       # binary modification
printf 'new\n' > drivers/hid/hid-surface.c                                              # untracked
mkdir -p "drivers/hid/ipts dir"; printf 'x\n' > "drivers/hid/ipts dir/a b.c"            # untracked, spaces
printf 'obj\n' > drivers/hid/x.o                                                        # ignored
snap() {  # independent snapshot of the tree state
  { g rev-parse HEAD; g symbolic-ref -q HEAD || echo DETACHED; g status --porcelain=v1 --untracked-files=all
    g diff --binary HEAD | sha256sum; (cd "$T/src" && git ls-files -o --exclude-standard -z | xargs -0 sha256sum)
    sha256sum "$T/src/drivers/hid/x.o"; } 2>&1
}

# --- the k14 base, made by kbuild14 itself (2 fake nouveau patches)
git clone -q "$T/src" "$T/mk14" && cd "$T/mk14" || exit 1
git checkout -q "$BASE"
for i in 1 2; do printf 'int n%s;\n' $i >> drivers/gpu/drm/nouveau/nouveau_drm.c; git commit -q -am "drm/nouveau: fake $i"; done
mkdir -p "$T/k14home/patches"
git format-patch -q -2 -o "$T/k14home/patches" HEAD
(cd "$T/k14home/patches" && sha256sum ./*.patch > SHA256SUMS)
cd "$T" || exit 1
KB14_TEST_SRC="$T/src" KB14_TEST_HOME="$T/k14home" KB14_TEST_NPATCH=2 bash "$KB14"
[ -f "$T/k14home/kbuild14.done" ] && ok "k14 base built by kbuild14 (test mode)" || { ko "k14 base"; cat "$T/k14home/kbuild14.log"; }
PRE=$(snap)
N14=$(g rev-parse k14-nouveau)

# --- fake k15 patch set made on a clone of k14-nouveau: 0110 traps.c + new gb_vnni_core.h, 0111 i915/display
git clone -q "$T/src" "$T/mk" && cd "$T/mk" || exit 1
git checkout -q -b w "origin/k14-nouveau" 2>/dev/null || git checkout -q -b w "$N14"
printf 'void handle_invalid_op(void) { /* v4 */ }\n' > arch/x86/kernel/traps.c
printf '/* core */\n' > arch/x86/kernel/gb_vnni_core.h
git add -A; GIT_AUTHOR_DATE="2026-10-26T12:00:00+00:00" git commit -q -m "x86/traps: v4"
printf 'int gamma; int gb_ignore_user_gamma;\n' > drivers/gpu/drm/i915/display/intel_display.c
GIT_AUTHOR_DATE="2026-10-26T12:00:01+00:00" git commit -q -am "drm/i915/display: gb param"
mkdir -p "$T/home/patches"
git format-patch -q -2 --start-number 110 -o "$T/home/patches" HEAD
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
cd "$T" || exit 1

run() { KB15_TEST_SRC="$T/src" KB15_TEST_HOME="$T/home" KB15_TEST_NNOUVEAU=2 KB15_K14_ID='' \
        KB15_K14_STATE="$T/k14home/k13-state" bash "$KB" "$@"; }

# 1. full run
run; LOG=$(cat "$T/home/kbuild15.log")
[ -f "$T/home/kbuild15.done" ] && ok "run 1 done marker" || { ko "run 1 done marker"; echo "$LOG"; }
echo "$LOG" | grep -q "K-01 (commits): 0110 " && echo "$LOG" | grep -q "K-01 (commits): 0111 " && ok "K-01 commit lines" || ko "K-01 commit lines"
echo "$LOG" | grep -q "K-07 (source): git diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau is empty" && ok "K-07 source gate line" || ko "K-07 line"
echo "$LOG" | grep -q "k13-state: identical to k14's saved state" && ok "k13 state == k14's saved state" || ko "k13 vs k14 state"
echo "$LOG" | grep -q "K-09: k13 working tree restored" && ok "K-09 restored line" || ko "K-09 restored line"
[ "$(snap)" = "$PRE" ] && ok "tree identical after run 1 (independent snapshot)" || { ko "tree after run 1"; diff <(echo "$PRE") <(snap); }
N15=$(g rev-parse k15-nouveau)
[ "$(g rev-list --count k14-nouveau..k15-nouveau)" = 2 ] && ok "k15-nouveau = k14-nouveau + 2" || ko "k15 commit count"
[ "$(g show k15-nouveau:arch/x86/kernel/traps.c)" = 'void handle_invalid_op(void) { /* v4 */ }' ] && g cat-file -e k15-nouveau:arch/x86/kernel/gb_vnni_core.h \
  && ok "k15-nouveau carries 0110 (traps.c + new header)" || ko "k15 content"
[ -z "$(g diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau)" ] && ok "nouveau identical to k14-nouveau" || ko "nouveau differs"
[ ! -e "$T/src/arch/x86/kernel/gb_vnni_core.h" ] && ok "the new header is gone from the restored working tree" || ko "gb_vnni_core.h left behind"
[ "$(g rev-parse k14-nouveau)" = "$N14" ] && ok "k14-nouveau untouched" || ko "k14-nouveau moved"

# 2. idempotent re-run: same commits (reproducible krel), same tree
run
[ -f "$T/home/kbuild15.done" ] && ok "run 2 done" || ko "run 2 done"
[ "$(g rev-parse k15-nouveau)" = "$N15" ] && ok "run 2 reproduces the k15-nouveau id" || ko "id changed"
[ "$(snap)" = "$PRE" ] && ok "tree identical after run 2" || ko "tree after run 2"

# 3. failing build still restores
KB15_TEST_RC=1 run
[ -f "$T/home/kbuild15.failed" ] && ok "failed build -> failed marker" || ko "failed marker"
[ "$(snap)" = "$PRE" ] && ok "tree identical after failed build" || ko "tree after failed build"

# 4. interrupted run (HEAD left on k15-nouveau, files checked out) -> --restore
g symbolic-ref HEAD refs/heads/k13-native; g reset -q --mixed; g checkout -q k15-nouveau
run --restore > "$T/restore.out" 2>&1
grep -q "identical to saved state" "$T/restore.out" && [ "$(snap)" = "$PRE" ] && ok "--restore after interruption" || { ko "--restore"; cat "$T/restore.out"; }

# 5. interrupted inside git am (rebase-apply present) -> --restore aborts the am and restores
g symbolic-ref HEAD refs/heads/k13-native; g reset -q --mixed; g checkout -q -B k15-nouveau k14-nouveau
printf 'From 0 Mon Sep 17 00:00:00 2001\nFrom: t <t@t.invalid>\nSubject: [PATCH] broken\n\n---\n' > "$T/bad.patch"
cat >> "$T/bad.patch" <<'EOF'
diff --git a/arch/x86/kernel/traps.c b/arch/x86/kernel/traps.c
--- a/arch/x86/kernel/traps.c
+++ b/arch/x86/kernel/traps.c
@@ -1 +1 @@
-does not match
+x
EOF
g am "$T/bad.patch" > /dev/null 2>&1
[ -d "$(g rev-parse --absolute-git-dir)/rebase-apply" ] && ok "(setup) a git am is in progress" || ko "(setup) am state"
run --restore > "$T/restore2.out" 2>&1
grep -q "identical to saved state" "$T/restore2.out" && [ "$(snap)" = "$PRE" ] && ok "--restore inside git am" || { ko "--restore in am"; cat "$T/restore2.out"; }

# 6. 0110 outside traps.c/gb_vnni_core.h is refused before the tree is touched
cp -a "$T/home/patches" "$T/patches.good"
sed -i 's#arch/x86/kernel/gb_vnni_core.h#arch/x86/kernel/other.c#g' "$T/home/patches/"0110-*.patch
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
run
grep -q "0110 touches files outside traps.c/gb_vnni_core.h: arch/x86/kernel/other.c" "$T/home/kbuild15.log" && ok "K-01 rejects 0110 outside its files" || ko "K-01 0110"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the 0110 refusal" || ko "tree after 0110 refusal"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"

# 7. 0111 outside drivers/gpu/drm/i915/display/ is refused
sed -i 's#drivers/gpu/drm/i915/display/intel_display.c#drivers/gpu/drm/i915/i915_drv.c#g' "$T/home/patches/"0111-*.patch
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
run
grep -q "0111 touches files outside drivers/gpu/drm/i915/display/: drivers/gpu/drm/i915/i915_drv.c" "$T/home/kbuild15.log" && ok "K-01 rejects 0111 outside display/" || ko "K-01 0111"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the 0111 refusal" || ko "tree after 0111 refusal"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"

# 8. tampered patch set -> refused
for f in "$T/home/patches/0110-"*; do printf ' ' >> "$f"; done
run
grep -q "patch set does not match" "$T/home/kbuild15.log" && ok "SHA256SUMS mismatch refused" || ko "sums"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"

# 9. no k14-nouveau -> refused, tree untouched
g branch -m k14-nouveau k14-away
run
grep -q "branch k14-nouveau missing" "$T/home/kbuild15.log" && ok "missing k14-nouveau refused" || ko "missing k14-nouveau"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the k14-nouveau refusal" || ko "tree after k14 refusal"
g branch -m k14-away k14-nouveau

# 10. expected k14 id mismatch -> refused
KB15_TEST_SRC="$T/src" KB15_TEST_HOME="$T/home" KB15_TEST_NNOUVEAU=2 KB15_K14_ID=000000000000 \
  KB15_K14_STATE="$T/k14home/k13-state" bash "$KB"
grep -q "k14-nouveau is .*, expected 000000000000" "$T/home/kbuild15.log" && ok "k14-nouveau id check" || ko "k14 id check"

# 11. a k13 state that differs from k14's saved one -> refused before HEAD moves
cp -a "$T/k14home/k13-state" "$T/s14bad"; echo 0000000000000000000000000000000000000000 > "$T/s14bad/head.txt"
KB15_TEST_SRC="$T/src" KB15_TEST_HOME="$T/home" KB15_TEST_NNOUVEAU=2 KB15_K14_ID='' KB15_K14_STATE="$T/s14bad" bash "$KB"
grep -q "k13 state differs from k14's" "$T/home/kbuild15.log" && [ -f "$T/home/kbuild15.failed" ] && ok "k13 state vs k14 state mismatch refused" || ko "state mismatch"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the state refusal" || ko "tree after state refusal"

echo "== $pass passed, $failn failed"
[ $failn = 0 ]
