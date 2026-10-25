#!/bin/sh
# test_gb_slotsync.sh -- TB-1 of WAVE2_PLAN_v2 B-01 (gb_slotsync.c), run on the builder (Ubuntu: gcc + ASan/UBSan,
# NDK r27c clang, readelf, strings).
#
#   sh test_gb_slotsync.sh SRC_DIR WORK_DIR
#
# SRC_DIR holds gb_slotsync.c (the new source) and gb_slotsync.old.c (the pre-Wave-2 source, sha256 3670f1a7..., only
# used by S4). WORK_DIR is created empty (rm -rf first) and holds everything the test writes; the NDK binary is left
# at WORK_DIR/ndk/gb_slotsync. Prints "PASS <case>" / "FAIL <case>" lines and "TB-1 RESULT: OK" or
# "TB-1 RESULT: <n> failure(s)" (exit 0 / 1).
#   S1 copy gb-a.conf + gb-a-nvtest.conf to slot b    S2 no nvtest template         S3 no final newline, CRLF, 4 KiB line
#   S3b a 3 MiB template (read_all growth)            S4 the old source fails S1 under ASan (mutation proof)
#   S5 the ESP lock (INTERFACES 5.4), fake clock      S6 --dry-run end to end         S7 the NDK binary
set -u
S=$(cd "$1" && pwd); W=$2
NDK_CC=/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang
FAILS=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
ok() { if [ "$1" = 0 ]; then pass "$2"; else fail "$2"; fi; }

rm -rf "$W"; mkdir -p "$W"; W=$(cd "$W" && pwd)
ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:abort_on_error=0; export ASAN_OPTIONS
UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1; export UBSAN_OPTIONS
T=$W/gb_slotsync_test
echo "== host test build: gcc $(gcc -dumpfullversion), -DGB_SLOTSYNC_TEST -fsanitize=address,undefined"
gcc -DGB_SLOTSYNC_TEST -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -Wall -Wextra -Werror \
  -o "$T" "$S/gb_slotsync.c" 2> "$W/build.err"
ok $? "test build compiles with -Wall -Wextra -Werror"
cat "$W/build.err"
[ -x "$T" ] || { echo "TB-1 RESULT: no test binary"; exit 1; }

# sanitizer reports in a stderr file
clean() { ! grep -q -E 'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:|SUMMARY: (Address|Undefined|Leak)' "$1"; }

OPTS='androidboot.boot_part_uuid=11111111-2222-4333-8444-555555555555 console=tty0 gb.hw=native gb.boot=usb gb.logpart=EFI-SYSTEM efi_pstore.pstore_disable=1 module_blacklist=nouveau reboot=pci'
TITLE='Googlebook OS - USB image (Surface Book 3)'
mkesp() {   # mkesp DIR [nvtest]
  rm -rf "$1"; mkdir -p "$1/loader/entries" "$1/gb"
  printf 'default gb-a\ntimeout 4\nconsole-mode max\neditor yes\n' > "$1/loader/loader.conf"
  printf 'title   %s (slot A)\nlinux   /vmlinuz\ninitrd  /initrd-a.img\noptions %s\n' "$TITLE" "$OPTS" \
    > "$1/loader/entries/gb-a.conf"
  [ "${2:-}" = nvtest ] && printf 'title   %s (slot A) [NVIDIA test]\nlinux   /vmlinuz\ninitrd  /initrd-a.img\noptions %s gb.nouveau=test\n' \
    "$TITLE" "$OPTS" > "$1/loader/entries/gb-a-nvtest.conf"
  : > "$1/initrd-a.img"
}
expect_b() {   # expect_b TEMPLATE -> the slot-b form on stdout
  sed -e 's/(slot A)/(slot B)/' -e 's|^initrd  /initrd-a\.img|initrd  /initrd-b.img|' "$1"
}

# ---- S1
E=$W/s1/esp; mkesp "$E" nvtest
GB_SLOTSYNC_MNT=$E "$T" --test-copy-entry b a > "$W/s1/out" 2> "$W/s1/err"; rc=$?
ok $rc "S1 --test-copy-entry b a exits 0"
expect_b "$E/loader/entries/gb-a.conf" | cmp -s - "$E/loader/entries/gb-b.conf"; ok $? "S1 gb-b.conf == gb-a.conf except (slot B) and initrd-b"
expect_b "$E/loader/entries/gb-a-nvtest.conf" | cmp -s - "$E/loader/entries/gb-b-nvtest.conf"
ok $? "S1 gb-b-nvtest.conf == gb-a-nvtest.conf except (slot B) and initrd-b"
grep -q '^title   .*(slot B) \[NVIDIA test\]$' "$E/loader/entries/gb-b-nvtest.conf"; ok $? "S1 the nvtest title keeps its suffix"
printf 'default gb-b\ntimeout 4\nconsole-mode max\neditor yes\n' | cmp -s - "$E/loader/loader.conf"; ok $? "S1 loader.conf default -> gb-b, rest unchanged"
[ "$(cd "$E/loader/entries" && ls)" = "$(printf 'gb-a-nvtest.conf\ngb-a.conf\ngb-b-nvtest.conf\ngb-b.conf')" ]
ok $? "S1 entries are exactly gb-{a,b}{,-nvtest}.conf (no -gfxtest, no .tmp left)"
clean "$W/s1/err"; ok $? "S1 no sanitizer report"

