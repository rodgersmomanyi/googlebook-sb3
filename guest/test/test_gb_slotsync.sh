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
#   S8 (Wave 2b) the lock r3: steal-in-progress back-off, exclusive owner, release wait, put-back without replacing
#   S8b (Wave 3, INTERFACES 5.4 r4, wave 2b review L1/L2): no mkdir while a steal is in progress, only consecutive
#       back-off polls count, a lock naming our own pid is stale, release waits 7 polls, put-back retried at 50 ms
#   S9 (Wave 2b) the loader.conf default as a whole line (not gb-b-nvtest, not #default, CR ignored, every line)
#   S10 (Wave 3 item 13, lead decision D-a) the kernel check before a switch, end to end on fake boot/init_boot/
#       vendor_boot images (--test-ticks): same release -> switch; same KMI, other GKI build -> switch (logged);
#       other KMI -> HOLD (initrd + entries written, loader.conf untouched, one HOLD line and one gb_update
#       notification over 3 ticks); truncated / garbage boot_b -> HOLD, no crash; gb/gb.cfg + gb/overlay survive
#       (WAVE3 contract 2.3); no OTA pending -> unchanged; KMI parser and boot-header reader vectors
#   S12 (Wave 3 fix phase, safety review MEDIUM) the HOLD notification only after the held slot's initrd + entries
#       are written; a rebuild removes the slot's older entries + stamp first; on later boots the non-running slot's
#       entries are removed when its stamp no longer matches or it is not bootable (never before the running slot is
#       the default; not in --dry-run); a HOLD still in effect is logged again
#   S11 (Wave 3 item 7) the wanted_slot truth table (block wanted_slot-vectors below; install_dualboot.ps1's
#       Get-GbWantedSlot runs the same block, dualboot test\test_wanted_slot.ps1)
#   S13 (Wave 8, SLOT-DEFAULT) systemd-boot 262.1 matches "default" against the entry id incl. ".conf": the matcher
#       (--test-loader-default, both modes), every write is "default gb-<x>.conf", an old "default gb-<x>" line is
#       rewritten once to the running slot (the user VM's slot-b ESP, a switch, a HOLD), a "preferred" line is
#       removed, the [NVIDIA test] entry is never the default, --dry-run writes nothing
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
  printf 'default gb-a.conf\ntimeout 4\nconsole-mode max\neditor yes\n' > "$1/loader/loader.conf"
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
printf 'default gb-b.conf\ntimeout 4\nconsole-mode max\neditor yes\n' | cmp -s - "$E/loader/loader.conf"; ok $? "S1 loader.conf default -> gb-b.conf, rest unchanged"
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

# ---- S8: the ESP lock r3 (INTERFACES 5.4, Wave 2b: safety finding 4)
sleep 120 & HP=$!
rm -rf "$LK" "$LK".stale.*; mkdir "$LK.stale.$HP"
lockrun busy; rc=$?
[ $rc = 0 ] && grep -q 'lock acquired after 5 s' "$L5/busy.out" && [ "$(grep -c 'a steal is in progress: backing off' "$L5/busy.out")" = 1 ] &&
  [ -d "$LK.stale.$HP" ] && [ ! -e "$LK" ]
ok $? "S8 steal in progress (live .stale.<pid>): 5 back-offs, then taken (5 fake s), the stealer's directory untouched"
rm -rf "$LK".stale.*; mkdir "$LK.stale.$(deadpid)"
lockrun busydead; rc=$?
[ $rc = 0 ] && grep -q 'lock acquired after 0 s' "$L5/busydead.out" && ! grep -q 'backing off' "$L5/busydead.out"
ok $? "S8 a dead stealer's leftover: not a steal in progress, taken at once"
rm -rf "$LK" "$LK".stale.*
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_RACE_OWNER="$(deadpid) gb_bootlog 3" "$T" --test-lock > "$L5/race.out" 2> "$L5/race.err"; rc=$?
[ $rc = 0 ] && grep -q 'another lock took the place of ours' "$L5/race.out" && [ "$(steals "$L5/race.out")" = 1 ] &&
  grep -q 'lock acquired after 1 s' "$L5/race.out" && [ ! -e "$LK" ]
ok $? "S8 an owner file in our new directory (EEXIST): not ours, never overwritten (stolen once as stale next poll), taken"
rm -rf "$LK"
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_DISPLACE="$LK.stale.$HP" GB_SLOTSYNC_TEST_PUTBACK="$LK.stale.$HP" \
  GB_SLOTSYNC_TEST_PUTBACK_POLLS=2 "$T" --test-lock > "$L5/rel.out" 2> "$L5/rel.err"; rc=$?
[ $rc = 0 ] && grep -q 'put back after 2 polls' "$L5/rel.out" && grep -q 'test: lock released' "$L5/rel.out" &&
  ! grep -q 'not ours' "$L5/rel.out" && [ ! -e "$LK" ] && [ ! -e "$LK.stale.$HP" ]
ok $? "S8 release while a stealer has our lock moved away: waited for the put-back (2 polls), then released"
rm -rf "$LK" "$LK".stale.*
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_DISPLACE="$LK.stale.$HP" "$T" --test-lock > "$L5/rel2.out" 2> "$L5/rel2.err"; rc=$?
[ $rc = 0 ] && grep -q 'not ours any more at release' "$L5/rel2.out" && ! grep -q 'test: lock released' "$L5/rel2.out" &&
  [ -d "$LK.stale.$HP" ] && grep -q 'test: release gave up after 7 polls' "$L5/rel2.out"
ok $? "S8 never put back: after 7 polls (r4: longer than the 5 s put-back budget) left alone (logged)"
rm -rf "$LK" "$LK".stale.*
mkdir "$LK"; echo "$(deadpid) gb_bootlog 12" > "$LK/owner"
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_STEAL_RACE="$HP gb_bootlog 30" GB_SLOTSYNC_TEST_INTRUDE_POLLS=2 \
  "$T" --test-lock > "$L5/pb.out" 2> "$L5/pb.err"; rc=$?
[ $rc = 3 ] && grep -q 'the lock changed while stealing it (owner .*): put back' "$L5/pb.out" &&
  [ "$(cat "$LK/owner" 2> /dev/null)" = "$HP gb_bootlog 30" ] && [ "$(steals "$L5/pb.out")" = 0 ] &&
  [ -z "$(find "$L5" -maxdepth 1 -name 'gb_esp.lock.stale.*')" ]
ok $? "S8 the steal moved a live lock, a newcomer in the way: put back after it backed off (never replaced), then waited"
rm -rf "$LK" "$LK".stale.*
mkdir "$LK"; echo "$(deadpid) gb_bootlog 12" > "$LK/owner"
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_STEAL_RACE=- GB_SLOTSYNC_TEST_INTRUDE_POLLS=99 \
  "$T" --test-lock > "$L5/pbo.out" 2> "$L5/pbo.err"; rc=$?
