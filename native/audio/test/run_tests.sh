#!/bin/sh
# run_tests.sh -- TB-2 of WAVE2_PLAN_v2 B-02 (gb_audiodiag.sh, gb_audio.sh), run on the builder (Ubuntu with
# mksh, the shellcheck tool, toybox and busybox).
#
#   sh run_tests.sh TREE_DIR WORK_DIR
#
# TREE_DIR holds gb_audiodiag.sh and gb_audio.sh. WORK_DIR is created empty (rm -rf first). The scripts run under
# mksh twice: with GNU coreutils, then with toybox applets first in PATH (Android's userland; busybox tr stands in,
# as in nouveau-policy's run_tests.sh). PATH shims: sleep (fake clock, 10 ms per second), mount/umount/sync/df,
# getprop/setprop, dmesg/logcat/dumpsys and cp (event log). Cases:
#   A1 ESP lock held by a live pid: waits, copies after the release      A2 600 fake s: no copy, files kept, kmsg line
#   A3 the ESP mount options contain rw,flush                            A4 the ESP is mounted only for the copy
#   A5 below the 128 MiB floor: no copy, files kept                      A6 gb_audio.sh driver_override (fake sysfs)
#   A7 at most 3 older local collection directories are kept
#   A8 the collection base is a symlink: replaced by a 0700 directory, the link target untouched
#   A9 the collection base has the wrong mode (or is a file): removed and re-created 0700, planted files gone
#   A10 a symlink inside the collection directory is never copied to the ESP (its target untouched)
#   A11 the collection base cannot be created (missing parent): nothing collected, no ESP mount, exit 0
#   Wave 2b (INTERFACES 5.4 r3): A12 a steal in progress -> back-off, A13 an owner file in our new lock directory is
#   never overwritten, A14 df without a number -> fail closed, A15 a CRLF bootcount; A6 also the legacy load with
#   PCI autoprobe off (variants: another driver registered meanwhile, no autoprobe switch)
#   Wave 3: A16 r4 no mkdir while a steal is in progress, A17 r4 a lock naming our own pid is stale (INTERFACES 5.4
#   r4); X1-X3 (item 2) the DRAS files: <soundcard> = the card id, the sof-soundwire field set, CaptureMixerElem
# Prints PASS/FAIL lines and "TB-2 RESULT: OK" or "TB-2 RESULT: <n> failure(s)" (exit 0 / 1).
set -u
SRC=$(cd "$1" && pwd); W=$2
rm -rf "$W"; mkdir -p "$W"; W=$(cd "$W" && pwd)
FAILS=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }
ok() { if [ "$1" = 0 ]; then pass "$2"; else fail "$2"; fi; }
REALSLEEP=$(command -v sleep); REALCP=$(command -v cp)

# shellcheck disable=SC2016  # $KSH_VERSION is expanded by mksh, not here
echo "== mksh: $(mksh -c 'echo "$KSH_VERSION"'); shellcheck: $(shellcheck --version | sed -n 's/^version: //p')"
shellcheck -s sh "$SRC/gb_audiodiag.sh" "$SRC/gb_audio.sh"
ok $? "shellcheck -s sh gb_audiodiag.sh gb_audio.sh: 0 findings"