# ---- S2
E=$W/s2/esp; mkesp "$E"
GB_SLOTSYNC_MNT=$E "$T" --test-copy-entry b a > "$W/s2/out" 2> "$W/s2/err"; rc=$?
ok $rc "S2 no nvtest template: exits 0"
[ -f "$E/loader/entries/gb-b.conf" ] && [ ! -e "$E/loader/entries/gb-b-nvtest.conf" ]; ok $? "S2 only gb-b.conf is written"
! grep -q 'not copied' "$W/s2/out"; ok $? "S2 a missing nvtest template is not reported as an error"
clean "$W/s2/err"; ok $? "S2 no sanitizer report"

# ---- S3: CRLF lines, a 4 KiB options line, no final newline
E=$W/s3/esp; mkesp "$E"
LONG=$(head -c 4096 /dev/zero | tr '\0' 'x')
printf 'title   %s (slot A)\r\nlinux   /vmlinuz\r\ninitrd  /initrd-a.img\r\noptions %s %s' "$TITLE" "$OPTS" "$LONG" \
  > "$E/loader/entries/gb-a.conf"
printf 'title   %s (slot A) [NVIDIA test]\ninitrd  /initrd-a.img\noptions %s gb.nouveau=test' "$TITLE" "$LONG" \
  > "$E/loader/entries/gb-a-nvtest.conf"
GB_SLOTSYNC_MNT=$E "$T" --test-copy-entry b a > "$W/s3/out" 2> "$W/s3/err"; rc=$?
ok $rc "S3 exits 0"
printf 'title   %s (slot B)\r\nlinux   /vmlinuz\r\ninitrd  /initrd-b.img\r\noptions %s %s\n' "$TITLE" "$OPTS" "$LONG" \
  | cmp -s - "$E/loader/entries/gb-b.conf"
ok $? "S3 CRLF kept, initrd line rewritten with its CR, 4 KiB line intact, final newline added"
printf 'title   %s (slot B) [NVIDIA test]\ninitrd  /initrd-b.img\noptions %s gb.nouveau=test\n' "$TITLE" "$LONG" \
  | cmp -s - "$E/loader/entries/gb-b-nvtest.conf"
ok $? "S3 nvtest template without a final newline"
clean "$W/s3/err"; ok $? "S3 no sanitizer report"

# ---- S3b: a template larger than read_all's first 1 MiB block (realloc growth path)
E=$W/s3b/esp; mkesp "$E"
{ printf 'title   %s (slot A)\ninitrd  /initrd-a.img\n' "$TITLE"; head -c 3145728 /dev/zero | tr '\0' 'y'; printf '\n'; } \
  > "$E/loader/entries/gb-a.conf"
GB_SLOTSYNC_MNT=$E "$T" --test-copy-entry b a > "$W/s3b/out" 2> "$W/s3b/err"; rc=$?
ok $rc "S3b 3 MiB template: exits 0"
expect_b "$E/loader/entries/gb-a.conf" | cmp -s - "$E/loader/entries/gb-b.conf"; ok $? "S3b output correct"
clean "$W/s3b/err"; ok $? "S3b no sanitizer report"

# ---- S4: mutation proof - the pre-Wave-2 select_entry under ASan on the S1 fixture
if [ -f "$S/gb_slotsync.old.c" ]; then
  M=$W/s4; mkdir -p "$M/shim/sys"
  printf '%s\n' '#define PROP_VALUE_MAX 92' \
    'static inline int __system_property_get(const char *n, char *v) { (void)n; v[0] = 0; return 0; }' \
    > "$M/shim/sys/system_properties.h"
  sed 's|^#define MNT "/mnt/gb_slotsync"|#define MNT "esp"|' "$S/gb_slotsync.old.c" > "$M/old_mut.c"
  printf '%s\n' '#define main old_main' '#include "old_mut.c"' '#undef main' \
    'int main(int argc, char **argv) { (void)argc; if (chdir(argv[1])) return 2; return select_entry('"'b', 'a'"') ? 1 : 0; }' \
    > "$M/harness.c"
  gcc -O1 -g -fsanitize=address -I "$M/shim" -I "$M" -o "$M/old_t" "$M/harness.c" 2> "$M/build.err"
  ok $? "S4 the old source builds with ASan"
  mkesp "$M/esp" nvtest
  "$M/old_t" "$M" > "$M/out" 2> "$M/err"; rc=$?
  grep -q -E 'heap-use-after-free|attempting double-free' "$M/err"
  ok $? "S4 ASan reports heap-use-after-free or double-free on the old source (exit $rc: $(grep -m1 -o -E 'ERROR: AddressSanitizer: [a-z-]+' "$M/err"))"