[ $rc = 0 ] && grep -q 'the moved directory had no owner and another is in place: removed' "$L5/pbo.out" &&
  grep -q 'lock acquired after' "$L5/pbo.out" && [ ! -e "$LK" ] && [ -z "$(find "$L5" -maxdepth 1 -name 'gb_esp.lock.stale.*')" ]
ok $? "S8 an ownerless moved directory with a newcomer in the way: removed; the newcomer's empty lock stolen later, taken"

# ---- S8b: lock r4 (INTERFACES 5.4 r4; wave 2b review L1, L2)
rm -rf "$LK" "$LK".stale.*; mkdir "$LK.stale.$HP"
lockrun busy4; rc=$?
[ $rc = 0 ] && [ "$(grep -c '^test: mkdir lock$' "$L5/busy4.out")" = 1 ] && grep -q 'lock acquired after 5 s' "$L5/busy4.out"
ok $? "S8b L1 steal in progress: no mkdir during the 5 back-offs (mkdir calls: $(grep -c '^test: mkdir lock$' "$L5/busy4.out")), then one, taken"
sleep 120 & HP2=$!
rm -rf "$LK" "$LK".stale.*; mkdir "$LK.stale.$HP" "$LK"; echo "$HP2 gb_nouveau 9" > "$LK/owner"
lockrun consec; rc=$?
nm=$(grep -c '^test: mkdir lock$' "$L5/consec.out")
[ $rc = 3 ] && [ "$nm" -ge 95 ] && [ "$nm" -le 105 ] && [ "$(steals "$L5/consec.out")" = 0 ] &&
  [ "$(cat "$LK/owner")" = "$HP2 gb_nouveau 9" ]
ok $? "S8b L1 only consecutive back-offs count: live holder + live stealer leftover over 600 fake s -> mkdir every 6th poll ($nm), no steal"
kill "$HP2" 2> /dev/null; wait "$HP2" 2> /dev/null
rm -rf "$LK" "$LK".stale.*
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_OWN_ORPHAN=1 "$T" --test-lock > "$L5/orph.out" 2> "$L5/orph.err"; rc=$?
[ $rc = 0 ] && [ "$(steals "$L5/orph.out")" = 1 ] && grep -q 'lock acquired after 0 s' "$L5/orph.out" && [ ! -e "$LK" ]
ok $? "S8b L2 a lock naming our own pid while we hold nothing (a late put-back): stale, stolen once, taken at once"
rm -rf "$LK" "$LK".stale.*
mkdir "$LK"; echo "$(deadpid) gb_bootlog 12" > "$LK/owner"
GB_SLOTSYNC_LOCK=$LK GB_SLOTSYNC_TEST_STEAL_RACE="$HP gb_bootlog 30" GB_SLOTSYNC_TEST_INTRUDE_POLLS=60 \
  "$T" --test-lock > "$L5/pb60.out" 2> "$L5/pb60.err"; rc=$?
[ $rc = 3 ] && grep -q 'the lock changed while stealing it (owner .*): put back' "$L5/pb60.out" &&
  [ "$(cat "$LK/owner" 2> /dev/null)" = "$HP gb_bootlog 30" ]
ok $? "S8b L1 put-back retried at 50 ms: a newcomer in the way for 60 tries (3 s) - still put back (r3 gave up after 5)"
kill "$HP" 2> /dev/null; wait "$HP" 2> /dev/null
rm -rf "$LK" "$LK".stale.*

# ---- S9: loader.conf default as a whole line (Wave 2b: safety finding 5), select_entry b a; (wave 8, SLOT-DEFAULT)
#      the line names the entry id with ".conf"; the old form, a glob, the nvtest id and "preferred" are rewritten
for v in nvtest:'default gb-b-nvtest' nvconf:'default gb-b-nvtest.conf' hash:'#default gb-b.conf' \
         crlf:'default gb-b.conf\r' last:'default gb-b.conf\ndefault gb-a.conf' ok:'default gb-b.conf' \
         old:'default gb-b' oldcr:'default gb-b\r' glob:'default gb-b*' upper:'default GB-B.CONF' \
         lead:'  default gb-b.conf' pref:'default gb-b.conf\npreferred gb-a.conf' preflead:'\tpreferred gb-b.conf'; do
  k=${v%%:*}; line=${v#*:}
  E=$W/s9-$k/esp; mkesp "$E" nvtest
  printf "$line"'\ntimeout 4\neditor yes\n' > "$E/loader/loader.conf"
  before=$(sha256sum < "$E/loader/loader.conf")
  GB_SLOTSYNC_MNT=$E "$T" --test-copy-entry b a > "$W/s9-$k/out" 2> "$W/s9-$k/err"; rc=$?
  after=$(sha256sum < "$E/loader/loader.conf")
  case $k in
    nvtest|nvconf|old|glob|upper|pref) printf 'default gb-b.conf\ntimeout 4\neditor yes\n' | cmp -s - "$E/loader/loader.conf"; r=$? ;;
    hash) printf '#default gb-b.conf\ntimeout 4\neditor yes\ndefault gb-b.conf\n' | cmp -s - "$E/loader/loader.conf"; r=$? ;;
    preflead) printf 'timeout 4\neditor yes\ndefault gb-b.conf\n' | cmp -s - "$E/loader/loader.conf"; r=$? ;;
    crlf|ok|lead) [ "$before" = "$after" ]; r=$? ;;
    last) printf 'default gb-b.conf\ndefault gb-b.conf\ntimeout 4\neditor yes\n' | cmp -s - "$E/loader/loader.conf"; r=$? ;;
    oldcr) printf 'default gb-b.conf\r\ntimeout 4\neditor yes\n' | cmp -s - "$E/loader/loader.conf"; r=$? ;;
  esac
  case $k in pref|preflead) grep -q "systemd-boot default -> gb-b.conf ('preferred' line removed)" "$W/s9-$k/out" || r=1 ;; esac
  [ $rc = 0 ] && [ $r = 0 ] && clean "$W/s9-$k/err"
  ok $? "S9 loader.conf '$line' -> $(tr '\r\n\t' '^|>' < "$E/loader/loader.conf")"
