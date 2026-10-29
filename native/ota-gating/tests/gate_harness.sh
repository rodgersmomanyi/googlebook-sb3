#!/bin/bash
# OTA gate (wave 5b) dynamic tests on the builder: GBINIT (test copies of the patched build_esp.py's literal) runs under
# the SHIPPED inject/busybox with an empty environment (env -i PATH=/nonexistent: anything that is not a /busybox applet
# or an ash builtin fails), in fake roots made of the REAL tree files that matter (ota_validated, build.prop) and the
# REAL init_boot ramdisk build.prop of lapis.bin's slot a (CL3B.260622.271.R1, the build before the .276 OTA) and slot b
# (CL3B.260622.276.R4, the validated one). mount/umount/mknod/insmod are a logging shim; init is a stub. Then
# gb_otagate.sh runs (busybox sh; as service gb_otagate and as gb_otagate_post) against the state GBINIT left, and
# the message it queues is fed to the REAL gb_notify.sh (native component) with a stub `cmd`, to prove the
# notification is accepted and posted.
#   bash gate_harness.sh <work dir> <input dir>
# input dir: busybox gbinit gbinit.base gbinit.norm gbinit.late vm.build.prop native.build.prop ota_validated
#            ramdisk_a.prop ramdisk_b.prop gb_otagate.sh gb_notify.sh modules.load expect.env
set -u
W=$1
IN=$(cd "$2" && pwd)
BB=$IN/busybox
. "$IN/expect.env"             # FP_A FP_B (the two real fingerprints)
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL $1"; }
check() { _n=$1; shift; if "$@"; then ok "$_n"; else bad "$_n"; fi; }
eq() { [ "$1" = "$2" ] || { echo "     expected [$2]"; echo "     got      [$1]"; return 1; }; }
rm -rf "$W"; mkdir -p "$W"
echo "== busybox $("$BB" | head -n 1 | cut -d' ' -f2); validated (slot b) = $FP_B; slot a = $FP_A"

check "G0 busybox sh -n gbinit (patched)" eq "$("$BB" sh -n "$IN/gbinit" 2>&1; echo "rc=$?")" "rc=0"
check "G0 busybox sh -n gb_otagate.sh" eq "$("$BB" sh -n "$IN/gb_otagate.sh" 2>&1; echo "rc=$?")" "rc=0"
check "G0 exec /init is gbinit's last line" eq "$(grep -v '^[[:space:]]*$' "$IN/gbinit" | tail -n 1)" "exec /init"
check "G0 no set -e in gbinit" eq "$(grep -c 'set -e' "$IN/gbinit")" 0

# ------------------------------------------------------------------------------------------------ case helpers
begin() {   # begin <name> [ramdisk prop file | none | dir] [validated file | none]: fake root $R with both trees
  C=$W/$1; R=$C/root
  mkdir -p "$R/gbstage/etc/gb" "$R/gbnative/etc/gb" "$R/gbnative/lib/modules"
  : > "$C/events"; : > "$C/init.log"; echo "VMware, Inc." > "$C/dmi"
  cp "$IN/vm.build.prop" "$R/gbstage/etc/build.prop"; cp "$IN/native.build.prop" "$R/gbnative/etc/build.prop"
  cp "$IN/modules.load" "$R/gbnative/lib/modules/modules.load"
  for t in gbstage gbnative; do
    printf 'a /data ext4 rw wait\n' > "$R/$t/etc/fstab.android-desktop"
    mkdir -p "$R/$t/vnni/ID-1/system/lib64" "$R/$t/vnni/ID-1/vendor/lib64"
    printf 'patched' > "$R/$t/vnni/ID-1/system/lib64/libz.so"; printf 'patched' > "$R/$t/vnni/ID-1/vendor/lib64/libz.so"
    printf '{}' > "$R/$t/vnni/ID-1/manifest.json"
    case ${3:-real} in
      none) ;;
      real) cp "$IN/ota_validated" "$R/$t/etc/gb/ota_validated" ;;
      *) cp "$3" "$R/$t/etc/gb/ota_validated" ;;
    esac
  done
  case ${2:-b} in
    none) ;;
    dir) mkdir -p "$R/system/etc/ramdisk/build.prop" ;;
    a) mkdir -p "$R/system/etc/ramdisk"; cp "$IN/ramdisk_a.prop" "$R/system/etc/ramdisk/build.prop" ;;
    b) mkdir -p "$R/system/etc/ramdisk"; cp "$IN/ramdisk_b.prop" "$R/system/etc/ramdisk/build.prop" ;;
    *) mkdir -p "$R/system/etc/ramdisk"; cp "$2" "$R/system/etc/ramdisk/build.prop" ;;
  esac
  printf '#!/bin/sh\nPATH=/usr/bin:/bin\necho "INIT REACHED" >> %s/init.log\n' "$C" > "$R/init"; chmod 0755 "$R/init"
  cat > "$C/bb" <<EOF