# ---- PATH shims (they read $GBT = the per-case directory)
SH=$W/shims; mkdir -p "$SH"
w() { cat > "$SH/$1"; chmod 0755 "$SH/$1"; }
w sleep <<EOF
#!/bin/sh
n=\$(cat "\$GBT/clock" 2>/dev/null || echo 0); echo \$((n + \${1%%.*})) > "\$GBT/clock"
$REALSLEEP 0.01
EOF
w mount <<'EOF'
#!/bin/sh
echo "MOUNT $*" >> "$GBT/events"
case " $* " in *" -t vfat "*) [ -e "$GBT/ctl/mount_fail" ] && exit 32; : > "$GBT/mounted" ;; esac
exit 0
EOF
w umount <<'EOF'
#!/bin/sh
echo "UMOUNT $*" >> "$GBT/events"; rm -f "$GBT/mounted"
EOF
w sync <<'EOF'
#!/bin/sh
echo "SYNC" >> "$GBT/events"
EOF
w df <<'EOF'
#!/bin/sh
[ -e "$GBT/ctl/df_garbage" ] && { echo "df: no such mount"; exit 0; }
f=$(cat "$GBT/ctl/df_free" 2>/dev/null || echo 400000)
echo "Filesystem 1K-blocks Used Available Use% Mounted on"
echo "/dev/block/by-name/EFI-SYSTEM 516080 100000 $f 20% ${2:-/}"
EOF
# mkdir: the real tool; ctl/mkdir_race (one shot): right after the script's mkdir of the lock, an owner file with the
# ctl file's line appears in it (a lock put back in place of the new directory, INTERFACES 5.4 r3; wave 3: the line
# SELF stands for the calling script's own pid). Every mkdir of the lock is counted in $GBT/lockmkdir (wave 3, r4).
w mkdir <<'EOF'
#!/bin/sh
[ "$1" = "$GBT/gb_esp.lock" ] && echo "mkdir lock" >> "$GBT/lockmkdir"
if [ -f "$GBT/ctl/mkdir_race" ] && [ "$1" = "$GBT/gb_esp.lock" ]; then
  "$GB_REAL_MKDIR" "$@" || exit 1
  if [ "$(cat "$GBT/ctl/mkdir_race")" = SELF ]; then echo "$PPID gb_audiodiag 3" > "$1/owner"
  else cat "$GBT/ctl/mkdir_race" > "$1/owner"; fi
  rm -f "$GBT/ctl/mkdir_race"; exit 0
fi
exec "$GB_REAL_MKDIR" "$@"
EOF
for c in getprop dmesg logcat dumpsys; do
  # shellcheck disable=SC2016  # the shim's own variables expand when the shim runs
  printf '#!/bin/sh\n[ -e "$GBT/mounted" ] && m=1 || m=0\necho "COLLECT %s mounted=$m" >> "$GBT/events"\n' "$c" | w "$c"
done
w setprop <<'EOF'
#!/bin/sh
echo "SETPROP $*" >> "$GBT/events"
EOF
w cp <<'EOF'
#!/bin/sh
[ -e "$GBT/mounted" ] && m=1 || m=0
eval "d=\${$#}"
echo "COPY $d mounted=$m" >> "$GBT/events"
exec "$GB_REAL_CP" "$@"
EOF

# toybox tool directory (the second pass)
TBX=
if command -v toybox > /dev/null && command -v busybox > /dev/null; then
  TBX=$W/toybox-bin; mkdir -p "$TBX"
  for a in cat cut sed grep sort ls readlink stat sha256sum wc head tail mkdir rmdir rm mv cp ln date chmod \
    basename dirname printf timeout seq find id; do
    ln -s "$(command -v toybox)" "$TBX/$a"
  done
  ln -s "$(command -v busybox)" "$TBX/tr"
fi

# ---- helpers
newcase() {   # newcase NAME: fresh $GBT with a fake ESP (bootcount 7), lock path, tmp base, kmsg
  GBT=$P/$1; export GBT
  rm -rf "$GBT"; mkdir -p "$GBT/ctl" "$GBT/esp/gblog"; mkdir -m 0700 "$GBT/tmp"
  echo 7 > "$GBT/esp/gblog/bootcount"; : > "$GBT/events"; : > "$GBT/kmsg"; echo 0 > "$GBT/clock"
}
adiag() {     # gb_audiodiag.sh (dump mode) under mksh with this case's overrides
  GB_ADIAG_LOCK=$GBT/gb_esp.lock GB_ADIAG_MNT=$GBT/esp GB_ADIAG_TMP=${ADIAG_TMP:-$GBT/tmp} GB_ADIAG_KMSG=$GBT/kmsg \
    mksh "$SRC/gb_audiodiag.sh" > "$GBT/out" 2>&1
}
copied() { find "$GBT/esp/gblog" -name 'audio-b7-t*.txt' | grep -c '/audio-b7-t[0-9]*\.txt$'; }
kept() { find "$GBT/tmp" -mindepth 1 -maxdepth 1 -type d -name 't[0-9]*' | wc -l; }
holder() { "$REALSLEEP" 300 & HP=$!; mkdir "$GBT/gb_esp.lock"; echo "$HP gb_bootlog 40" > "$GBT/gb_esp.lock/owner"; }
tree_sum() { (cd "$1" && find . -type f -exec sha256sum {} + | sort); }

