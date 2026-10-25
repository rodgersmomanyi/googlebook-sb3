#!/bin/bash
# Host test for kbuild14_native.sh's source-tree handling (K-09 logic), against a throw-away git repository that
# looks like the k13 tree: detached HEAD, uncommitted modifications (text + binary), untracked files (one with a
# space), an ignored file. No Kleaf build. Usage: bash test_kbuild14_state.sh /path/to/kbuild14_native.sh
# ok()/ko() always succeed, so 'A && ok || ko' never runs ko after ok.
# shellcheck disable=SC2015
set -u
KB=${1:?usage: $0 kbuild14_native.sh}
T=$(mktemp -d /tmp/kb14test.XXXXXX)
trap 'rm -rf "$T"' EXIT
pass=0; failn=0
ok() { echo "PASS: $*"; pass=$((pass+1)); }
ko() { echo "FAIL: $*"; failn=$((failn+1)); }
g() { git -C "$T/src" "$@"; }
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t.invalid

# --- fake "ACK" history and k13 working tree
mkdir -p "$T/src/drivers/gpu/drm/nouveau" "$T/src/arch/x86/configs" "$T/src/drivers/hid"
cd "$T/src" || exit 1
git init -q .
printf 'int a(void) { return 1; }\n' > drivers/gpu/drm/nouveau/nouveau_drm.c
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
PRE=$(snap)

# --- fake patch set (2 nouveau patches) made in a clone
git clone -q "$T/src" "$T/mk" && cd "$T/mk" || exit 1
git checkout -q "$BASE"
for i in 1 2; do printf 'int n%s;\n' $i >> drivers/gpu/drm/nouveau/nouveau_drm.c; git commit -q -am "drm/nouveau: fake $i"; done
mkdir -p "$T/home/patches"
git format-patch -q -2 -o "$T/home/patches" HEAD
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
cd "$T" || exit 1

run() { KB14_TEST_SRC="$T/src" KB14_TEST_HOME="$T/home" KB14_TEST_NPATCH=2 bash "$KB" "$@"; }

# 1. full run
run; LOG=$(cat "$T/home/kbuild14.log")
[ -f "$T/home/kbuild14.done" ] && ok "run 1 done marker" || { ko "run 1 done marker"; echo "$LOG"; }
echo "$LOG" | grep -q "K-01 (commits): 2 commits" && ok "K-01 commits line" || ko "K-01 commits line"
echo "$LOG" | grep -q "K-09: k13 working tree restored" && ok "K-09 restored line" || ko "K-09 restored line"
[ "$(snap)" = "$PRE" ] && ok "tree identical after run 1 (independent snapshot)" || { ko "tree after run 1"; diff <(echo "$PRE") <(snap); }
N13=$(g rev-parse k13-native); N14=$(g rev-parse k14-nouveau)
[ "$(g show k14-nouveau:drivers/gpu/drm/nouveau/nouveau_drm.c | grep -c '^int n')" = 2 ] && ok "k14-nouveau carries both patches" || ko "k14 content"
[ "$(g show k13-native:BUILD.bazel)" = 'modules = ["a.ko", "nouveau.ko"]' ] && ok "k13-native records the k13 edits" || ko "k13-native content"
g cat-file -e "k13-native:drivers/hid/ipts dir/a b.c" && ok "k13-native records untracked files" || ko "untracked in k13-native"
if g cat-file -e k13-native:drivers/hid/x.o 2>/dev/null; then ko "ignored file committed"; else ok "ignored file not committed"; fi

# 2. idempotent re-run: same commits (reproducible krel), same tree
run
[ -f "$T/home/kbuild14.done" ] && ok "run 2 done" || ko "run 2 done"
[ "$(g rev-parse k13-native)" = "$N13" ] && [ "$(g rev-parse k14-nouveau)" = "$N14" ] && ok "run 2 reproduces k13-native/k14-nouveau ids" || ko "ids changed"
[ "$(snap)" = "$PRE" ] && ok "tree identical after run 2" || ko "tree after run 2"

# 3. failing build still restores
KB14_TEST_RC=1 run
[ -f "$T/home/kbuild14.failed" ] && ok "failed build -> failed marker" || ko "failed marker"
[ "$(snap)" = "$PRE" ] && ok "tree identical after failed build" || ko "tree after failed build"

# 4. interrupted run (HEAD left on k14-nouveau) -> --restore
g symbolic-ref HEAD refs/heads/k14-nouveau; g reset -q --hard k14-nouveau 2>/dev/null
# (reset --hard would only touch tracked files; the untracked k13 files are tracked on k14-nouveau, so they stay)
run --restore > "$T/restore.out" 2>&1
grep -q "identical to saved state" "$T/restore.out" && [ "$(snap)" = "$PRE" ] && ok "--restore after interruption" || { ko "--restore"; cat "$T/restore.out"; }

# 5. a patch outside drivers/gpu/drm/nouveau/ is refused before the tree is touched
cd "$T/mk" || exit 1; printf 'x\n' >> drivers/hid/Kconfig; git commit -q -am "HID: outside"; cd "$T" || exit 1
git -C "$T/mk" format-patch -q -1 --start-number 3 -o "$T/home/patches" HEAD
(cd "$T/home/patches" && sha256sum ./*.patch > SHA256SUMS)
KB14_TEST_SRC="$T/src" KB14_TEST_HOME="$T/home" KB14_TEST_NPATCH=3 bash "$KB"
grep -q "patches touch files outside drivers/gpu/drm/nouveau/: drivers/hid/Kconfig" "$T/home/kbuild14.log" && ok "K-01 rejects outside patch" || ko "K-01 outside"
[ "$(snap)" = "$PRE" ] && ok "tree untouched after K-01 refusal" || ko "tree after refusal"

# 6. tampered patch set -> refused
rm "$T/home/patches/0003-"*; for f in "$T/home/patches/0001-"*; do printf ' ' >> "$f"; done
KB14_TEST_SRC="$T/src" KB14_TEST_HOME="$T/home" KB14_TEST_NPATCH=2 bash "$KB"
grep -q "patch set does not match" "$T/home/kbuild14.log" && ok "SHA256SUMS mismatch refused" || ko "sums"

echo "== $pass passed, $failn failed"
[ $failn = 0 ]