#!/bin/sh
PATH=/usr/bin:/bin; export PATH
C='$C'; BB='$BB'
EOF
  cat >> "$C/bb" <<'EOF'
app=$1; shift
case $app in
mount)
  echo "mount $*" >> "$C/events"
  t=
  while [ $# -gt 2 ]; do case $1 in -t) t=$2; shift 2;; -o) shift 2;; *) break;; esac; done
  dst=$2
  case $t in
    proc) mkdir -p "$dst"; cp "$C/cmdline" "$dst/cmdline"; exit 0;;
    sysfs) mkdir -p "$dst/class/dmi/id"; cp "$C/dmi" "$dst/class/dmi/id/sys_vendor"; exit 0;;
    tmpfs) mkdir -p "$dst"; exit 0;;
  esac
  exit 32;;
umount) echo "umount $*" >> "$C/events"; case $1 in -l) ;; *) rm -rf "$1" 2>/dev/null; mkdir -p "$1";; esac; exit 0;;
mknod) [ "$1" = -m ] && shift 2; : > "$1"; echo "mknod $*" >> "$C/events"; exit 0;;
insmod) echo "insmod $*" >> "$C/events"; exit 0;;
*) exec "$BB" "$app" "$@";;
esac
EOF
  chmod 0755 "$C/bb"
}
boot() {    # boot <cmdline> [gbinit variant]: run a test copy of GBINIT under the shipped busybox
  printf '%s\n' "$1" > "$C/cmdline"
  sed -e 's#/busybox#@BB@#g' \
      -e 's#\([ =<>(]\)/\(proc\|sys\|odm\|gbstage\|gbnative\|gbbringup\.sh\|gbbu\|init\|system\)\b#\1@R@/\2#g' \
      "${2:-$IN/gbinit}" | sed -e "s#@BB@#$C/bb#g" -e "s#@R@#$R#g" > "$C/gbinit.test"
  if grep -n -E '(^|[ =<>(])/(busybox|proc|sys|odm|gbstage|gbnative|gbbringup\.sh|gbbu|init|system)\b' "$C/gbinit.test"; then
    echo "     gbinit test copy still names an absolute root"
  fi
  (cd "$C" && timeout 60 env -i PATH=/nonexistent "$BB" sh "$C/gbinit.test" > "$C/gbinit.out" 2>&1)
  RC=$?
}
st() {      # state/vnni/the build.prop tail/hw/init/insmods
  _s=$(tr '\n' '|' < "$R/odm/etc/gb/ota_state" 2>/dev/null || echo "-")
  _v=$([ -e "$R/odm/vnni/ID-1/vendor/lib64/libz.so" ] && echo vnni || echo novnni)
  _p=$(if cmp -s "$R/odm/etc/build.prop" "$R/$T/etc/build.prop"; then echo prop-unchanged
       elif [ "$(head -c "$(wc -c < "$R/$T/etc/build.prop")" "$R/odm/etc/build.prop" | cmp - "$R/$T/etc/build.prop" && echo y)" = y ] &&
            [ "$(tail -c +"$(($(wc -c < "$R/$T/etc/build.prop") + 1))" "$R/odm/etc/build.prop" | od -An -c | tr -s ' ')" = "$(printf '\nro.vendor.gb.ota_gate=unvalidated\n' | od -An -c | tr -s ' ')" ]
       then echo prop+gate; else echo prop-OTHER; fi)
  echo "$_s/$_v/$_p/$(cat "$R/odm/etc/gb_hw" 2>/dev/null)/init=$(grep -c 'INIT REACHED' "$C/init.log")/insmod=$(grep -c '^insmod' "$C/events")"
}
whiteouts() { [ -e "$R/odm/vup/etc/init/android.hardware.gatekeeper-service.trusty.rc" ] &&
              [ -e "$R/odm/vup/apex/com.android.hardware.keymint.strongbox.desktop.apex" ]; }
