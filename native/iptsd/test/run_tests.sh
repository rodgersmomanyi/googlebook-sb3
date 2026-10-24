#!/bin/bash
# Builder-side tests for the Android iptsd build (run as root: sudo bash run_tests.sh).
#  1. --help of every static binary (NDK/bionic static binaries run on the Linux host).
#  2. iptsd-perf replays of synthetic iptsd-dump files: EDS1 (exact kernel descriptor, SB3 preset from ./etc/presets)
#     and EDS2 (metadata feature report, product id without preset -> size/invert from metadata only).
#  3. End to end: gb_iptsd.sh under mksh (Android's /system/bin/sh) finds a fake "IPTS 045E:09B2" uhid device, writes
#     the IDCs, runs the Android iptsd in a chroot holding the /odm/etc/iptsd tree at its compiled-in path;
#     uhid_e2e.py checks the uinput touchscreen/stylus output, a fake resume and a device re-creation.
#  4. ipts.ko reload after repeated immediate iptsd failures. 5. persist.gb.iptsd.disable: bounded wait for the
#     persist properties, then idle (init would restart an exited service every 5 s).
set -u
T=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-/build/native/iptsd/out}
DESC_H=${DESC_H:-/build/gki/common/drivers/hid/ipts/desc.h}
W=$T/work
rc=0
rm -rf "$W"; mkdir -p "$W"

echo "=== 1. --help"
for b in iptsd iptsd-check-device iptsd-dump iptsd-perf iptsd-calibrate; do
	"$OUT/bin/$b" --help > "$W/help_$b.txt" 2>&1 && echo "ok $b" || { echo "FAIL $b"; rc=1; }
done

echo "=== 2. iptsd-perf replay"
python3 "$T/ipts_frames.py" --desc-h "$DESC_H" --out "$W/eds1_045E_09B2.bin"
python3 -c "
import sys; sys.path.insert(0, '$T'); import ipts_frames as F
d = F.descriptor('$DESC_H', eds2=True); _, fb = F.kernel_descriptors('$DESC_H'); size = 2 + F.data_report_count(fb)
feat = bytes([F.META_ID]) + F.metadata_feature()
F.write_dump('$W/eds2_045E_0999.bin', d, F.build_reports(eds2=True, size=size), product=0x0999, feature=feat)
print('wrote eds2 dump')"
mkdir -p "$W/perfcwd/etc"; cp -r "$OUT/etc/iptsd/presets" "$W/perfcwd/etc/presets"
( cd "$W/perfcwd" && "$OUT/bin/iptsd-perf" "$W/eds1_045E_09B2.bin" 20 ) > "$W/perf_eds1.txt" 2>&1
cat "$W/perf_eds1.txt"
grep -q "Loading config ./etc/presets/surface-book-3-13.conf" "$W/perf_eds1.txt" && grep -q "Ran [1-9]" "$W/perf_eds1.txt" \
	&& echo "ok perf eds1 (SB3 preset matched)" || { echo "FAIL perf eds1"; rc=1; }
( cd "$W" && "$OUT/bin/iptsd-perf" "$W/eds2_045E_0999.bin" 20 ) > "$W/perf_eds2.txt" 2>&1
cat "$W/perf_eds2.txt"
grep -q "Ran [1-9]" "$W/perf_eds2.txt" && ! grep -q "Loading config" "$W/perf_eds2.txt" \
	&& echo "ok perf eds2 (metadata only)" || { echo "FAIL perf eds2"; rc=1; }

