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
f=$(cat "$GBT/ctl/df_free" 2>/dev/null || echo 400000)
echo "Filesystem 1K-blocks Used Available Use% Mounted on"
echo "/dev/block/by-name/EFI-SYSTEM 516080 100000 $f 20% ${2:-/}"
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

  # A6: gb_audio.sh on a fake sysfs
  for dsp in 1 3; do
    GBT=$P/a6-$dsp; export GBT; rm -rf "$GBT"; mkdir -p "$GBT/ctl"; : > "$GBT/events"; echo 0 > "$GBT/clock"
    R=$GBT/root; mkdir -p "$R/dev" "$R/sys/module/snd_intel_dspcfg/parameters" "$R/sys/bus/pci/drivers"
    : > "$R/dev/kmsg"; : > "$R/sys/bus/pci/drivers_probe"; echo $dsp > "$R/sys/module/snd_intel_dspcfg/parameters/dsp_driver"
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
      [ "$(tree_sum "$R/sys/bus/pci/devices/0000:00:02.0")" = "$gpu0" ]
    untouched=$?
    if [ $dsp = 1 ]; then
      [ "$hda" = snd_hda_intel ] && [ $untouched = 0 ] && grep -q 'driver_override 0000:00:1f.3 = snd_hda_intel' "$R/dev/kmsg" &&
        [ "$(grep -c 'driver_override' "$R/dev/kmsg")" = 1 ]
      ok $? "$1 A6 dsp_driver=1: only 00:1f.3 driver_override = snd_hda_intel; 02:00.0/02:00.1 (0x10de) and 00:02.0 untouched"
    else
      [ "$hda" = '(null)' ] && [ $untouched = 0 ] && ! grep -q 'driver_override' "$R/dev/kmsg"
      ok $? "$1 A6 dsp_driver=3: no driver_override write"
    fi
  done
}

PATH0=$PATH
GB_REAL_CP=$REALCP; export GB_REAL_CP; PATH=$SH:$PATH0; export PATH
suite gnu
if [ -n "$TBX" ]; then
  GB_REAL_CP=$TBX/cp; PATH=$SH:$TBX:$PATH0
  suite toybox
else
  fail "toybox pass skipped (toybox or busybox missing)"
fi
if [ "$FAILS" = 0 ]; then echo "TB-2 RESULT: OK"; else echo "TB-2 RESULT: $FAILS failure(s)"; exit 1; fi