clean() { rm -rf "$C"; }
NMOD=$(grep -v -E '^(#|$)' "$IN/modules.load" | grep -c .)
VAL="state=validated|validated=$FP_B|booted=$FP_B|token=none|"
UNA="state=unvalidated|validated=$FP_B|booted=$FP_A|token=none|"
NAT="rdinit=/gbinit gb.hw=native gb.boot=internal"

# ------------------------------------------------------------------------------------------------ GBINIT cases
T=gbstage
begin G1 b; boot "rdinit=/gbinit gb.hw=vm"
check "G1 VM, validated build (slot b ramdisk): state validated, /odm/vnni kept, build.prop unchanged, init reached" \
  eq "$(st)" "$VAL/vnni/prop-unchanged/vm/init=1/insmod=0"
check "G1 whiteouts still made, gbinit rc 0 (stub init)" eq "$(whiteouts && echo w)/$RC" "w/0"
clean
begin G2 a; boot "rdinit=/gbinit gb.hw=vm"
check "G2 VM after the OTA (slot a ramdisk, another build): unvalidated, /odm/vnni removed, the gate prop appended once" \
  eq "$(st)" "$UNA/novnni/prop+gate/vm/init=1/insmod=0"
check "G2 everything else as built: whiteouts, fstab, the rest of /odm" \
  eq "$(whiteouts && echo w)/$(cat "$R/odm/etc/fstab.android-desktop")" "w/a /data ext4 rw wait"
clean
T=gbnative
begin G3 b; boot "$NAT"
check "G3 native, validated: state validated, vnni kept, all modules loaded" \
  eq "$(st)" "$VAL/vnni/prop-unchanged/native/init=1/insmod=$NMOD"
clean
begin G4 a; boot "$NAT"
check "G4 native after the OTA: unvalidated, vnni removed, gate prop, modules still loaded (drivers keep working)" \
  eq "$(st)" "$UNA/novnni/prop+gate/native/init=1/insmod=$NMOD"
clean
T=gbstage
begin G5 a; boot "rdinit=/gbinit gb.hw=vm gb.otagate=0"
check "G5 gb.otagate=0 on another build: off, everything as built (vnni kept, build.prop unchanged)" \
  eq "$(st)" "state=off|validated=$FP_B|booted=$FP_A|token=0|/vnni/prop-unchanged/vm/init=1/insmod=0"
clean
begin G6 b; boot "rdinit=/gbinit gb.hw=vm gb.otagate=force"
check "G6 gb.otagate=force on the validated build: the unvalidated path (test mode)" \
  eq "$(st)" "state=unvalidated|validated=$FP_B|booted=$FP_B|token=force|/novnni/prop+gate/vm/init=1/insmod=0"
clean
begin G7 none; boot "rdinit=/gbinit gb.hw=vm"
check "G7 no ramdisk build.prop (unreadable build): unvalidated, booted=unknown" \
  eq "$(st)" "state=unvalidated|validated=$FP_B|booted=unknown|token=none|/novnni/prop+gate/vm/init=1/insmod=0"
clean
begin G8 dir; boot "rdinit=/gbinit gb.hw=vm"
check "G8 ramdisk build.prop is a directory (sed fails): unvalidated, init still reached" \
  eq "$(st)" "state=unvalidated|validated=$FP_B|booted=unknown|token=none|/novnni/prop+gate/vm/init=1/insmod=0"