done
cat "$L5"/*.err 2> /dev/null > "$L5/all.err"; clean "$L5/all.err"; ok $? "S5 no sanitizer report"

# ---- S10: the kernel check before a switch (item 13, D-a), end to end with --test-ticks on a fake ESP + by-name dir
REL_A='6.18.21-android17-5-g3938c8d97142-ab16302624'          # lapis .271/.276 boot_a/boot_b (both slots)
RUN='6.18.21-android17-5-g9629ced3e50e-ab16302714'            # our k14 (uname)
mkboot() {   # mkboot FILE RELEASE|-truncated|-garbage: boot header v4 + a bzImage setup header with the version string
  python3 - "$1" "$2" <<'PY'
import os, struct, sys
f, rel = sys.argv[1], sys.argv[2]
if rel == "-truncated":
    open(f, "wb").write(b"ANDROID!" + b"\0" * 92); sys.exit(0)
if rel == "-garbage":
    open(f, "wb").write(os.urandom(65536)); sys.exit(0)
ksz = 1 << 20
hdr = bytearray(4096); hdr[0:8] = b"ANDROID!"; struct.pack_into("<II", hdr, 8, ksz, 0); struct.pack_into("<I", hdr, 40, 4)
k = bytearray(65536)
k[0x202:0x206] = b"HdrS"; ptr = 0x3c40; struct.pack_into("<H", k, 0x20E, ptr)
s = (rel + " (kleaf@build-host) #1 SMP PREEMPT Thu Jan  1 00:00:00 UTC 1970").encode() + b"\0"
k[0x200 + ptr:0x200 + ptr + len(s)] = s
open(f, "wb").write(bytes(hdr) + bytes(k) + b"\0" * (2 << 20))
PY
}
mkramdisks() {   # mkramdisks BYNAME_DIR SLOT: minimal init_boot (v4) and vendor_boot (v4) images build_slot accepts
  python3 - "$1" "$2" <<'PY'
import struct, sys
d, s = sys.argv[1], sys.argv[2]
rd = b"generic-ramdisk-" + s.encode() * 100
h = bytearray(4096); h[0:8] = b"ANDROID!"; struct.pack_into("<II", h, 8, 0, len(rd)); struct.pack_into("<I", h, 40, 4)
open(f"{d}/init_boot_{s}", "wb").write(bytes(h) + rd + b"\0" * (4096 - len(rd) % 4096))
vr, bc, page = b"vendor-ramdisk-" + s.encode() * 50, b"androidboot.hardware = android-desktop\n", 4096
v = bytearray(page); v[0:8] = b"VNDRBOOT"; struct.pack_into("<II", v, 8, 4, page); struct.pack_into("<I", v, 24, len(vr))
struct.pack_into("<II", v, 2096, 2128, 0); struct.pack_into("<I", v, 2112, 0); struct.pack_into("<I", v, 2124, len(bc))
pad = lambda b: b + b"\0" * (-len(b) % page)
open(f"{d}/vendor_boot_{s}", "wb").write(bytes(v) + pad(vr) + pad(bc))
PY
}
s10() {   # s10 TAG BOOT_B [extra env assignments...]: fresh fake device, 3 ticks; output in $W/s10-TAG/out
  X=$W/s10-$1; rm -rf "$X"; mkdir -p "$X/bn" "$X/notify" "$X/ue"
  E=$X/esp; mkesp "$E" nvtest
  printf 'gzip-cpio' > "$E/gb/inject.gz"; printf 'androidboot.gb = 1\n' > "$E/gb/bootconfig_extra"
  printf '# gb.cfg v1\nGB_PROP_1=debug.gb.x=1\n' > "$E/gb/gb.cfg"; mkdir -p "$E/gb/overlay/usr/idc"
  printf 'touch.deviceType = pointer\n' > "$E/gb/overlay/usr/idc/x.idc"
  mkboot "$X/bn/boot_a" "$REL_A"; mkboot "$X/bn/boot_b" "$2"; mkramdisks "$X/bn" b
  echo test-boot-id > "$X/ue/done"
  (cd "$E" && find . -type f -exec sha256sum {} + | sort) > "$X/before"
  tag=$1; shift 2
  env GB_SLOTSYNC_MNT="$E" GB_SLOTSYNC_LOCK="$X/gb_esp.lock" GB_SLOTSYNC_SLOT_SUFFIX=_a GB_SLOTSYNC_BOOT_ID=test-boot-id \
    GB_SLOTSYNC_BYNAME="$X/bn" GB_SLOTSYNC_UE_PREFS="$X/ue/done" GB_SLOTSYNC_NOTIFY="$X/notify" \
    GB_SLOTSYNC_TEST_ATTRS='010e000000000000 007f000000000000' GB_SLOTSYNC_TEST_UNAME="$RUN" "$@" \
    "$T" --test-ticks 3 > "$X/out" 2> "$X/err"
  echo $? > "$X/rc"
  (cd "$E" && find . -type f -exec sha256sum {} + | sort) > "$X/after"
  echo "  s10-$tag: $(grep -c . "$X/out") output lines, rc $(cat "$X/rc")"
}
lc() { grep -c -F -- "$2" "$W/s10-$1/out"; }
same() { grep -q -F " ./$2" "$W/s10-$1/before" &&
         [ "$(grep -F " ./$2" "$W/s10-$1/before")" = "$(grep -F " ./$2" "$W/s10-$1/after")" ]; }
default_is() { grep -q -x "default gb-$2.conf" "$W/s10-$1/esp/loader/loader.conf"; }
bcfg() { same "$1" gb/gb.cfg && same "$1" gb/overlay/usr/idc/x.idc; }
nnote() { find "$W/s10-$1/notify" -type f | wc -l; }

s10 same "$REL_A"
[ "$(cat "$W/s10-same/rc")" = 0 ] && default_is same b && [ -f "$W/s10-same/esp/initrd-b.img" ] &&
  [ -f "$W/s10-same/esp/loader/entries/gb-b.conf" ] && [ "$(lc same 'kernel check passed (GKI unchanged')" = 1 ] &&
  [ "$(lc same 'HOLD slot')" = 0 ] && [ "$(nnote same)" = 0 ] && bcfg same && clean "$W/s10-same/err"
ok $? "S10 same release in boot_b: switched (default gb-b.conf, initrd-b + gb-b.conf), logged once, no notification, gb.cfg/overlay untouched"

s10 gki "6.18.30-android17-5-g0123456789ab-ab16302999"
[ "$(cat "$W/s10-gki/rc")" = 0 ] && default_is gki b && [ "$(lc gki "GKI changed $REL_A -> 6.18.30-android17-5-g0123456789ab-ab16302999 (same KMI 6.18-android17-5")" = 1 ] &&
  [ "$(lc gki 'HOLD slot')" = 0 ] && [ "$(nnote gki)" = 0 ] && bcfg gki && clean "$W/s10-gki/err"
ok $? "S10 D-a same KMI, other GKI sublevel/build: switched (not held), the change logged once, no notification"

s10 kmi "6.18.30-android17-6-g0123456789ab-ab16400000"
X=$W/s10-kmi
hl=$(lc kmi 'gb_slotsync: HOLD slot b: KMI 6.18-android17-6 != running 6.18-android17-5; default stays gb-a')
nf=$(find "$X/notify" -type f -name '[0-9]*-[0-9]*-[0-9]*.msg'); nf1=$(echo "$nf" | head -n 1)
txt=$(sed -n 's/^text=//p' "$nf1" 2> /dev/null)
[ "$(cat "$X/rc")" = 0 ] && [ "$hl" = 1 ] && default_is kmi a && same kmi loader/loader.conf &&
  [ -f "$X/esp/initrd-b.img" ] && [ -f "$X/esp/loader/entries/gb-b.conf" ] && [ -f "$X/esp/loader/entries/gb-b-nvtest.conf" ] &&
  [ "$(nnote kmi)" = 1 ] && [ "$(echo "$nf" | grep -c .)" = 1 ] && [ "$(lc kmi 'test: setprop vendor.gb.notify ')" = 1 ] &&
  grep -q -x 'kind=notify' "$nf1" && grep -q -x 'tag=gb_update' "$nf1" && grep -q -x 'title=System update held' "$nf1" &&
  [ ${#txt} -le 200 ] && [ -z "$(printf '%s' "$txt" | tr -d ' -~')" ] && case $txt in 'Kernel mismatch: '*) true;; *) false;; esac &&
  [ "$(stat -c %a "$nf1")" = 644 ] && [ "$(lc kmi 'test: sync_once rc=0 settled=- held=b ')" = 1 ] && bcfg kmi && clean "$X/err"
ok $? "S10 other KMI: HOLD - initrd-b + gb-b(-nvtest).conf written, loader.conf untouched, 1 HOLD line + 1 gb_update notification over 3 ticks"
echo "  notification text (${#txt} chars): $txt"

s10 trunc -truncated
[ "$(cat "$W/s10-trunc/rc")" = 0 ] && default_is trunc a && same trunc loader/loader.conf &&
  [ "$(lc trunc 'HOLD slot b: cannot read boot_b kernel version; default stays gb-a')" = 1 ] && [ "$(nnote trunc)" = 1 ] &&
  grep -q '^text=Kernel check failed: ' "$W"/s10-trunc/notify/*.msg && bcfg trunc && clean "$W/s10-trunc/err"
ok $? "S10 truncated boot_b: HOLD (cannot read boot_b kernel version), no crash, one notification"

s10 garbage -garbage
[ "$(cat "$W/s10-garbage/rc")" = 0 ] && default_is garbage a && [ "$(lc garbage 'HOLD slot b: cannot read boot_b kernel version')" = 1 ] &&
  bcfg garbage && clean "$W/s10-garbage/err"
ok $? "S10 garbage boot_b (64 KiB random): HOLD, no crash, no sanitizer report"

s10 noq "6.18.30-android17-6-g0123456789ab-ab16400000" GB_SLOTSYNC_NOTIFY="$W/s10-noq/absent"
[ "$(cat "$W/s10-noq/rc")" = 0 ] && default_is noq a && [ "$(lc noq 'no notification queue')" = 1 ] &&
  [ "$(lc noq 'HOLD slot b: KMI ')" = 1 ] && [ "$(lc noq 'test: setprop')" = 0 ] && clean "$W/s10-noq/err"
ok $? "S10 no notification queue (a tree without gb_notify, e.g. the VM): HOLD logged to kmsg only"

s10 nopend "6.18.30-android17-6-g0123456789ab-ab16400000" GB_SLOTSYNC_BOOT_ID=another-boot
[ "$(cat "$W/s10-nopend/rc")" = 0 ] && default_is nopend a && same nopend loader/loader.conf && [ ! -e "$W/s10-nopend/esp/initrd-b.img" ] &&
  [ "$(lc nopend 'kernel')" = 0 ] && [ "$(lc nopend 'test: sync_once rc=0 settled=a')" = 1 ] && bcfg nopend
ok $? "S10 no OTA completed in this boot: unchanged (settled a, no kernel check, nothing written for b)"

s10 dry "6.18.30-android17-6-g0123456789ab-ab16400000"
X=$W/s10-dry; rm -rf "$X/notify"/*
env GB_SLOTSYNC_MNT="$X/esp" GB_SLOTSYNC_LOCK="$X/gb_esp.lock" GB_SLOTSYNC_SLOT_SUFFIX=_a GB_SLOTSYNC_BOOT_ID=test-boot-id \
  GB_SLOTSYNC_BYNAME="$X/bn" GB_SLOTSYNC_UE_PREFS="$X/ue/done" GB_SLOTSYNC_NOTIFY="$X/notify" \
  GB_SLOTSYNC_TEST_ATTRS='010e000000000000 007f000000000000' GB_SLOTSYNC_TEST_UNAME="$RUN" "$T" --dry-run > "$X/dry.out" 2>&1
[ "$(find "$X/notify" -type f | wc -l)" = 0 ] && grep -q 'dry-run: would post the notification' "$X/dry.out"
ok $? "S10 --dry-run: the HOLD notification is not written"

v=0
for kv in "$REL_A=6.18-android17-5" "$RUN=6.18-android17-5" "6.18-android17-5=6.18-android17-5" \
          "6.1.99-android14-11-gabc-ab1=6.1-android14-11" "6.18.21-android17-5+=6.18-android17-5" \
          "6.18.21=-" "6.18.21-android17=-" "6.18.21-android17-x-g1=-" "6.18.21-android17-5x=-" "android17-5=-" "=-" \
          "6.18.21-androidX-5=-" "10.100.1-android123-45-gfff=10.100-android123-45"; do
  in=${kv%=*}; want=${kv##*=}
  got=$("$T" --test-kmi "$in" 2> /dev/null)
  [ "$got" = "$want" ] || { echo "  kmi '$in': got '$got', want '$want'"; v=$((v + 1)); }
done
ok $v "S10 KMI parser: 13 vectors (release -> <maj>.<min>-android<N>-<gen>, malformed -> none)"
X=$W/s10-gkiv; mkdir -p "$X"; mkboot "$X/boot_a" "$REL_A"; mkboot "$X/boot_b" -truncated
python3 -c "import sys; b=bytearray(open(sys.argv[1],'rb').read()); b[4096+0x202:4096+0x206]=b'XXXX'; open(sys.argv[2],'wb').write(b)" "$X/boot_a" "$X/boot_c"
python3 -c "import sys,struct; b=bytearray(open(sys.argv[1],'rb').read()); struct.pack_into('<H',b,4096+0x20E,0xff00); open(sys.argv[2],'wb').write(b)" "$X/boot_a" "$X/boot_d"
python3 -c "import sys; b=bytearray(open(sys.argv[1],'rb').read()); i=b.find(b' (kleaf'); b[i]=0x07; open(sys.argv[2],'wb').write(b)" "$X/boot_a" "$X/boot_e"
r=$(for s in a b c d e f; do GB_SLOTSYNC_BYNAME=$X "$T" --test-gki $s 2> /dev/null; done | tr '\n' ' ')
[ "$r" = "$REL_A - - - - - " ]; ok $? "S10 boot header reader: good, truncated, no HdrS, pointer past 64 KiB, non-printable end, missing -> '$r'"

# ---- S12 (wave 3 fix phase, safety review MEDIUM): the HOLD notification only after the held slot's initrd and
#      entries are on the ESP; a rebuild removes the slot's older entries first; with no switch pending the
#      non-running slot's entries are removed when they no longer match it (stamp) or it is not bootable (GPT), only
#      after the running slot is the default, nothing in --dry-run; a HOLD still in effect is logged again later.
KMI_B='6.18.30-android17-6-g0123456789ab-ab16400000'
WANT_B='010e000000000000 007f000000000000'                   # b wanted + bootable (an OTA completed into b)
B_DEAD='010e000000000000 0000000000000000'                   # b priority 0: not bootable (an OTA is writing b)
s12dev() {   # s12dev TAG BOOT_B_RELEASE: a fresh fake device as in S10 (running a), nothing run yet
  X=$W/s12-$1; rm -rf "$X"; mkdir -p "$X/bn" "$X/notify" "$X/ue"
  E=$X/esp; mkesp "$E" nvtest
  printf 'gzip-cpio' > "$E/gb/inject.gz"; printf 'androidboot.gb = 1\n' > "$E/gb/bootconfig_extra"
  printf '# gb.cfg v1\n' > "$E/gb/gb.cfg"
  mkboot "$X/bn/boot_a" "$REL_A"; mkboot "$X/bn/boot_b" "$2"; mkramdisks "$X/bn" b
  echo test-boot-id > "$X/ue/done"
}
s12run() {   # s12run TAG OUT BOOT_ID ATTRS ARGS...: one run of the test binary on $W/s12-TAG; output $X/OUT{,.err,.rc}
  X=$W/s12-$1; o=$2; b=$3; at=$4; shift 4
  env GB_SLOTSYNC_MNT="$X/esp" GB_SLOTSYNC_LOCK="$X/gb_esp.lock" GB_SLOTSYNC_SLOT_SUFFIX=_a GB_SLOTSYNC_BOOT_ID="$b" \
    GB_SLOTSYNC_BYNAME="$X/bn" GB_SLOTSYNC_UE_PREFS="$X/ue/done" GB_SLOTSYNC_NOTIFY="$X/notify" \
    GB_SLOTSYNC_TEST_ATTRS="$at" GB_SLOTSYNC_TEST_UNAME="$RUN" "$T" "$@" > "$X/$o" 2> "$X/$o.err"
  echo $? > "$X/$o.rc"
}
c12() { grep -c -F -- "$3" "$W/s12-$1/$2"; }
n12() { find "$W/s12-$1/notify" -type f | wc -l; }
tree12() { (cd "$W/s12-$1/esp" && find . -type f -exec sha256sum {} + | sort); }
flip_ib() { python3 -c "import sys; p=sys.argv[1]; b=bytearray(open(p,'rb').read()); b[4096]^=0xff; open(p,'wb').write(b)" "$1"; }

s12dev nfy "$KMI_B"; X=$W/s12-nfy; mv "$X/esp/gb/inject.gz" "$X/inject.gz.aside"
s12run nfy t1 test-boot-id "$WANT_B" --test-ticks 3
[ "$(cat "$X/t1.rc")" = 1 ] && [ "$(c12 nfy t1 'HOLD slot b: KMI 6.18-android17-6')" = 1 ] && [ "$(n12 nfy)" = 0 ] &&
  [ "$(c12 nfy t1 'test: setprop vendor.gb.notify')" = 0 ] && [ ! -e "$X/esp/loader/entries/gb-b.conf" ] &&
  [ "$(c12 nfy t1 'ESP lacks /gb/inject.gz')" = 3 ] && clean "$X/t1.err"
ok $? "S12 HOLD whose rebuild fails (3 ticks): HOLD logged once, no notification, no gb-b entry"
mv "$X/inject.gz.aside" "$X/esp/gb/inject.gz"
s12run nfy t2 test-boot-id "$WANT_B" --test-ticks 2
nf=$(find "$X/notify" -type f -name '*.msg' | head -n 1)
[ "$(cat "$X/t2.rc")" = 0 ] && [ "$(n12 nfy)" = 1 ] && [ -f "$X/esp/loader/entries/gb-b.conf" ] && [ -f "$X/esp/initrd-b.img" ] &&
  grep -q -x 'tag=gb_update' "$nf" && grep -q '^text=Kernel mismatch: .*(slot B) is in the boot menu\.$' "$nf" &&
  awk '/copy|written|built initrd-b/{b=NR} /test: setprop vendor.gb.notify/{n=NR} END {exit !(b && n && b < n)}' "$X/t2" &&
  clean "$X/t2.err"
ok $? "S12 the same HOLD once the ESP is complete (2 ticks): initrd + entries written, then exactly one notification"

s12dev old "$KMI_B"; X=$W/s12-old; E=$X/esp
expect_b "$E/loader/entries/gb-a.conf" > "$E/loader/entries/gb-b.conf"
expect_b "$E/loader/entries/gb-a-nvtest.conf" > "$E/loader/entries/gb-b-nvtest.conf"
printf 'deadbeef\n' > "$E/gb/slot_b.stamp"; mkdir -p "$E/initrd-b.img/x"        # the new initrd cannot be written
s12run old t1 test-boot-id "$WANT_B" --test-ticks 1
[ "$(cat "$X/t1.rc")" = 1 ] && [ ! -e "$E/loader/entries/gb-b.conf" ] && [ ! -e "$E/loader/entries/gb-b-nvtest.conf" ] &&
  [ ! -e "$E/gb/slot_b.stamp" ] && [ "$(c12 old t1 'slot b: removed its older boot entries before rebuilding initrd-b.img')" = 1 ] &&
  [ "$(c12 old t1 'writing initrd-b.img failed')" = 1 ] && [ "$(n12 old)" = 0 ] && [ ! -e "$E/initrd-b.img.tmp" ] &&
  grep -q -x 'default gb-a.conf' "$E/loader/loader.conf" && clean "$X/t1.err"
ok $? "S12 rebuild of slot b fails after an older gb-b(-nvtest).conf existed: old entries + stamp removed first, no .tmp left, no notification"
rm -rf "$E/initrd-b.img"
s12run old t2 test-boot-id "$WANT_B" --test-ticks 1
expect_b "$E/loader/entries/gb-a.conf" | cmp -s - "$E/loader/entries/gb-b.conf" && [ -s "$E/gb/slot_b.stamp" ] &&
  [ "$(cat "$E/gb/slot_b.stamp")" != deadbeef ] && [ "$(n12 old)" = 1 ] && [ "$(cat "$X/t2.rc")" = 0 ] && clean "$X/t2.err"
ok $? "S12 the retry builds initrd-b, writes gb-b.conf again from the template and a new stamp, then notifies once"

# a held slot b with current entries, then later boots (another boot id: no update pending)
s12dev held "$KMI_B"; s12run held t0 test-boot-id "$WANT_B" --test-ticks 1
H=$W/s12-held; [ -f "$H/esp/loader/entries/gb-b.conf" ] && [ -f "$H/esp/gb/slot_b.stamp" ] && [ "$(cat "$H/t0.rc")" = 0 ]
ok $? "S12 fixture: slot b held with its initrd, entries and stamp"
for t in keep stale dead dry dflt dflt2; do rm -rf "$W/s12-$t"; cp -a "$H" "$W/s12-$t"; done
s12run keep t1 next-boot "$WANT_B" --test-ticks 3
[ "$(cat "$W/s12-keep/t1.rc")" = 0 ] && [ "$(tree12 held)" = "$(tree12 keep)" ] &&
  [ "$(c12 keep t1 'gb_slotsync: HOLD slot b: KMI 6.18-android17-6 != running 6.18-android17-5; default stays gb-a')" = 1 ] &&
  [ "$(c12 keep t1 'removed')" = 0 ] && [ "$(c12 keep t1 'test: sync_once rc=0 settled=a')" = 1 ] && clean "$W/s12-keep/t1.err"
ok $? "S12 later boot, held b still current: nothing changed, the HOLD line logged again once over 3 ticks"
flip_ib "$W/s12-stale/bn/init_boot_b"
s12run stale t1 next-boot "$WANT_B" --test-ticks 2
E=$W/s12-stale/esp
[ "$(cat "$W/s12-stale/t1.rc")" = 0 ] && [ ! -e "$E/loader/entries/gb-b.conf" ] && [ ! -e "$E/loader/entries/gb-b-nvtest.conf" ] &&
  [ ! -e "$E/gb/slot_b.stamp" ] && [ -f "$E/loader/entries/gb-a.conf" ] && [ -f "$E/loader/entries/gb-a-nvtest.conf" ] &&
  [ "$(c12 stale t1 'slot b: removed its boot entries (2): its initrd does not match')" = 1 ] &&
  [ "$(c12 stale t1 'HOLD slot')" = 0 ] && grep -q -x 'default gb-a.conf' "$E/loader/loader.conf" &&
  cmp -s "$E/gb/gb.cfg" "$H/esp/gb/gb.cfg" && clean "$W/s12-stale/t1.err"
ok $? "S12 later boot, slot b's init_boot changed after its initrd was built: gb-b(-nvtest).conf + stamp removed once, slot a and gb.cfg untouched"
s12run dead t1 next-boot "$B_DEAD" --test-ticks 1
E=$W/s12-dead/esp
[ ! -e "$E/loader/entries/gb-b.conf" ] && [ "$(c12 dead t1 'slot b: removed its boot entries (2): the slot is not bootable')" = 1 ] &&
  [ "$(c12 dead t1 'HOLD slot')" = 0 ] && clean "$W/s12-dead/t1.err"
ok $? "S12 later boot, slot b not bootable by its GPT attributes (an OTA is writing it): its entries are removed"
flip_ib "$W/s12-dry/bn/init_boot_b"
before=$(tree12 dry); s12run dry t1 next-boot "$WANT_B" --dry-run; after=$(tree12 dry)
[ "$before" = "$after" ] && [ "$(c12 dry t1 'dry-run: would remove the boot entries of slot b')" = 1 ]
ok $? "S12 --dry-run with stale slot-b entries: nothing removed, the removal is reported"
flip_ib "$W/s12-dflt/bn/init_boot_b"; flip_ib "$W/s12-dflt2/bn/init_boot_b"
printf 'default gb-b\ntimeout 4\n' > "$W/s12-dflt/esp/loader/loader.conf"            # the form before wave 8
printf 'default gb-b.conf\ntimeout 4\n' > "$W/s12-dflt2/esp/loader/loader.conf"      # (wave 8) the form that matches
for t in dflt dflt2; do
  s12run $t t1 next-boot "$WANT_B" --test-ticks 1
  E=$W/s12-$t/esp
  [ ! -e "$E/loader/entries/gb-b.conf" ] && grep -q -x 'default gb-a.conf' "$E/loader/loader.conf" &&
    awk '/systemd-boot default -> gb-a.conf/{d=NR} /slot b: removed its boot entries/{r=NR} END {exit !(d && r && d < r)}' "$W/s12-$t/t1"
  ok $? "S12 stale slot-b entries while loader.conf names gb-b$([ $t = dflt2 ] && echo .conf): the running slot a is made the default (gb-a.conf) first, then they are removed"
done

# ---- S11: the wanted_slot truth table (also run by install_dualboot.ps1's Get-GbWantedSlot, test_wanted_slot.ps1)
# Columns: GPT attributes of vbmeta_a and vbmeta_b (hex), wanted slot (a, b, - = none), ambiguous (1 = both bootable
# with the same priority: gb_slotsync takes a, install_dualboot.ps1 -UpdateEspOnly refuses). Bits: priority 48-51,
# tries 52-55, successful 56.
# @@wanted_slot-vectors-begin
# 0000000000000000 0000000000000000 - 0
# 010f000000000000 0000000000000000 a 0
# 0000000000000000 010f000000000000 b 0
# 010f000000000000 010e000000000000 a 0
# 010e000000000000 010f000000000000 b 0
# 010f000000000000 010f000000000000 a 1
# 007f000000000000 010e000000000000 a 0
# 000f000000000000 010e000000000000 b 0
# 000f000000000000 000f000000000000 - 0
# 0100000000000000 0100000000000000 - 0
# 001f000000000000 001e000000000000 a 0
# 001f000000000000 001f000000000000 a 1
# 010e000000000000 007f000000000000 b 0
# 01ff000000000000 010f000000000000 a 1
# 00f1000000000000 0000000000000000 a 0
# 0ef0000000000000 0000000000000000 - 0
# 010f00000000ffff 0000000000000000 a 0
# f10f000000000000 0000000000000000 a 0
# 0001000000000000 0002000000000000 - 0
# 0102000000000000 0101000000000000 a 0
# @@wanted_slot-vectors-end
v=0; nv=0
sed -n '/^# @@wanted_slot-vectors-begin$/,/^# @@wanted_slot-vectors-end$/p' "$0" | grep -E '^# [0-9a-f]{16} ' > "$W/wsv.txt"
while read -r _ a b want amb; do
  nv=$((nv + 1)); got=$("$T" --test-wanted-slot "$a" "$b")
  [ "$got" = "$want" ] || { echo "  wanted_slot($a, $b) = '$got', want '$want'"; v=$((v + 1)); }
done < "$W/wsv.txt"
[ "$nv" -ge 20 ]; ok $(( v + $? )) "S11 wanted_slot truth table: $nv vectors (shared with install_dualboot.ps1)"

# ---- S13 (wave 8, SLOT-DEFAULT): systemd-boot 262.1 matches "default"/"preferred" against the entry id, which includes
#      ".conf" (w8prep\lows-b F5A_DESIGN 1.2/1.3: "default gb-a" started gb-b.conf, "default gb-a.conf" started gb-a.conf)
# S13a the matcher, mode 0 (every default line is exactly gb-<s>.conf, no preferred line) and mode 1 (any default line
#      names gb-<s>.conf or the old gb-<s>: the dangling-default guard of check_other)
X=$W/s13a; mkdir -p "$X/esp/loader"; v=0; nv=0
while IFS='|' read -r conf s m want; do
  [ -n "$conf" ] || continue
  nv=$((nv + 1)); printf "$conf" > "$X/esp/loader/loader.conf"
  got=$(GB_SLOTSYNC_MNT="$X/esp" "$T" --test-loader-default "$s" "$m" 2> "$X/err")
  [ "$got" = "$want" ] && clean "$X/err" || { echo "  loader_default('$conf', $s, $m) = '$got', want '$want'"; v=$((v + 1)); }
done <<'VEC'
default gb-a.conf\n|a|0|1
default gb-a.conf\r\ntimeout 3\n|a|0|1
default gb-a.conf  \t\n|a|0|1
  default gb-a.conf\n|a|0|1
default\tgb-a.conf\n|a|0|1
default gb-a\n|a|0|0
default gb-a\n|a|1|1
default gb-a.conf\n|a|1|1
default gb-b.conf\n|a|0|0
default gb-b.conf\n|a|1|0
default gb-b\n|a|1|0
default gb-a-nvtest.conf\n|a|0|0
default gb-a-nvtest.conf\n|a|1|0
default gb-a-nvtest\n|a|1|0
default gb-a.confx\n|a|1|0
default gb-a.conf\ndefault gb-b.conf\n|a|0|0
default gb-a.conf\ndefault gb-b.conf\n|a|1|1
default gb-a.conf\npreferred gb-a.conf\n|a|0|0
default gb-a.conf\npreferred gb-b.conf\n|a|0|0
default gb-a.conf\n  preferred gb-b.conf\n|a|0|0
default gb-a.conf\npreferredx gb-b.conf\n|a|0|1
default gb-a.conf\n#preferred gb-b.conf\n|a|0|1
#default gb-a.conf\n|a|0|0
#default gb-a.conf\n|a|1|0
defaultx gb-a.conf\n|a|1|0
timeout 3\n|a|0|0
default\n|a|0|0
default gb-*\n|a|0|0
default GB-A.CONF\n|a|0|0
default gb-b.conf\n|b|0|1
default gb-b\n|b|1|1
VEC
[ "$nv" -ge 30 ]; ok $(( v + $? )) "S13a loader_default(): $nv vectors (mode 0 only gb-<s>.conf and no preferred; mode 1 both forms)"

s13run() {   # s13run TAG SUFFIX BOOT_ID ATTRS ARGS...: one run on $W/s13-TAG (fake device as S12); output $X/out{,.err,.rc}
  X=$W/s13-$1; sfx=$2; b=$3; at=$4; shift 4
  env GB_SLOTSYNC_MNT="$X/esp" GB_SLOTSYNC_LOCK="$X/gb_esp.lock" GB_SLOTSYNC_SLOT_SUFFIX="$sfx" GB_SLOTSYNC_BOOT_ID="$b" \
    GB_SLOTSYNC_BYNAME="$X/bn" GB_SLOTSYNC_UE_PREFS="$X/ue/done" GB_SLOTSYNC_NOTIFY="$X/notify" \
    GB_SLOTSYNC_TEST_ATTRS="$at" GB_SLOTSYNC_TEST_UNAME="$RUN" "$T" "$@" > "$X/out" 2> "$X/out.err"
  echo $? > "$X/out.rc"
}
s13dev() {   # s13dev TAG LOADER_CONF_TEXT [b]: a fake device whose ESP has slot a's entries (or slot b's with "b")
  X=$W/s13-$1; rm -rf "$X"; mkdir -p "$X/bn" "$X/notify" "$X/ue"
  E=$X/esp; mkesp "$E" nvtest
  if [ "${3:-}" = b ]; then
    for f in "" -nvtest; do expect_b "$E/loader/entries/gb-a$f.conf" > "$E/loader/entries/gb-b$f.conf"; rm "$E/loader/entries/gb-a$f.conf"; done
    mv "$E/initrd-a.img" "$E/initrd-b.img"
  fi
  printf "$2" > "$E/loader/loader.conf"
  printf 'gzip-cpio' > "$E/gb/inject.gz"; printf 'androidboot.gb = 1\n' > "$E/gb/bootconfig_extra"
  mkboot "$X/bn/boot_a" "$REL_A"; mkboot "$X/bn/boot_b" "$REL_A"; mkramdisks "$X/bn" a; mkramdisks "$X/bn" b
  echo test-boot-id > "$X/ue/done"
}
t13() { (cd "$W/s13-$1/esp" && find . -type f -exec sha256sum {} + | sort); }
A_DEAD='0000000000000000 01ff000000000000'                    # the user VM / USB image: a priority 0, b successful

# S13b the user VM today (wave-7 ESP, slot b, "default gb-b", only gb-b(-nvtest).conf), first boot with this binary:
#      the line becomes "default gb-b.conf" (still slot b), nothing else changes; the next boot (a new process) writes nothing
s13dev uvm 'default gb-b\ntimeout 3\nconsole-mode max\neditor yes\n' b
before=$(t13 uvm | grep -v ' ./loader/loader.conf$')
s13run uvm _b boot-1 "$A_DEAD" --test-ticks 2
X=$W/s13-uvm; E=$X/esp
printf 'default gb-b.conf\ntimeout 3\nconsole-mode max\neditor yes\n' | cmp -s - "$E/loader/loader.conf" &&
  [ "$before" = "$(t13 uvm | grep -v ' ./loader/loader.conf$')" ] && [ "$(cat "$X/out.rc")" = 0 ] &&
  [ "$(grep -c 'systemd-boot default -> gb-b.conf' "$X/out")" = 1 ] && clean "$X/out.err"
ok $? "S13b the user VM's slot-b ESP with 'default gb-b': rewritten once to 'default gb-b.conf' (2 ticks), every other file unchanged"
h1=$(t13 uvm); s13run uvm _b boot-2 "$A_DEAD" --test-ticks 1
[ "$h1" = "$(t13 uvm)" ] && ! grep -q 'systemd-boot default' "$X/out" && [ "$(cat "$X/out.rc")" = 0 ]
ok $? "S13b the next boot: nothing written (the .conf line is accepted as it is)"

# S13c a native ESP running a with the old line (an ESP written before wave 8 after a manual pick of slot A)
s13dev olda 'default gb-a\ntimeout 4\nconsole-mode max\neditor yes\n'
s13run olda _a boot-1 '' --test-ticks 1
E=$W/s13-olda/esp
printf 'default gb-a.conf\ntimeout 4\nconsole-mode max\neditor yes\n' | cmp -s - "$E/loader/loader.conf" &&
  [ ! -e "$E/loader/entries/gb-b.conf" ] && clean "$W/s13-olda/out.err"
ok $? "S13c running slot a with 'default gb-a': rewritten to 'default gb-a.conf' (a manual pick sticks)"

# S13d the switch b -> a (the next Google OTA after today's slot-b installs), old line on the ESP: the switch writes
#      gb-a(-nvtest).conf and then "default gb-a.conf"
s13dev sw 'default gb-b\ntimeout 3\nconsole-mode max\neditor yes\n' b
s13run sw _b test-boot-id '01ff000000000000 010e000000000000' --test-ticks 2
X=$W/s13-sw; E=$X/esp
printf 'default gb-a.conf\ntimeout 3\nconsole-mode max\neditor yes\n' | cmp -s - "$E/loader/loader.conf" &&
  [ -f "$E/loader/entries/gb-a.conf" ] && [ -f "$E/loader/entries/gb-a-nvtest.conf" ] && [ -f "$E/initrd-a.img" ] &&
  grep -q '^initrd  /initrd-a.img$' "$E/loader/entries/gb-a.conf" && [ "$(grep -c 'systemd-boot default -> gb-a.conf' "$X/out")" = 1 ] &&
  [ "$(grep -c 'kernel check passed' "$X/out")" = 1 ] && [ "$(cat "$X/out.rc")" = 0 ] && clean "$X/out.err"
ok $? "S13d update completed into a while b runs ('default gb-b'): gb-a(-nvtest).conf + initrd-a written, then 'default gb-a.conf'"

# S13e a HOLD while the old line is on the ESP: the running slot's line is made to match first, the held entries after
s13dev hold 'default gb-a\ntimeout 4\n'
mkboot "$W/s13-hold/bn/boot_b" "$KMI_B"
s13run hold _a test-boot-id "$WANT_B" --test-ticks 2
X=$W/s13-hold; E=$X/esp
printf 'default gb-a.conf\ntimeout 4\n' | cmp -s - "$E/loader/loader.conf" && [ -f "$E/loader/entries/gb-b.conf" ] &&
  [ "$(grep -c 'HOLD slot b: KMI 6.18-android17-6' "$X/out")" = 1 ] && [ "$(grep -c 'systemd-boot default -> gb-a.conf' "$X/out")" = 1 ] &&
  ! grep -q 'default -> gb-b' "$X/out" && [ "$(cat "$X/out.rc")" = 0 ] && clean "$X/out.err"
ok $? "S13e HOLD of b with 'default gb-a' on the ESP: 'default gb-a.conf' (still the running slot), gb-b.conf held in the menu"
s13dev hold2 'default gb-a.conf\ntimeout 4\n'
mkboot "$W/s13-hold2/bn/boot_b" "$KMI_B"
h1=$(sha256sum < "$W/s13-hold2/esp/loader/loader.conf")
s13run hold2 _a test-boot-id "$WANT_B" --test-ticks 1
[ "$h1" = "$(sha256sum < "$W/s13-hold2/esp/loader/loader.conf")" ] && [ -f "$W/s13-hold2/esp/loader/entries/gb-b.conf" ] &&
  ! grep -q 'systemd-boot default' "$W/s13-hold2/out"
ok $? "S13e HOLD with 'default gb-a.conf': loader.conf untouched (byte for byte)"

# S13f a 'preferred' line (would override the default): removed when the running slot is made the default
s13dev pref 'default gb-a.conf\npreferred gb-b.conf\ntimeout 4\n'
s13run pref _a boot-1 '' --test-ticks 1
printf 'default gb-a.conf\ntimeout 4\n' | cmp -s - "$W/s13-pref/esp/loader/loader.conf" &&
  grep -q "systemd-boot default -> gb-a.conf ('preferred' line removed)" "$W/s13-pref/out"
ok $? "S13f 'preferred gb-b.conf' while a runs: removed, 'default gb-a.conf' kept"

# S13g the [NVIDIA test] entry is never the default: a default naming it is replaced by the running slot's entry
s13dev nv 'default gb-a-nvtest.conf\n'
s13run nv _a boot-1 '' --test-ticks 1
printf 'default gb-a.conf\n' | cmp -s - "$W/s13-nv/esp/loader/loader.conf"
ok $? "S13g 'default gb-a-nvtest.conf' -> 'default gb-a.conf'"

# S13h --dry-run on the old line: the rewrite is reported, nothing written
s13dev dry 'default gb-a\n'
before=$(t13 dry); s13run dry _a boot-1 '' --dry-run
[ "$before" = "$(t13 dry)" ] && grep -q 'systemd-boot default -> gb-a.conf' "$W/s13-dry/out"
ok $? "S13h --dry-run with 'default gb-a': nothing written, the rewrite to gb-a.conf reported"

# S13i a switch whose entry cannot be written (no template gb-b.conf): loader.conf unchanged (never names a missing entry)
s13dev noent 'default gb-b.conf\ntimeout 3\n' b
rm "$W/s13-noent/esp/loader/entries/gb-b.conf"
h1=$(sha256sum < "$W/s13-noent/esp/loader/loader.conf")
s13run noent _b test-boot-id '01ff000000000000 010e000000000000' --test-ticks 1
[ "$h1" = "$(sha256sum < "$W/s13-noent/esp/loader/loader.conf")" ] && [ ! -e "$W/s13-noent/esp/loader/entries/gb-a.conf" ] &&
  [ "$(cat "$W/s13-noent/out.rc")" = 1 ] && grep -q 'no template entry' "$W/s13-noent/out" && clean "$W/s13-noent/out.err"
ok $? "S13i switch to a without a template entry: fails, loader.conf unchanged (the default never names a missing entry)"

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
strings -a "$N/gb_slotsync" | grep -q -x 'default gb-%c.conf' && ! strings -a "$N/gb_slotsync" | grep -q -x 'default gb-%c'
ok $? "S7 (wave 8) the binary writes 'default gb-%c.conf' (never the old 'default gb-%c')"
echo "  $(sha256sum "$N/gb_slotsync" | cut -c1-64) $(stat -c %s "$N/gb_slotsync") B"

if [ "$FAILS" = 0 ]; then echo "TB-1 RESULT: OK"; else echo "TB-1 RESULT: $FAILS failure(s)"; exit 1; fi