suite() {     # suite LABEL: every case once, PATH already set
  P=$W/$1; mkdir -p "$P"
  echo "== suite $1: cp=$GB_REAL_CP, tools: $(command -v stat) $(command -v mv)"

  # A1: a live holder; release it after 20 fake s; the copy follows
  newcase a1; holder
  adiag & AP=$!
  i=0; while [ "$(cat "$GBT/clock")" -lt 20 ] && [ $i -lt 500 ]; do "$REALSLEEP" 0.02; i=$((i + 1)); done
  waited=$(cat "$GBT/clock")
  [ -e "$GBT/mounted" ]; m_during=$?
  rm -f "$GBT/gb_esp.lock/owner"; rmdir "$GBT/gb_esp.lock"
  wait "$AP"; rc=$?
  kill "$HP" 2> /dev/null; wait "$HP" 2> /dev/null
  [ $rc = 0 ] && [ "$waited" -ge 20 ] && [ $m_during = 1 ] && [ "$(copied)" = 1 ] && [ "$(kept)" = 0 ] &&
    grep -q 'wrote gblog/audio-b7-t' "$GBT/kmsg" && [ ! -e "$GBT/gb_esp.lock" ]
  ok $? "$1 A1 live holder: waited (>= 20 fake s, ESP not mounted meanwhile), copied after the release, local copy removed, lock released"

  # A3 + A4 on the A1 run
  grep '^MOUNT .*-t vfat' "$GBT/events" | grep -q -- '-o rw,flush,nosuid,nodev,noexec'
  ok $? "$1 A3 the ESP mount options are rw,flush,nosuid,nodev,noexec"
  awk '/^MOUNT .*-t vfat/ {m++; on=1} /^UMOUNT/ {on=0} /^COLLECT/ && on {bad++} /^COPY/ && !on {bad++}
       /^COLLECT/ {c=NR} /^MOUNT .*-t vfat/ && !first {first=NR}
       END {exit !(m == 1 && !bad && c < first)}' "$GBT/events"
  ok $? "$1 A4 one ESP mount, after the whole collection; every copy inside it, nothing collected while mounted"
  grep -q 'COPY .*/esp/gblog/audio-b7-t[0-9]*\.txt mounted=1' "$GBT/events"; ok $? "$1 A4 the copy is named audio-b<bootcount>-t<T>.txt"

  # A2: the holder never releases: 600 fake s, then give up
  newcase a2; holder; I0=$(stat -c %i "$GBT/gb_esp.lock")
  adiag; rc=$?
  kill "$HP" 2> /dev/null; wait "$HP" 2> /dev/null
  [ $rc = 0 ] && [ "$(cat "$GBT/clock")" -ge 600 ] && [ "$(copied)" = 0 ] && [ "$(kept)" = 1 ] &&
    ! grep -q '^MOUNT .*-t vfat' "$GBT/events" && grep -q 'ESP lock held over 600 s' "$GBT/kmsg" &&
    [ "$(stat -c %i "$GBT/gb_esp.lock")" = "$I0" ] && [ "$(cat "$GBT/gb_esp.lock/owner")" = "$HP gb_bootlog 40" ]
  ok $? "$1 A2 600 fake s: gave up, no ESP mount, files kept locally, kmsg line, the holder's lock untouched"

  # A5: below the 128 MiB floor
  newcase a5; echo 100000 > "$GBT/ctl/df_free"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(copied)" = 0 ] && [ "$(kept)" = 1 ] && grep -q 'below the 128 MiB floor' "$GBT/kmsg" &&
    grep -q '^UMOUNT' "$GBT/events" && [ ! -e "$GBT/gb_esp.lock" ] && [ "$(ls "$GBT/esp/gblog")" = bootcount ]
  ok $? "$1 A5 ESP free 100,000 KiB: nothing written to gblog/, files kept, kmsg line, unmounted and unlocked"

  # A5b: a mount failure keeps the files too
  newcase a5b; : > "$GBT/ctl/mount_fail"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(kept)" = 1 ] && grep -q 'cannot mount' "$GBT/kmsg" && [ ! -e "$GBT/gb_esp.lock" ]
  ok $? "$1 A5b mount failure: files kept, kmsg line, lock released"

  # A1b: a stale lock (dead pid) is stolen once
  newcase a1b; mkdir "$GBT/gb_esp.lock"; sh -c 'exit 0' & dp=$!; wait "$dp"; echo "$dp gb_bootlog 3" > "$GBT/gb_esp.lock/owner"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(copied)" = 1 ] && [ "$(grep -c 'stealing a stale ESP lock' "$GBT/kmsg")" = 1 ]
  ok $? "$1 A1b dead-pid lock: stolen once, copied"

  # A7: five older collection directories -> the newest 3 stay (plus this run's, kept by the floor)
  newcase a7; echo 100000 > "$GBT/ctl/df_free"
  for k in 1 2 3 4 5; do mkdir "$GBT/tmp/t10$k"; touch -d "@$((1700000000 + k * 100))" "$GBT/tmp/t10$k"; done
  adiag; rc=$?
  left=$(find "$GBT/tmp" -mindepth 1 -maxdepth 1 -type d -name 't[0-9]*' -exec basename {} \; | sort | tr '\n' ' ')
  [ $rc = 0 ] && [ "$(kept)" = 4 ] && [ ! -e "$GBT/tmp/t101" ] && [ ! -e "$GBT/tmp/t102" ] && [ -d "$GBT/tmp/t105" ]
  ok $? "$1 A7 older local copies pruned to the newest 3 (left: $left)"

  # A8: the collection base is a symlink to another directory
  newcase a8; rmdir "$GBT/tmp"; mkdir "$GBT/victim"; echo keep > "$GBT/victim/f"; ln -s "$GBT/victim" "$GBT/tmp"
  v0=$(tree_sum "$GBT/victim")
  adiag; rc=$?
  [ $rc = 0 ] && [ ! -L "$GBT/tmp" ] && [ -d "$GBT/tmp" ] && [ "$(stat -c %a "$GBT/tmp")" = 700 ] &&
    [ "$(tree_sum "$GBT/victim")" = "$v0" ] && [ "$(ls "$GBT/victim")" = f ] && [ "$(copied)" = 1 ] &&
    grep -q 'is not a root-only directory: recreated' "$GBT/kmsg"
  ok $? "$1 A8 symlinked collection base: replaced by a 0700 directory, link target untouched, copied"

  # A9: the collection base has mode 0777 and holds a planted directory; A9b: the base is a plain file
  newcase a9; chmod 0777 "$GBT/tmp"; mkdir "$GBT/tmp/t1"; echo x > "$GBT/tmp/t1/x"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(stat -c %a "$GBT/tmp")" = 700 ] && [ ! -e "$GBT/tmp/t1" ] && [ "$(copied)" = 1 ] &&
    grep -q 'is not a root-only directory: recreated' "$GBT/kmsg"
  ok $? "$1 A9 collection base with mode 0777: removed and re-created 0700, planted entries gone, copied"
  newcase a9b; rmdir "$GBT/tmp"; echo x > "$GBT/tmp"
  adiag; rc=$?
  [ $rc = 0 ] && [ -d "$GBT/tmp" ] && [ "$(stat -c %a "$GBT/tmp")" = 700 ] && [ "$(copied)" = 1 ]
  ok $? "$1 A9b collection base is a plain file: re-created as a 0700 directory, copied"

  # A10: symlinks planted in the directories this run may use (t<uptime>..t<uptime+3>): never copied
  newcase a10; echo SECRET > "$GBT/secret"; s0=$(sha256sum < "$GBT/secret")
  u=$(cut -d. -f1 /proc/uptime)
  for k in 0 1 2 3; do mkdir "$GBT/tmp/t$((u + k))"; ln -s "$GBT/secret" "$GBT/tmp/t$((u + k))/z.lnk"; done
  adiag; rc=$?
  left=$(find "$GBT/tmp" -mindepth 1 -maxdepth 1 -type d -name 't[0-9]*' | wc -l)
  f=$(find "$GBT/esp/gblog" -name 'audio-b7-t*.txt' | head -n 1); t=${f##*/audio-b7-t}; t=${t%.txt}
  case $t in ''|*[!0-9]*) t=-1;; esac        # the run's T must be one of the planted directories (not vacuous)
  [ $rc = 0 ] && [ "$t" -ge "$u" ] && [ "$t" -le $((u + 3)) ] && [ ! -e "$GBT/tmp/t$t" ] && [ "$left" = 3 ] &&
    [ "$(copied)" = 1 ] && [ -z "$(find "$GBT/esp/gblog" -name '*z.lnk*')" ] &&
    [ "$(sha256sum < "$GBT/secret")" = "$s0" ] && ! grep -q 'COPY .*z\.lnk' "$GBT/events"
  ok $? "$1 A10 a symlink in the collection directory used by the run (t$t) is not copied (target untouched)"

  # A11: the base cannot be created (its parent is missing): nothing collected, no ESP mount, exit 0
  newcase a11; (ADIAG_TMP=$GBT/missing/tmp; adiag); rc=$?
  [ $rc = 0 ] && [ ! -e "$GBT/missing" ] && [ "$(copied)" = 0 ] && ! grep -q '^MOUNT .*-t vfat' "$GBT/events" &&
    grep -q 'cannot create a root-only' "$GBT/kmsg"
  ok $? "$1 A11 base not creatable: nothing collected, no ESP mount, kmsg line, exit 0"

  # ---- Wave 2b (INTERFACES 5.4 r3, safety findings 4 and 8)
  # A12: a steal in progress (a .stale.<pid> directory of a live pid): 5 back-offs, then the lock and the copy
  newcase a12; "$REALSLEEP" 300 & SP=$!; mkdir "$GBT/gb_esp.lock.stale.$SP"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(cat "$GBT/clock")" -ge 5 ] && [ "$(copied)" = 1 ] && [ -d "$GBT/gb_esp.lock.stale.$SP" ] &&
    [ ! -e "$GBT/gb_esp.lock" ] && ! grep -q 'stealing' "$GBT/kmsg"
  ok $? "$1 A12 steal in progress: backed off (clock $(cat "$GBT/clock") fake s), then copied; the stealer's directory untouched"
  kill "$SP" 2> /dev/null; wait "$SP" 2> /dev/null
  # A13: an owner file appears in the new lock directory before ours (dead pid): never overwritten, stolen as stale
  newcase a13; sh -c 'exit 0' & dp=$!; wait "$dp"; echo "$dp gb_bootlog 3" > "$GBT/ctl/mkdir_race"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(copied)" = 1 ] && [ "$(grep -c 'stealing a stale ESP lock' "$GBT/kmsg")" = 1 ] &&
    [ "$(cat "$GBT/clock")" -ge 1 ] && [ ! -e "$GBT/gb_esp.lock" ]
  ok $? "$1 A13 an owner file appeared after our mkdir: not overwritten (stolen once as stale at the next poll), copied"
  # A14: df gives no free-space number: fail closed
  newcase a14; : > "$GBT/ctl/df_garbage"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(copied)" = 0 ] && [ "$(kept)" = 1 ] && grep -q 'cannot read the ESP free space' "$GBT/kmsg" &&
    grep -q '^UMOUNT' "$GBT/events" && [ ! -e "$GBT/gb_esp.lock" ] && [ "$(ls "$GBT/esp/gblog")" = bootcount ]
  ok $? "$1 A14 df without a number: nothing written to gblog/, files kept, kmsg line, unmounted and unlocked"
  # A15: a bootcount edited on Windows (CRLF) keeps its number
  newcase a15; printf '7\r\n' > "$GBT/esp/gblog/bootcount"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(copied)" = 1 ]; ok $? "$1 A15 CRLF bootcount 7: copied as audio-b7-*"

  # ---- Wave 3 (INTERFACES 5.4 r4, wave 2b review L1/L2)
  # A16: a steal in progress: no mkdir of the lock at all during the 5 back-offs, then one, copied
  newcase a16; "$REALSLEEP" 300 & SP=$!; mkdir "$GBT/gb_esp.lock.stale.$SP"
  adiag; rc=$?
  nm=$(grep -c . "$GBT/lockmkdir" 2> /dev/null)
  [ $rc = 0 ] && [ "$(copied)" = 1 ] && [ "${nm:-0}" = 1 ] && [ "$(cat "$GBT/clock")" -ge 5 ]
  ok $? "$1 A16 r4 steal in progress: no mkdir while busy (lock mkdirs: ${nm:-0}), then copied"
  kill "$SP" 2> /dev/null; wait "$SP" 2> /dev/null
  # A17: a lock naming our own pid while we hold nothing (a late put-back) is stale: stolen once, copied
  newcase a17; echo SELF > "$GBT/ctl/mkdir_race"
  adiag; rc=$?
  [ $rc = 0 ] && [ "$(copied)" = 1 ] && [ "$(grep -c 'stealing a stale ESP lock' "$GBT/kmsg")" = 1 ] && [ ! -e "$GBT/gb_esp.lock" ]
  ok $? "$1 A17 r4 own pid in a lock we do not hold: stolen once as stale, copied"

  # A6: gb_audio.sh on a fake sysfs. v = dsp_driver value + variant: n = another PCI driver registers while
  # autoprobe is off, u = no drivers_autoprobe attribute (cannot switch it off: no legacy load)
  for v in 1 1n 1u 3; do
    dsp=${v%[nu]}
    GBT=$P/a6-$v; export GBT; rm -rf "$GBT"; mkdir -p "$GBT/ctl"; : > "$GBT/events"; echo 0 > "$GBT/clock"
    R=$GBT/root; mkdir -p "$R/dev" "$R/sys/module/snd_intel_dspcfg/parameters" "$R/sys/bus/pci/drivers" "$R/vendor/bin"
    : > "$R/dev/kmsg"; : > "$R/sys/bus/pci/drivers_probe"; echo $dsp > "$R/sys/module/snd_intel_dspcfg/parameters/dsp_driver"
    [ "$v" = 1u ] || echo 1 > "$R/sys/bus/pci/drivers_autoprobe"
    [ "$v" = 1n ] && : > "$GBT/ctl/newdrv"
    # shellcheck disable=SC2016  # the shim's own variables expand when the shim runs
    printf '%s\n' '#!/bin/sh' \
      'echo "MODPROBE $* autoprobe=$(cat "$GB_ROOT/sys/bus/pci/drivers_autoprobe" 2>/dev/null)" >> "$GBT/events"' \
      'for a in "$@"; do [ "$a" = snd_hda_intel ] || continue; mkdir -p "$GB_ROOT/sys/bus/pci/drivers/snd_hda_intel"' \
      '  [ -e "$GBT/ctl/newdrv" ] && mkdir -p "$GB_ROOT/sys/bus/pci/drivers/fake_new"; done' 'exit 0' > "$R/vendor/bin/modprobe"
    chmod 0755 "$R/vendor/bin/modprobe"
    mkpci() { d=$R/sys/bus/pci/devices/$1; mkdir -p "$d"; echo "$2" > "$d/vendor"; echo "$3" > "$d/device"; echo "$4" > "$d/class"
              echo '(null)' > "$d/driver_override"; }
    mkpci 0000:00:1f.3 0x8086 0x34c8 0x040380
    mkpci 0000:00:02.0 0x8086 0x8a52 0x030000
    mkpci 0000:02:00.0 0x10de 0x1f95 0x030200
    mkpci 0000:02:00.1 0x10de 0x10fa 0x040300
    nv0=$(tree_sum "$R/sys/bus/pci/devices/0000:02:00.1"); nv00=$(tree_sum "$R/sys/bus/pci/devices/0000:02:00.0")
    gpu0=$(tree_sum "$R/sys/bus/pci/devices/0000:00:02.0")
    GB_ROOT=$R mksh "$SRC/gb_audio.sh" > "$GBT/out" 2>&1
    hda=$(cat "$R/sys/bus/pci/devices/0000:00:1f.3/driver_override")
    [ "$(tree_sum "$R/sys/bus/pci/devices/0000:02:00.1")" = "$nv0" ] && [ "$(tree_sum "$R/sys/bus/pci/devices/0000:02:00.0")" = "$nv00" ] &&
      [ "$(tree_sum "$R/sys/bus/pci/devices/0000:00:02.0")" = "$gpu0" ] && ! grep -q 'probe 0000:02:00' "$R/dev/kmsg"
    untouched=$?
    ap=$(cat "$R/sys/bus/pci/drivers_autoprobe" 2>/dev/null)
    case $v in
      1)
        [ "$hda" = snd_hda_intel ] && [ $untouched = 0 ] && grep -q 'driver_override 0000:00:1f.3 = snd_hda_intel' "$R/dev/kmsg" &&
          [ "$(grep -c 'driver_override' "$R/dev/kmsg")" = 1 ]
        ok $? "$1 A6 dsp_driver=1: only 00:1f.3 driver_override = snd_hda_intel; 02:00.0/02:00.1 (0x10de) and 00:02.0 untouched"
        grep -q '^MODPROBE -d /vendor/lib/modules snd_hda_intel autoprobe=0$' "$GBT/events" && [ "$ap" = 1 ] &&
          grep -q 'probe 0000:00:1f.3 (snd_hda_intel)' "$R/dev/kmsg" && [ "$(grep -c 'GB_AUDIO: probe ' "$R/dev/kmsg")" = 1 ]
        ok $? "$1 A6 dsp_driver=1: snd_hda_intel loaded with PCI autoprobe 0, restored to 1, only 00:1f.3 probed by name" ;;
      1n)
        [ $untouched = 0 ] && grep -q 'registered while autoprobe was off: fake_new' "$R/dev/kmsg" &&
          grep -q 'GB_AUDIO: probe 0000:00:02.0$' "$R/dev/kmsg" && [ "$ap" = 1 ]
        ok $? "$1 A6 dsp_driver=1, another PCI driver registered meanwhile: driverless non-NVIDIA functions probed, NVIDIA never" ;;
      1u)
        [ $untouched = 0 ] && ! grep -q 'MODPROBE .*snd_hda_intel' "$GBT/events" && grep -q 'snd_hda_intel not loaded' "$R/dev/kmsg"
        ok $? "$1 A6 dsp_driver=1 without a drivers_autoprobe switch: snd_hda_intel not loaded (fail closed)" ;;
      3)
        [ "$hda" = '(null)' ] && [ $untouched = 0 ] && ! grep -q 'driver_override' "$R/dev/kmsg" && [ "$ap" = 1 ] &&
          ! grep -q 'MODPROBE .*snd_hda_intel' "$GBT/events"
        ok $? "$1 A6 dsp_driver=3: no driver_override write, autoprobe untouched, no snd_hda_intel" ;;
    esac
  done
}