else
  fail "S4 $S/gb_slotsync.old.c missing"
fi

# ---- S5: the ESP lock (fake clock: each 1 s poll takes 10 ms)
L5=$W/s5; mkdir -p "$L5"
LK=$L5/gb_esp.lock
lockrun() {   # lockrun TAG [hold_ms]: --test-lock in the foreground, output in $L5/TAG.{out,err}
  GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_HOLD_MS=${2:-0} "$T" --test-lock > "$L5/$1.out" 2> "$L5/$1.err"
}
steals() { grep -c 'stealing a stale lock' "$@" | awk -F: '{s += $NF} END {print s + 0}'; }
deadpid() { sh -c 'exit 0' & p=$!; wait "$p"; echo "$p"; }
inode() { stat -c %i "$1"; }

rm -rf "$LK"; lockrun free; rc=$?
[ $rc = 0 ] && grep -q 'lock acquired after 0 s' "$L5/free.out" && grep -q 'lock released' "$L5/free.out" && [ ! -e "$LK" ]
ok $? "S5 free lock: taken at once, released, no lock dir left"
grep -q "owner '[0-9]* gb_slotsync [0-9]*'" "$L5/free.out"; ok $? "S5 owner line is '<pid> gb_slotsync <uptime_s>'"

sleep 120 & HP=$!
mkdir "$LK"; echo "$HP gb_bootlog 41" > "$LK/owner"; I0=$(inode "$LK")
lockrun live; rc=$?
[ $rc = 3 ] && grep -q 'held over 600 s' "$L5/live.out" && grep -q 'lock not taken after 600 s' "$L5/live.out"
ok $? "S5 live holder: waits 600 fake s, then skips (exit $rc)"
[ "$(inode "$LK")" = "$I0" ] && [ "$(cat "$LK/owner")" = "$HP gb_bootlog 41" ] && [ "$(steals "$L5/live.out")" = 0 ]
ok $? "S5 live holder: its lock untouched (same inode and owner, no steal)"
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_HOLD_MS=0 "$T" --test-lock > "$L5/release.out" 2> "$L5/release.err" & TP=$!
sleep 1; rm -f "$LK/owner"; rmdir "$LK"
wait "$TP"; rc=$?
wt=$(sed -n 's/.*lock acquired after \([0-9]*\) s.*/\1/p' "$L5/release.out")
[ $rc = 0 ] && [ -n "$wt" ] && [ "$wt" -gt 0 ] && [ "$wt" -lt 600 ] && [ "$(steals "$L5/release.out")" = 0 ]
ok $? "S5 live holder releases while we wait: taken after ${wt:-?} fake s, no steal"
kill "$HP" 2> /dev/null; wait "$HP" 2> /dev/null

rm -rf "$LK"; mkdir "$LK"; echo "$(deadpid) gb_bootlog 12" > "$LK/owner"
lockrun dead; rc=$?
[ $rc = 0 ] && [ "$(steals "$L5/dead.out")" = 1 ] && grep -q 'lock acquired after 0 s' "$L5/dead.out" && [ ! -e "$LK" ]
ok $? "S5 dead pid: stolen once, taken, released"

rm -rf "$LK"; mkdir "$LK"; echo "abc gb_bootlog 12" > "$LK/owner"
lockrun nonnum; rc=$?
[ $rc = 0 ] && [ "$(steals "$L5/nonnum.out")" = 1 ]; ok $? "S5 non-numeric owner: stolen once"

rm -rf "$LK"; mkdir "$LK"
lockrun noowner; rc=$?
wt=$(sed -n 's/.*lock acquired after \([0-9]*\) s.*/\1/p' "$L5/noowner.out")
[ $rc = 0 ] && [ "$(steals "$L5/noowner.out")" = 1 ] && [ "${wt:-0}" -ge 4 ] && [ "${wt:-0}" -le 5 ]
ok $? "S5 ownerless lock: stolen after 5 polls (taken after ${wt:-?} s)"

bad=0
for it in 1 2 3 4 5; do
  rm -rf "$LK"; mkdir "$LK"; echo "$(deadpid) gb_bootlog 12" > "$LK/owner"
  GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_HOLD_MS=300 "$T" --test-lock > "$L5/two$it.a" 2>&1 & P1=$!
  GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_HOLD_MS=300 "$T" --test-lock > "$L5/two$it.b" 2>&1 & P2=$!
  wait "$P1"; r1=$?; wait "$P2"; r2=$?
  n=$(steals "$L5/two$it.a" "$L5/two$it.b")
  echo "  two instances, iteration $it: exits $r1/$r2, steals $n"
  { [ $r1 = 0 ] && [ $r2 = 0 ] && [ "$n" = 1 ] && [ ! -e "$LK" ]; } || bad=$((bad + 1))