clean
printf '# x\r\nro.bootimage.build.fingerprint=%s\r\nro.bootimage.build.fingerprint=%s\r\n' "$FP_A" "$FP_B" > "$W/crlf_last_b.prop"
begin G9 "$W/crlf_last_b.prop"; boot "rdinit=/gbinit gb.hw=vm"
check "G9 CRLF and two assignments, the last = validated: validated (last wins, CR ignored)" \
  eq "$(st)" "$VAL/vnni/prop-unchanged/vm/init=1/insmod=0"
clean
printf 'ro.bootimage.build.fingerprint=%s\nro.bootimage.build.fingerprint=%s\n' "$FP_B" "$FP_A" > "$W/last_a.prop"
begin G10 "$W/last_a.prop"; boot "rdinit=/gbinit gb.hw=vm"
check "G10 two assignments, the last = another build: unvalidated" \
  eq "$(st)" "$UNA/novnni/prop+gate/vm/init=1/insmod=0"
clean
printf 'ro.bootimage.build.fingerprint=%s-x\nro.bootimage.build.fingerprint.x=%s\nxro.bootimage.build.fingerprint=%s\n' "$FP_B" "$FP_B" "$FP_B" > "$W/near.prop"
begin G11 "$W/near.prop"; boot "rdinit=/gbinit gb.hw=vm"
check "G11 near misses (suffix, other key, prefixed key): unvalidated (exact match only)" \
  eq "$(st)" "state=unvalidated|validated=$FP_B|booted=$FP_B-x|token=none|/novnni/prop+gate/vm/init=1/insmod=0"
clean
begin G12 a none; boot "rdinit=/gbinit gb.hw=vm"
check "G12 a tree without ota_validated (built without the gate): nothing happens (no state, vnni kept, prop unchanged)" \
  eq "$(st)" "-/vnni/prop-unchanged/vm/init=1/insmod=0"
clean
: > "$W/empty_validated"
begin G13 none "$W/empty_validated"; boot "rdinit=/gbinit gb.hw=vm"
check "G13 an empty ota_validated and no ramdisk prop: unvalidated (two empty strings never validate)" \
  eq "$(st)" "state=unvalidated|validated=|booted=unknown|token=none|/novnni/prop+gate/vm/init=1/insmod=0"
clean
for tok in gb.otagate=00 gb.otagate= xgb.otagate=0 gb.otagate=0x gb.otagate=forced gb.otagate=FORCE gb.otagate=1; do
  begin G14 b; boot "rdinit=/gbinit gb.hw=vm $tok"
  check "G14 '$tok' is not a gate token: validated as without it" eq "$(st)" "$VAL/vnni/prop-unchanged/vm/init=1/insmod=0"
  clean
done
begin G15 b; boot "rdinit=/gbinit gb.hw=vm gb.vnni=0"
check "G15 gb.vnni=0 on the validated build: vnni removed by the kill switch, the gate says validated" \
  eq "$(st)" "$VAL/novnni/prop-unchanged/vm/init=1/insmod=0"
clean
begin G16 a; boot "rdinit=/gbinit gb.otagate=0 gb.otagate=force"
check "G16 DMI VMware, both tokens (the last wins: force): unvalidated" \
  eq "$(st)" "state=unvalidated|validated=$FP_B|booted=$FP_A|token=force|/novnni/prop+gate/vm/init=1/insmod=0"
clean

# ------------------------------------------------------------------------------------------------ mutations
begin M1 a; boot "rdinit=/gbinit gb.hw=vm" "$IN/gbinit.base"
check "M1 (mutation) the unpatched GBINIT after the OTA keeps the stale /odm/vnni and sets no state (what G2 catches)" \
  eq "$(st)" "-/vnni/prop-unchanged/vm/init=1/insmod=0"
clean
begin M2 a; boot "rdinit=/gbinit gb.hw=vm" "$IN/gbinit.norm"
check "M2 (mutation) a gate without its rm: G2's expectation fails (vnni kept)" \
  eq "$([ "$(st)" = "$UNA/novnni/prop+gate/vm/init=1/insmod=0" ] && echo same || echo differs)" "differs"
