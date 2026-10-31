#!/bin/bash
# Host test for kbuild16_native.sh's source-tree handling (K-09 logic), against a throw-away git repository that
# looks like the k13 tree: detached HEAD, uncommitted modifications (text + binary), untracked files (one with a
# space), an ignored file. The k14 and k15 bases are made by running the real kbuild14_native.sh and
# kbuild15_native.sh in their own test modes (2 fake nouveau patches, fake 0110/0111). No Kleaf build.
# Mutation cases: 0112 or 0113 outside their files, a tampered patch set, a missing or moved k15-nouveau, a k13 state
# that differs from k15's saved one, an interrupted run and an interrupted git am.
# Usage (builder): bash test_kbuild16_state.sh kbuild16_native.sh kbuild15_native.sh kbuild14_native.sh
# ok()/ko() always succeed, so 'A && ok || ko' never runs ko after ok.
# shellcheck disable=SC2015
set -u
KB=${1:?usage: $0 kbuild16_native.sh kbuild15_native.sh kbuild14_native.sh}
KB15=${2:?usage}
KB14=${3:?usage}
T=$(mktemp -d /tmp/kb16test.XXXXXX)
trap 'rm -rf "$T"' EXIT
pass=0; failn=0
ok() { echo "PASS: $*"; pass=$((pass+1)); }
ko() { echo "FAIL: $*"; failn=$((failn+1)); }
g() { git -C "$T/src" "$@"; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t.invalid

# --- fake "ACK" history and k13 working tree (as test_kbuild15_state.sh)
mkdir -p "$T/src/drivers/gpu/drm/nouveau" "$T/src/arch/x86/configs" "$T/src/arch/x86/kernel" "$T/src/drivers/hid" \
         "$T/src/drivers/gpu/drm/i915/display" "$T/src/drivers/mmc/host"
cd "$T/src" || exit 1
git init -q .
printf 'int a(void) { return 1; }\n' > drivers/gpu/drm/nouveau/nouveau_drm.c
printf 'void handle_invalid_op(void) { }\n' > arch/x86/kernel/traps.c
printf 'int other;\n' > arch/x86/kernel/other.c
printf 'int gamma;\n' > drivers/gpu/drm/i915/display/intel_display.c
printf 'int drv;\n' > drivers/gpu/drm/i915/i915_drv.c
printf 'int host;\n' > drivers/mmc/host/rtsx_usb_sdmmc.c
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
KB14_TEST_SRC="$T/src" KB14_TEST_HOME="$T/k14home" KB14_TEST_NPATCH=2 bash "$KB14" > /dev/null 2>&1
[ -f "$T/k14home/kbuild14.done" ] && ok "k14 base built by kbuild14 (test mode)" || { ko "k14 base"; cat "$T/k14home/kbuild14.log"; }
N14=$(g rev-parse k14-nouveau)

# --- the k15 base, made by kbuild15 itself (fake 0110 traps.c + gb_vnni_core.h, 0111 i915/display)
git clone -q "$T/src" "$T/mk15" && cd "$T/mk15" || exit 1
git checkout -q -b w "$N14"
printf 'void handle_invalid_op(void) { /* v4 */ }\n' > arch/x86/kernel/traps.c
printf '/* core */\n' > arch/x86/kernel/gb_vnni_core.h
git add -A; GIT_AUTHOR_DATE="2026-10-26T12:00:00+00:00" git commit -q -m "x86/traps: v4"
printf 'int gamma; int gb_ignore_user_gamma;\n' > drivers/gpu/drm/i915/display/intel_display.c
GIT_AUTHOR_DATE="2026-10-26T12:00:01+00:00" git commit -q -am "drm/i915/display: gb param"
mkdir -p "$T/k15home/patches"
git format-patch -q -2 --start-number 110 -o "$T/k15home/patches" HEAD
(cd "$T/k15home/patches" && sha256sum ./*.patch > SHA256SUMS)
cd "$T" || exit 1
KB15_TEST_SRC="$T/src" KB15_TEST_HOME="$T/k15home" KB15_TEST_NNOUVEAU=2 KB15_K14_ID='' \
  KB15_K14_STATE="$T/k14home/k13-state" bash "$KB15" > /dev/null 2>&1
[ -f "$T/k15home/kbuild15.done" ] && ok "k15 base built by kbuild15 (test mode)" || { ko "k15 base"; cat "$T/k15home/kbuild15.log"; }
PRE=$(snap)
N15=$(g rev-parse k15-nouveau)

# --- fake k16 patch set made on a clone of k15-nouveau: 0112 traps.c, 0113 gki_defconfig + BUILD.bazel
git clone -q "$T/src" "$T/mk" && cd "$T/mk" || exit 1
git checkout -q -b w "$N15"
printf 'void handle_invalid_op(void) { /* v4 + escaped comm */ }\n' > arch/x86/kernel/traps.c
GIT_AUTHOR_DATE="2026-10-26T20:00:00+00:00" git commit -q -am "x86/traps: gb_vnni: escape comm"
printf 'CONFIG_X=y\nCONFIG_DRM_NOUVEAU=m\nCONFIG_MISC_RTSX_USB=m\n' > arch/x86/configs/gki_defconfig
printf 'modules = ["a.ko", "nouveau.ko", "rtsx_usb.ko"]\n' > BUILD.bazel
GIT_AUTHOR_DATE="2026-10-30T20:00:00+00:00" git commit -q -am "gki_defconfig, BUILD.bazel: rtsx"
mkdir -p "$T/home/patches"
git format-patch -q -2 --start-number 112 -o "$T/home/patches" HEAD
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
cd "$T" || exit 1

run() { KB16_TEST_SRC="$T/src" KB16_TEST_HOME="$T/home" KB16_K15_ID='' KB16_K14_ID='' \
        KB16_K15_STATE="$T/k15home/k13-state" bash "$KB" "$@"; }

# 1. full run
run; LOG=$(cat "$T/home/kbuild16.log")
[ -f "$T/home/kbuild16.done" ] && ok "run 1 done marker" || { ko "run 1 done marker"; echo "$LOG"; }
echo "$LOG" | grep -q "K-01 (commits): 0112 .*\[arch/x86/kernel/traps.c \]" && \
  echo "$LOG" | grep -q "K-01 (commits): 0113 .*\[BUILD.bazel arch/x86/configs/gki_defconfig \]" && ok "K-01 commit lines" || ko "K-01 commit lines"
echo "$LOG" | grep -q "K-07 (source): git diff k15-nouveau k16-nouveau -- drivers/gpu/drm/nouveau is empty" && ok "K-07 source gate line" || ko "K-07 line"
echo "$LOG" | grep -q "k13-state: identical to k15's saved state" && ok "k13 state == k15's saved state" || ko "k13 vs k15 state"
echo "$LOG" | grep -q "K-09: k13 working tree restored" && ok "K-09 restored line" || ko "K-09 restored line"
[ "$(snap)" = "$PRE" ] && ok "tree identical after run 1 (independent snapshot)" || { ko "tree after run 1"; diff <(echo "$PRE") <(snap); }
N16=$(g rev-parse k16-nouveau)
[ "$(g rev-list --count k15-nouveau..k16-nouveau)" = 2 ] && ok "k16-nouveau = k15-nouveau + 2" || ko "k16 commit count"
[ "$(g show k16-nouveau:arch/x86/kernel/traps.c)" = 'void handle_invalid_op(void) { /* v4 + escaped comm */ }' ] && \
  g show k16-nouveau:arch/x86/configs/gki_defconfig | grep -qx 'CONFIG_MISC_RTSX_USB=m' && \
  g cat-file -e k16-nouveau:arch/x86/kernel/gb_vnni_core.h && ok "k16-nouveau carries k15 + 0112 + 0113" || ko "k16 content"
[ -z "$(g diff k15-nouveau k16-nouveau -- drivers/gpu/drm/nouveau drivers/gpu/drm/i915)" ] && ok "nouveau and i915 identical to k15-nouveau" || ko "nouveau/i915 differ"
[ "$(g rev-parse k15-nouveau)" = "$N15" ] && [ "$(g rev-parse k14-nouveau)" = "$N14" ] && ok "k15-nouveau and k14-nouveau untouched" || ko "k14/k15 moved"
grep -q 'CONFIG_MISC_RTSX_USB' "$T/src/arch/x86/configs/gki_defconfig" && ko "0113 left in the restored working tree" || ok "the restored working tree has the k13 defconfig"

# 2. idempotent re-run: same commits (reproducible krel), same tree
run
[ -f "$T/home/kbuild16.done" ] && ok "run 2 done" || ko "run 2 done"
[ "$(g rev-parse k16-nouveau)" = "$N16" ] && ok "run 2 reproduces the k16-nouveau id" || ko "id changed"
[ "$(snap)" = "$PRE" ] && ok "tree identical after run 2" || ko "tree after run 2"

# 3. failing build still restores
KB16_TEST_RC=1 run
[ -f "$T/home/kbuild16.failed" ] && ok "failed build -> failed marker" || ko "failed marker"
[ "$(snap)" = "$PRE" ] && ok "tree identical after failed build" || ko "tree after failed build"

# 4. interrupted run (HEAD left on k16-nouveau, files checked out) -> --restore
g symbolic-ref HEAD refs/heads/k13-native; g reset -q --mixed; g checkout -q k16-nouveau
run --restore > "$T/restore.out" 2>&1
grep -q "identical to saved state" "$T/restore.out" && [ "$(snap)" = "$PRE" ] && ok "--restore after interruption" || { ko "--restore"; cat "$T/restore.out"; }

# 4b. a build started while a previous run left HEAD on k16-nouveau restores first, then builds
g symbolic-ref HEAD refs/heads/k13-native; g reset -q --mixed; g checkout -q k16-nouveau
run
grep -q "previous run left HEAD on k16-nouveau; restoring k13 first" "$T/home/kbuild16.log" && [ -f "$T/home/kbuild16.done" ] && \
  [ "$(snap)" = "$PRE" ] && ok "build after an interrupted run restores first" || ko "build after interruption"

# 5. interrupted inside git am (rebase-apply present) -> --restore aborts the am and restores
g symbolic-ref HEAD refs/heads/k13-native; g reset -q --mixed; g checkout -q -B k16-nouveau k15-nouveau
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

# 6. 0112 outside traps.c is refused before the tree is touched
cp -a "$T/home/patches" "$T/patches.good"
sed -i 's#arch/x86/kernel/traps.c#arch/x86/kernel/other.c#g' "$T/home/patches/"0112-*.patch
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
run
grep -q "0112 touches files outside traps.c: arch/x86/kernel/other.c" "$T/home/kbuild16.log" && ok "K-01 rejects 0112 outside traps.c" || ko "K-01 0112"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the 0112 refusal" || ko "tree after 0112 refusal"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"

# 7. 0113 outside gki_defconfig/BUILD.bazel is refused
sed -i 's#BUILD.bazel#drivers/mmc/host/rtsx_usb_sdmmc.c#g' "$T/home/patches/"0113-*.patch
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
run
grep -q "0113 touches files outside gki_defconfig/BUILD.bazel: drivers/mmc/host/rtsx_usb_sdmmc.c" "$T/home/kbuild16.log" && ok "K-01 rejects 0113 outside its files" || ko "K-01 0113"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the 0113 refusal" || ko "tree after 0113 refusal"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"

# 8. tampered patch set -> refused; a third patch -> refused
for f in "$T/home/patches/0112-"*; do printf ' ' >> "$f"; done
run
grep -q "patch set does not match" "$T/home/kbuild16.log" && ok "SHA256SUMS mismatch refused" || ko "sums"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"
cp "$T/home/patches/"0112-*.patch "$T/home/patches/0114-extra.patch"; (cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
run
grep -q "expected 2 patches in" "$T/home/kbuild16.log" && ok "an extra patch is refused" || ko "extra patch"
rm -rf "$T/home/patches"; cp -a "$T/patches.good" "$T/home/patches"

# 9. no k15-nouveau -> refused, tree untouched
g branch -m k15-nouveau k15-away
run
grep -q "branch k15-nouveau missing" "$T/home/kbuild16.log" && ok "missing k15-nouveau refused" || ko "missing k15-nouveau"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the k15-nouveau refusal" || ko "tree after k15 refusal"
g branch -m k15-away k15-nouveau

# 10. expected k15 id mismatch -> refused; k15-nouveau with a third commit -> refused
KB16_TEST_SRC="$T/src" KB16_TEST_HOME="$T/home" KB16_K15_ID=000000000000 KB16_K14_ID='' \
  KB16_K15_STATE="$T/k15home/k13-state" bash "$KB"
grep -q "k15-nouveau is .*, expected 000000000000" "$T/home/kbuild16.log" && ok "k15-nouveau id check" || ko "k15 id check"
KB16_TEST_SRC="$T/src" KB16_TEST_HOME="$T/home" KB16_K15_ID='' KB16_K14_ID='' KB16_TEST_NK15=3 \
  KB16_K15_STATE="$T/k15home/k13-state" bash "$KB"
grep -q "k15-nouveau has 2 commits over k14-nouveau, expected 3" "$T/home/kbuild16.log" && ok "k15 commit-count check" || ko "k15 count check"

# 11. a k13 state that differs from k15's saved one -> refused before HEAD moves
cp -a "$T/k15home/k13-state" "$T/s15bad"; echo 0000000000000000000000000000000000000000 > "$T/s15bad/head.txt"
KB16_TEST_SRC="$T/src" KB16_TEST_HOME="$T/home" KB16_K15_ID='' KB16_K14_ID='' KB16_K15_STATE="$T/s15bad" bash "$KB"
grep -q "k13 state differs from k15's" "$T/home/kbuild16.log" && [ -f "$T/home/kbuild16.failed" ] && ok "k13 state vs k15 state mismatch refused" || ko "state mismatch"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after the state refusal" || ko "tree after state refusal"

echo "== $pass passed, $failn failed"
[ $failn = 0 ]