echo "=== 3. end to end (uhid + uinput + gb_iptsd.sh + chroot iptsd)"
modprobe uhid 2> /dev/null; modprobe hid-generic 2> /dev/null
CH=$W/chroot
mkdir -p "$CH/vendor/bin" "$CH/odm/etc" "$CH/dev"
cp "$OUT/bin/iptsd" "$OUT/bin/iptsd-check-device" "$CH/vendor/bin/"
cp -r "$OUT/etc/iptsd" "$CH/odm/etc/"
mount --bind /dev "$CH/dev"
printf '#!/bin/sh\nexec chroot %s /vendor/bin/iptsd "$@"\n' "$CH" > "$W/iptsd-chroot"; chmod +x "$W/iptsd-chroot"
# iptsd-check-device builds the full iptsd application, so it also needs the presets (screen size) -> chroot too
printf '#!/bin/sh\nexec chroot %s /vendor/bin/iptsd-check-device "$@"\n' "$CH" > "$W/check-chroot"; chmod +x "$W/check-chroot"
# The runtime IDC dir mimics /odm/usr/idc: NOT pre-created, and the launcher runs with init's service umask (077), so
# the test checks that system_server could search it (0755) and read the files (0644).
IDCD=$W/odm/usr/idc
mkdir -p "$W/stubs" "$W/static_idc" "$W/odm"
# getprop stub: persist properties are "loaded"; everything else is unset
printf '#!/bin/sh\n[ "$1" = ro.persistent_properties.ready ] && echo true\nexit 0\n' > "$W/stubs/getprop"
printf '#!/bin/sh\necho "stub $(basename $0) $*"\n' > "$W/stubs/rmmod"; cp "$W/stubs/rmmod" "$W/stubs/insmod"
chmod +x "$W/stubs/"*
cp "$OUT/idc/"*.idc "$W/static_idc/"
echo 0 > "$W/suspend_success"
( umask 077; exec env GB_IPTSD_BIN="$W/iptsd-chroot" GB_IPTSD_CHECK="$W/check-chroot" GB_IPTSD_IDC="$IDCD" \
	GB_IPTSD_STATIC_IDC="$W/static_idc" GB_IPTSD_KO=/nonexistent/ipts.ko GB_IPTSD_USERCONF="$W/none.conf" \
	GB_IPTSD_SUSPEND_STATS="$W/suspend_success" GB_IPTSD_POLL=1 PATH="$W/stubs:$PATH" \
	setsid mksh "$OUT/gb_iptsd.sh" ) > "$W/launcher.log" 2>&1 &
LPID=$!
sleep 1
python3 "$T/uhid_e2e.py" --desc-h "$DESC_H" --suspend "$W/suspend_success" --recreate 2>&1 | tee "$W/e2e_eds1.txt"
[ "${PIPESTATUS[0]}" = 0 ] || rc=1
# EDS2 flavour with a product id that has no preset and no static IDC: size/invert must come from the metadata
# feature report, and the launcher must generate the IDCs.
python3 "$T/uhid_e2e.py" --desc-h "$DESC_H" --eds2 --product 0x0999 2>&1 | tee "$W/e2e_eds2.txt"
[ "${PIPESTATUS[0]}" = 0 ] || rc=1
# Discovery fallback: a device NOT named "IPTS ..." must still be found after 10 s through iptsd-check-device
# (VVVV:PPPP then come from HID_ID). Afterwards the device disappears for good: the launcher must try to reload
# ipts.ko after ~15 s (rmmod/insmod are stubs here).
python3 "$T/uhid_e2e.py" --desc-h "$DESC_H" --product 0x09B2 --name "Fake Digitizer" --timeout 60 2>&1 | tee "$W/e2e_check.txt"
[ "${PIPESTATUS[0]}" = 0 ] || rc=1
sleep 22
grep -q "reloading the ipts driver" "$W/launcher.log" && grep -q "stub rmmod ipts" "$W/launcher.log" \
	&& grep -q "stub insmod /nonexistent/ipts.ko" "$W/launcher.log" && echo "ok module reload attempted" \
	|| { echo "FAIL no module reload"; rc=1; }