clean
begin M3 a; boot "rdinit=/gbinit gb.hw=vm" "$IN/gbinit.late"
check "M3 (mutation) the gate moved after exec /init never runs: G2's expectation fails" \
  eq "$([ "$(st)" = "$UNA/novnni/prop+gate/vm/init=1/insmod=0" ] && echo same || echo differs)" "differs"
clean

# ------------------------------------------------------------------------------------------------ gb_otagate.sh
nbegin() {  # nbegin <name> <state file content | none> [queue | stray | notifier]: GB_ROOT for gb_otagate.sh /
            # gb_notify.sh, stub tools. queue = native (gb_notify shipped + its queue dir); stray = a queue dir without
            # gb_notify (not the native tree); notifier = gb_notify shipped, no queue dir yet
  C=$W/$1; R=$C/root; S=$C/stub
  mkdir -p "$R/odm/etc/gb" "$R/dev" "$S"; : > "$R/dev/kmsg"; : > "$C/calls"
  [ "$2" != none ] && printf '%s' "$2" > "$R/odm/etc/gb/ota_state"
  case ${3:-} in queue | stray) mkdir -p "$R/data/vendor/gb/notify" ;; esac
  case ${3:-} in queue | notifier) mkdir -p "$R/odm/vup/bin"; cp "$IN/gb_notify.sh" "$R/odm/vup/bin/gb_notify.sh" ;; esac
  for t in cmd setprop log; do
    printf '#!/bin/sh\nprintf "%%s" "%s" >> %s/calls; for a in "$@"; do printf " [%%s]" "$a" >> %s/calls; done; echo >> %s/calls\n' \
      "$t" "$C" "$C" "$C" > "$S/$t"
    chmod 0755 "$S/$t"
  done
  for t in sed tail tr cut printf date mv rm timeout wc sleep stat; do ln -s "$BB" "$S/$t"; done
}
nrun() { (cd "$C" && env -i GB_ROOT="$R" GB_TEST_PATH="$S" PATH=/nonexistent "$BB" sh "$IN/gb_otagate.sh" "$@" > "$C/out" 2>&1); }
# nrun = service gb_otagate (root: kmsg line + queue); nrun post = service gb_otagate_post (user shell: direct post
# only where no gb_notify queue exists). Both start on the same trigger.
TITLE="System update not validated"
TEXT="Build CL3B.260622.271.R1 is not the build this boot setup was made for (CL3B.260622.276.R4). Paused until the next ESP update: VNNI libz patch."
nbegin N1 "state=validated
validated=$FP_B
booted=$FP_B
token=none
" queue; nrun; nrun post
check "N1 validated state, both services: exit, no message, no call, no kmsg line" \
  eq "$(ls -A "$R/data/vendor/gb/notify" | wc -l)/$(wc -c < "$C/calls")/$(wc -c < "$R/dev/kmsg")" "0/0/0"
clean
nbegin N1b "state=validated
validated=$FP_B
booted=$FP_B
token=none
"; nrun; nrun post
check "N1b validated state without a queue (VM), both services: no call, no kmsg line" \
  eq "$(wc -c < "$C/calls")/$(wc -c < "$R/dev/kmsg")" "0/0"
clean
nbegin N2 none queue; nrun; nrun post
check "N2 no state file, both services: exit, nothing" eq "$(ls -A "$R/data/vendor/gb/notify" | wc -l)/$(wc -c < "$C/calls")" "0/0"
clean
nbegin N2b "state=off
validated=$FP_B
booted=$FP_A
token=0
"; nrun; nrun post
check "N2b state off (gb.otagate=0), both services: nothing" eq "$(wc -c < "$C/calls")/$(wc -c < "$R/dev/kmsg")" "0/0"
clean
UST="state=unvalidated
validated=$FP_B
booted=$FP_A
token=none
"
nbegin N3 "$UST" queue; nrun
M=$(ls "$R/data/vendor/gb/notify")
check "N3 native (queue present): one message, named <13 digits>-<pid>-1.msg, no .tmp left" \
  eq "$(echo "$M" | grep -c -E '^[0-9]{13}-[0-9]+-1\.msg$')/$(ls "$R/data/vendor/gb/notify" | grep -c tmp)" "1/0"