done
ok $bad "S5 two concurrent instances against one stale lock: exactly one steal, both served in turn (5 runs)"

rm -rf "$LK"
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_HOLD_MS=1500 "$T" --test-lock > "$L5/repl.out" 2>&1 & TP=$!
i=0; while [ ! -s "$LK/owner" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
echo "1 gb_other 5" > "$LK/owner"
wait "$TP"; rc=$?
[ $rc = 0 ] && grep -q 'not ours any more at release' "$L5/repl.out" && [ "$(cat "$LK/owner" 2> /dev/null)" = "1 gb_other 5" ]
ok $? "S5 owner replaced while held: not removed at release"
rm -rf "$LK"
cat "$L5"/*.err 2> /dev/null > "$L5/all.err"; clean "$L5/all.err"; ok $? "S5 no sanitizer report"

# ---- S6: --dry-run end to end on a fake ESP dir, no OTA pending
E=$W/s6/esp; mkesp "$E" nvtest
before=$(cd "$E" && find . -type f -exec sha256sum {} + | sort)
GB_SLOTSYNC_MNT=$E GB_SLOTSYNC_LOCK=$W/s6/gb_esp.lock GB_SLOTSYNC_SLOT_SUFFIX=_a GB_SLOTSYNC_BOOT_ID=test-boot-id \
  "$T" --dry-run > "$W/s6/out" 2> "$W/s6/err"; rc=$?
after=$(cd "$E" && find . -type f -exec sha256sum {} + | sort)
ok $rc "S6 --dry-run exits 0"
[ "$before" = "$after" ]; ok $? "S6 nothing written on the fake ESP"
grep -q 'test: lock acquired' "$W/s6/out" && grep -q 'test: lock released' "$W/s6/out" && [ ! -e "$W/s6/gb_esp.lock" ]
ok $? "S6 lock taken and released"
awk '/test: lock acquired/{a=NR} /test: mount/{m=NR} /test: umount/{u=NR} /test: lock released/{r=NR}
     END {exit !(a && a < m && m < u && u < r)}' "$W/s6/out"
ok $? "S6 order: lock < mount < umount < unlock"
grep -q 'test: mount .*flush' "$W/s6/out"; ok $? "S6 the ESP mount uses flush"
grep -q 'test: sync_once rc=0 settled=a' "$W/s6/out"; ok $? "S6 settled = a"
clean "$W/s6/err"; ok $? "S6 no sanitizer report"

# ---- S7: the NDK binary
N=$W/ndk; mkdir -p "$N"
"$NDK_CC" --target=x86_64-linux-android34 -O2 -Wall -Wextra -Werror -o "$N/gb_slotsync" "$S/gb_slotsync.c" 2> "$N/build.err"
ok $? "S7 NDK clang $("$NDK_CC" --version | head -n 1 | sed 's/.*clang version \([0-9.]*\).*/\1/') builds with -Wall -Wextra -Werror"
cat "$N/build.err"
need=$(readelf -d "$N/gb_slotsync" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p' | sort | tr '\n' ' ')
echo "  NEEDED: $need"
bad=0; for l in $need; do case $l in libc.so | libdl.so | libm.so) ;; *) bad=1 ;; esac; done
[ -n "$need" ] && [ $bad = 0 ]; ok $? "S7 NEEDED only bionic libraries"
readelf -l "$N/gb_slotsync" | grep -q '/system/bin/linker64'; ok $? "S7 interpreter /system/bin/linker64"
! strings -a "$N/gb_slotsync" | grep -q -E 'GB_SLOTSYNC_TEST|GB_SLOTSYNC_|--test-|test: '
ok $? "S7 no test hooks in the binary (no GB_SLOTSYNC_*, --test-, 'test: ' strings)"
strings -a "$N/gb_slotsync" | grep -q 'gb_slotsync %ld' && strings -a "$N/gb_slotsync" | grep -q -- '-nvtest'
ok $? "S7 the binary has the lock owner format and the -nvtest copy"
! strings -a "$N/gb_slotsync" | grep -q -- '-gfxtest'; ok $? "S7 no -gfxtest"
echo "  $(sha256sum "$N/gb_slotsync" | cut -c1-64) $(stat -c %s "$N/gb_slotsync") B"

if [ "$FAILS" = 0 ]; then echo "TB-1 RESULT: OK"; else echo "TB-1 RESULT: $FAILS failure(s)"; exit 1; fi