# ---- Wave 3 item 2 (WAVE3_PLAN 5.1): the DRAS configuration files of the native tree (TREE_DIR/vup/etc/...)
# X1: <soundcard> in hal_configs/audio_default.xml is the ALSA card id that gb_audio.sh uses (ID=), not the name
SC=$(sed -n 's:^[[:space:]]*<soundcard>\([^<]*\)</soundcard>[[:space:]]*$:\1:p' "$SRC/vup/etc/data/hal_configs/audio_default.xml")
GID=$(sed -n 's/^ID=\([A-Za-z0-9]*\).*/\1/p' "$SRC/gb_audio.sh" | head -n 1)
[ -n "$SC" ] && [ "$SC" = "$GID" ] && [ "$SC" = sofhdadsp ] &&
  grep -q '<SoundCardName>hw:'"$GID"'</SoundCardName>' "$SRC/vup/etc/audio/sof-hda-dsp_sb3/audio_config.xml"
ok $? "X1 audio_default.xml <soundcard> '$SC' == gb_audio.sh ID '$GID' == audio_config.xml hw:<id> (the card id)"
# X2: every non-HDMI section is a superset of the fields of lapis's sof-soundwire section of the same name. Reference
# (hard-coded field names only): lapis vendor_a CL3B.260622.271.R1 (/build/vendor_a.img sha256 774cf8d9...),
# /vendor/etc/audio/sof-soundwire_cs42l43_cs35l56/audio_config.xml, read 2026-10-26.
fields() {   # fields FILE SECTION -> the child element names of <SECTION> (one per line, sorted)
  awk -v s="$2" '$0 ~ "<" s ">" {f=1; next} $0 ~ "</" s ">" {f=0} f && /<[A-Za-z]/ {
    l=$0; sub(/^[ \t]*</, "", l); sub(/>.*/, "", l); if (l !~ /^!/) print l }' "$1" | sort -u
}
bad=0
for ref in 'Speaker:PlaybackPCM' 'Headphone:PlaybackPCM JackDev JackSwitch' \
           'InternalMic:CapturePCM IntrinsicSensitivity RampUpMs' 'Mic:CapturePCM CaptureMixerElem JackDev'; do
  sec=${ref%%:*}
  for cfg in sof-hda-dsp_sb3 PCH_sb3_legacy; do
    F=$SRC/vup/etc/audio/$cfg/audio_config.xml
    have=$(fields "$F" "$sec")
    [ -z "$have" ] && [ "$cfg" = PCH_sb3_legacy ] && continue      # the legacy fallback declares only Speaker + Mic
    for f in ${ref#*:}; do
      printf '%s\n' "$have" | grep -q -x "$f" || { echo "  $cfg $sec lacks $f (has: $(echo $have))"; bad=$((bad + 1)); }
    done
  done
done
[ -n "$(fields "$SRC/vup/etc/audio/sof-hda-dsp_sb3/audio_config.xml" InternalMic)" ]; bad=$((bad + $?))
ok $bad "X2 sof-hda-dsp_sb3 (and the PCH fallback's sections) carry every field of lapis's sof-soundwire sections"
grep -q '<CaptureMixerElem>Capture</CaptureMixerElem>' "$SRC/vup/etc/audio/PCH_sb3_legacy/audio_config.xml" &&
  grep -q '<CaptureMixerElem>Capture</CaptureMixerElem>' "$SRC/vup/etc/audio/sof-hda-dsp_sb3/audio_config.xml"
ok $? "X3 both Mic sections carry CaptureMixerElem Capture"

PATH0=$PATH
GB_REAL_MKDIR=$(command -v mkdir); export GB_REAL_MKDIR
GB_REAL_CP=$REALCP; export GB_REAL_CP; PATH=$SH:$PATH0; export PATH
suite gnu
if [ -n "$TBX" ]; then
  GB_REAL_CP=$TBX/cp; GB_REAL_MKDIR=$TBX/mkdir; PATH=$SH:$TBX:$PATH0
  suite toybox
else
  fail "toybox pass skipped (toybox or busybox missing)"
fi
if [ "$FAILS" = 0 ]; then echo "TB-2 RESULT: OK"; else echo "TB-2 RESULT: $FAILS failure(s)"; exit 1; fi