check "N3 the message: kind=notify, tag gb_update, the title and the text" \
  eq "$(cat "$R/data/vendor/gb/notify/$M")" "kind=notify
tag=gb_update
title=$TITLE
text=$TEXT"
check "N3 setprop vendor.gb.notify <the message's epoch ms>, no direct post" \
  eq "$(cat "$C/calls")" "setprop [vendor.gb.notify] [${M%%-*}]"
check "N3 kmsg: the UNVALIDATED line (both fingerprints, skipped and kept labels) and 'queued'" \
  eq "$(grep -c -F "gb_otagate: UNVALIDATED build: booted $FP_A, ESP built for $FP_B (token none); skipped: VNNI libz patch; kept as built (needed to boot): KeyMint graft, SELinux, VM graphics, fstab, system_ext VINTF; update the ESP to re-validate" "$R/dev/kmsg")/$(grep -c 'gb_otagate: notification queued for gb_notify' "$R/dev/kmsg")" "1/1"
: > "$C/calls"; K0=$(wc -c < "$R/dev/kmsg"); nrun post
check "N3 gb_otagate_post with the queue present (native): no direct post, no kmsg line, the queue untouched" \
  eq "$(wc -c < "$C/calls")/$(($(wc -c < "$R/dev/kmsg") - K0))/$(ls "$R/data/vendor/gb/notify")" "0/0/$M"
# the REAL gb_notify.sh (native component) delivers it: validation passes and it posts via cmd
: > "$C/calls"
(cd "$C" && env -i GB_ROOT="$R" GB_TEST_PATH="$S" PATH=/nonexistent "$BB" sh "$IN/gb_notify.sh" > "$C/nout" 2>&1)
check "N3 gb_notify.sh accepts the message and posts it: cmd notification post -t <title> gb_update <text>; queue emptied" \
  eq "$(cat "$C/calls")/$(ls -A "$R/data/vendor/gb/notify" | wc -l)" "cmd [notification] [post] [-t] [$TITLE] [gb_update] [$TEXT]/0"
check "N3 gb_notify logged no 'dropped' line" eq "$(grep -c dropped "$R/dev/kmsg")" 0
clean
nbegin N4 "$UST"; nrun
check "N4 VM (no queue), gb_otagate (root): the kmsg line and 'no gb_notify queue', no post as root, nothing queued" \
  eq "$(wc -c < "$C/calls")/$(grep -c 'gb_otagate: UNVALIDATED build' "$R/dev/kmsg")/$(grep -c 'gb_otagate: no gb_notify queue: gb_otagate_post posts the notification' "$R/dev/kmsg")/$(grep -c queued "$R/dev/kmsg")/$([ -e "$R/data" ] && echo data || echo nodata)" \
  "0/1/1/0/nodata"
K0=$(wc -c < "$R/dev/kmsg"); nrun post
check "N4 VM (no queue), gb_otagate_post (user shell): posts directly with the same title, tag and text, no kmsg line" \
  eq "$(cat "$C/calls")/$(($(wc -c < "$R/dev/kmsg") - K0))" "cmd [notification] [post] [-t] [$TITLE] [gb_update] [$TEXT]/0"
clean
nbegin N4b "$UST"; printf '#!/bin/sh\necho "cmd $*" >> %s/calls\nexit 1\n' "$C" > "$S/cmd"; nrun post
check "N4b the direct post fails: logged ('cmd notification post failed'), exit 0" \
  eq "$(grep -c 'gb_otagate: cmd notification post failed' "$R/dev/kmsg")/$(grep -c '^cmd notification post' "$C/calls")/$(grep -c . "$C/out")" "1/1/0"