kill -TERM -$LPID 2> /dev/null; sleep 1; kill -KILL -$LPID 2> /dev/null
m=$(stat -c %a "$W/odm/usr" "$IDCD" | tr '\n' ' '); f=$(stat -c %a "$IDCD"/*.idc | sort -u | tr '\n' ' ')
[ "$m" = "755 755 " ] && [ "$f" = "644 " ] && echo "ok runtime IDC dir modes under umask 077 (dirs $m files $f)" \
	|| { echo "FAIL runtime IDC modes: dirs $m files $f (system_server could not read them)"; rc=1; }

echo "=== 4. iptsd failing immediately on a present device -> ipts.ko reload after 5 failures"
printf '#!/bin/sh\necho "fake iptsd failing on $1"\nexit 1\n' > "$W/iptsd-fail"; chmod +x "$W/iptsd-fail"
env GB_IPTSD_BIN="$W/iptsd-fail" GB_IPTSD_CHECK="$W/check-chroot" GB_IPTSD_IDC="$IDCD" \
	GB_IPTSD_STATIC_IDC="$W/static_idc" GB_IPTSD_KO=/nonexistent/ipts.ko GB_IPTSD_USERCONF="$W/none.conf" \
	GB_IPTSD_SUSPEND_STATS="$W/no_such_file" GB_IPTSD_POLL=1 PATH="$W/stubs:$PATH" \
	setsid mksh "$OUT/gb_iptsd.sh" > "$W/launcher_fail.log" 2>&1 &
LPID=$!
python3 - "$T" "$DESC_H" <<'EOF'
import sys, time
sys.path.insert(0, sys.argv[1]); import ipts_frames as F, uhid_e2e as U
d = U.Uhid("IPTS 045E:09B2", F.descriptor(sys.argv[2], max_count=4000), 0x045E, 0x09B2)
end = time.time() + 40
while time.time() < end:
    d.poll(0.2, None)
d.destroy()
EOF
kill -TERM -$LPID 2> /dev/null; sleep 1; kill -KILL -$LPID 2> /dev/null
cat "$W/launcher_fail.log"
grep -q "iptsd failed 5 times in a row" "$W/launcher_fail.log" && grep -q "stub rmmod ipts" "$W/launcher_fail.log" \
	&& grep -q "no $W/no_such_file: no restart after resume" "$W/launcher_fail.log" \
	&& echo "ok reload after repeated failures" || { echo "FAIL no reload after repeated failures"; rc=1; }

echo "=== 5. persist.gb.iptsd.disable=1 with persist properties never 'ready': bounded wait, then idle (no exit)"
mkdir -p "$W/stubs2"; cp "$W/stubs/rmmod" "$W/stubs/insmod" "$W/stubs2/"
printf '#!/bin/sh\n[ "$1" = persist.gb.iptsd.disable ] && echo 1\nexit 0\n' > "$W/stubs2/getprop"; chmod +x "$W/stubs2/"*
env GB_IPTSD_BIN="$W/iptsd-fail" GB_IPTSD_IDC="$IDCD" GB_IPTSD_PROPWAIT=5 GB_IPTSD_SUSPEND_STATS="$W/no_such_file" \
	PATH="$W/stubs2:$PATH" setsid mksh "$OUT/gb_iptsd.sh" > "$W/launcher_disabled.log" 2>&1 &
LPID=$!
sleep 5
alive=0; kill -0 $LPID 2> /dev/null && alive=1
kill -TERM -$LPID 2> /dev/null; sleep 1; kill -KILL -$LPID 2> /dev/null
cat "$W/launcher_disabled.log"
[ $alive = 1 ] && [ "$(grep -c 'disabled (persist.gb.iptsd.disable=1)' "$W/launcher_disabled.log")" = 1 ] \
	&& ! grep -q "fake iptsd" "$W/launcher_disabled.log" && echo "ok disabled: idles, logged once, iptsd not run" \
	|| { echo "FAIL disabled handling (alive=$alive)"; rc=1; }
umount "$CH/dev"
echo "--- launcher log"; cat "$W/launcher.log"
echo "--- runtime IDCs"; for f in "$IDCD/"*; do echo "## $f"; cat "$f"; done
echo "=== RESULT: $([ $rc = 0 ] && echo PASS || echo FAIL)"
exit $rc