clean
nbegin N5 "state=unvalidated
validated=
booted=unknown
token=force
" queue; nrun
M=$(ls "$R/data/vendor/gb/notify")
check "N5 unknown/empty fingerprints: ids 'unknown', the message still valid" \
  eq "$(sed -n 's/^text=//p' "$R/data/vendor/gb/notify/$M")" \
  "Build unknown is not the build this boot setup was made for (unknown). Paused until the next ESP update: VNNI libz patch."
clean
LONG=$(printf 'A%.0s' $(seq 1 300))
nbegin N6 "state=unvalidated
validated=x/y/z:1/${LONG}\$(reboot)\`id\`/1:user/k
booted=a/b/c:1/I\"D;rm -rf /;\$X/1:user/k
token=none
" queue; nrun
M=$(ls "$R/data/vendor/gb/notify")
T6=$(sed -n 's/^text=//p' "$R/data/vendor/gb/notify/$M")
: > "$C/calls"
(cd "$C" && env -i GB_ROOT="$R" GB_TEST_PATH="$S" PATH=/nonexistent "$BB" sh "$IN/gb_notify.sh" > "$C/nout" 2>&1)
check "N6 hostile fingerprints: ids cut to safe characters (<= 24), text <= 200 printable, nothing executed, gb_notify posts it" \
  eq "$([ ${#T6} -le 200 ] && echo short)/$(printf '%s' "$T6" | tr -d ' -~' | wc -c)/$(grep -c '^cmd \[notification\] \[post\]' "$C/calls")/$(grep -c -E 'reboot|^id' "$C/calls")" "short/0/1/0"
check "N6 the text" eq "$T6" "Build IDrm-rf is not the build this boot setup was made for (AAAAAAAAAAAAAAAAAAAAAAAA). Paused until the next ESP update: VNNI libz patch."
clean
nbegin N7 "$UST" notifier; mkdir -p "$C/elsewhere"; mkdir -p "$R/data/vendor/gb"; ln -s "$C/elsewhere" "$R/data/vendor/gb/notify"; nrun
check "N7 gb_notify shipped, its queue dir a symlink: gb_otagate does not write through it and does not post as root" \
  eq "$(ls -A "$C/elsewhere" | wc -l)/$(grep -c '^cmd' "$C/calls")" "0/0"
nrun post
check "N7 ... gb_otagate_post posts directly instead (exactly one post)" \
  eq "$(ls -A "$C/elsewhere" | wc -l)/$(grep -c '^cmd' "$C/calls")" "0/1"
clean
nbegin N8 "$UST" queue; nrun post; nrun post
check "N8 gb_otagate_post alone, run twice, with the queue: never posts, never writes the queue" \
  eq "$(wc -c < "$C/calls")/$(ls -A "$R/data/vendor/gb/notify" | wc -l)" "0/0"
clean
nbegin N9 "$UST"; rm -f "$R/dev/kmsg"; mkdir -p "$R/dev/kmsg"; nrun
check "N9 /dev/kmsg not writable (gb_otagate_post's shell uid, a 0600 root kmsg): log1 falls back to logcat (log -t gb_otagate)" \
  eq "$(grep -c '^log \[-t\] \[gb_otagate\] \[-p\] \[w\] \[UNVALIDATED build' "$C/calls")/$(grep -c . "$C/out")" "1/0"
clean
nbegin N10 "$UST" stray; nrun; nrun post
check "N10 a stray queue dir without gb_notify (not the native tree): nothing queued (nobody would deliver it), one direct post" \
  eq "$(ls -A "$R/data/vendor/gb/notify" | wc -l)/$(grep -c '^cmd \[notification\] \[post\] \[-t\] \[System update not validated\] \[gb_update\]' "$C/calls")/$(grep -c '^setprop' "$C/calls")" "0/1/0"
clean
nbegin N11 "$UST" notifier; nrun; nrun post
check "N11 gb_notify shipped but no queue dir yet: nothing queued, one direct post (the notification is not lost)" \
  eq "$([ -e "$R/data" ] && echo data || echo nodata)/$(grep -c '^cmd \[notification\] \[post\]' "$C/calls")" "nodata/1"
clean

echo "RESULT pass=$PASS fail=$FAIL"
[ "$FAIL" = 0 ]
