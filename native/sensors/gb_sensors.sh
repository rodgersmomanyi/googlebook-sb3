#!/system/bin/sh
# Googlebook SB3 native: sensors helper (staged as /odm/gb_sensors.sh, run by gb.rc services).
#   wait  - exec_start'ed at early-boot, before class hal starts the sensors multi-HAL: the lapis IIO
#           sub-HAL scans /sys/bus/iio/devices only once, so wait (bounded) until the ISH accelerometer
#           (cros-ec-accel) and the APDS-9960 light device (cros-ec-light) have registered.
#   diag  - after boot_completed: IIO + sensorservice state to the kernel log (gb_bootlog copies dmesg),
#           and enable auto-rotate once (first native boot only) for the CURRENT user, which at boot_completed is the
#           headless system user 0 (the login screen; lapis runs in headless system user mode).
#   user <id> - (wave 7, headless-settings) the person's FULL user <id>: gb_ui.sh runs it once per user ever, at the
#           user's first unlock (gb_ui.sh's marker), so the person's own later choice is never overwritten. It turns
#           auto-rotate on only when the user's value is not 1 already (GbSb3SettingsOverlay gives a new user 1).
#   rotation - (readiness A-L3) after boot_completed and at every DTX device-mode change (vendor.gb.dtx.mode, set by
#           gb_power): one read-only 'GB_SENSORS: rotation:' line (rot_report) per change, at most 3 per start.
# Test hooks: GB_SENSORS_LOG=- logs to stdout instead of /dev/kmsg; GB_SENSORS_SETTLE = seconds to let Android apply a
# posture change before 'rotation' reads it (default 3).
[ "${GB_SENSORS_LOG:-}" = - ] || exec >/dev/kmsg 2>&1
mode=$1

iio_names() { for n in /sys/bus/iio/devices/iio:device*/name; do [ -f "$n" ] && cat "$n"; done; }

# (readiness A-L3) Rotation-locked in laptop posture, auto-rotate in tablet posture: lapis 276 does this itself.
# WindowManager's DisplayRotation.LaptopController (flag enable_auto_rotate_on_slate_state, compiled in as true) fixes
# the built-in display to the user rotation, set to landscape, in the device state OPEN (LID_OPEN), and releases it in
# TABLET (SLATE: SW_TABLET_MODE from surface_dtx with the lid open = clipboard detached or base reversed), where the
# user's own Auto-rotate setting (accelerometer_rotation) applies. The controller exists only while
# config_supportAutoRotation is true (GbSb3SensorsOverlay; lapis's own overlay sets false), and the locked default
# rotation of a PC (android.hardware.type.pc) applies in every other state. Nothing here writes a setting: rot_report
# only logs what Android decided, with a verdict. Every Android call is bounded (timeout 20); only display 0's
# DisplayRotation fields, the device-state name/id and plain tokens are printed (never window or app names).
rot_val() {         # $1 = value, $2 = text for an empty value (default ?): $1 if it is one plain token, else ?
	case "$1" in '') echo "${2:-?}";; *[!A-Za-z0-9_.:-]*) echo '?';; *) echo "$1";; esac
}
rot_display0() {    # "support_auto fixed_to_user user_rotation_mode user_rotation rotation" of display 0 (? = not found)
	timeout 20 dumpsys window displays 2>/dev/null | awk '
		/Display: mDisplayId=/ { d = $0; sub(/.*Display: mDisplayId=/, "", d); sub(/[^0-9].*$/, "", d); on = (d == "0"); dr = 0; next }
		on && /^ *DisplayRotation *$/ { dr = 1; next }
		dr && /RotationHistory|RotationLockHistory|FoldController/ { dr = 0 }
		dr && /^ *mRotation=[0-9]/ { s = $0; sub(/^ *mRotation=/, "", s); sub(/[^0-9].*$/, "", s); rot = s }
		dr && /^ *mSupportAutoRotation=/ { s = $0; sub(/^ *mSupportAutoRotation=/, "", s); sub(/[^a-z].*$/, "", s); sup = s }
		dr && /^ *mFixedToUserRotation=/ { s = $0; sub(/^ *mFixedToUserRotation=/, "", s); sub(/[^a-z].*$/, "", s); fix = s }
		dr && /^ *mUserRotationMode=/ {
			s = $0; sub(/^ *mUserRotationMode=/, "", s); m = s; sub(/[^A-Z_].*$/, "", m); mode = m
			if (match(s, / mUserRotation=[A-Z_0-9]+/)) urot = substr(s, RSTART + 15, RLENGTH - 15)
		}
		END {
			printf "%s %s %s %s %s\n", (sup == "" ? "?" : sup), (fix == "" ? "?" : fix), (mode == "" ? "?" : mode),
				(urot == "" ? "?" : urot), (rot == "" ? "?" : rot)
		}'
}
rot_verdict() {     # $1 device state, $2 support_auto, $3 fixed_to_user, $4 user_rotation_mode, $5 DTX mode
	case "$2" in
	true)
		case "$1:$3" in
		OPEN:true) v="ok: laptop posture, rotation locked (lapis LaptopController)" ;;
		OPEN:false) v="MISMATCH: laptop posture but the display follows the accelerometer (readiness A-L3)" ;;
		TABLET:false)
			case "$4" in
			USER_ROTATION_FREE) v="ok: tablet posture, auto-rotate on" ;;
			USER_ROTATION_LOCKED) v="ok: tablet posture, auto-rotate off by the user's Auto-rotate setting" ;;
			*) v="ok: tablet posture, the display follows the user's Auto-rotate setting" ;;
			esac ;;
		TABLET:true) v="MISMATCH: tablet posture but the display is fixed to the user rotation (no auto-rotate)" ;;
		*) v="info: no rotation expectation for device state $1" ;;
		esac ;;
	false) v="WARN: config_supportAutoRotation is off (GbSb3SensorsOverlay not applied? cmd overlay list): no auto-rotate in any posture" ;;
	*) v="info: no DisplayRotation data (dumpsys window displays)" ;;
	esac
	case "$5:$1" in
	laptop:TABLET|tablet:OPEN|studio:OPEN) v="$v; note: DTX mode $5 disagrees with the device state $1" ;;
	esac
	echo "$v"
}
rot_report() {      # $1 = what triggered it. Reads only.
	t=$1
	set -- $(timeout 20 dumpsys device_state 2>/dev/null | grep 'mCommittedState=' | head -n 1 |
		sed -n "s/.*identifier=\([0-9]*\), name='\([A-Za-z_]*\)'.*/\2 \1/p")
	ds=$(rot_val "${1:-}"); dsid=$(rot_val "${2:-}")
	set -f; set -- $(rot_display0); set +f      # no globbing of a '?' field
	sup=$(rot_val "${1:-}"); fix=$(rot_val "${2:-}"); urm=$(rot_val "${3:-}"); ur=$(rot_val "${4:-}"); r=$(rot_val "${5:-}")
	dtx=$(rot_val "$(getprop vendor.gb.dtx.mode 2>/dev/null)" -)
	acc=$(rot_val "$(timeout 20 settings get system accelerometer_rotation 2>/dev/null | head -n 1)")
	u=$(rot_val "$(timeout 20 am get-current-user 2>/dev/null | head -n 1)")
	echo "GB_SENSORS: rotation: $t: device_state=$ds($dsid) dtx=$dtx support_auto=$sup fixed_to_user=$fix user_rotation_mode=$urm user_rotation=$ur rotation=$r accelerometer_rotation=$acc user=$u => $(rot_verdict "$ds" "$sup" "$fix" "$urm" "$dtx")"
}

case "$mode" in
wait)
	want=
	for d in /sys/bus/pci/devices/*; do
		[ "$(cat $d/vendor 2>/dev/null):$(cat $d/device 2>/dev/null)" = "0x8086:0x34fc" ] && want="cros-ec-accel"
	done
	[ -d /sys/bus/acpi/devices/MSHW0184:00 ] && want="$want cros-ec-light"
	if [ -z "$want" ]; then echo "GB_SENSORS: no ISH (8086:34fc) / MSHW0184, not waiting"; exit 0; fi
	i=0; miss=$want
	while [ $i -lt 80 ]; do
		have=" $(iio_names | tr '\n' ' ') "
		miss=
		for w in $want; do case "$have" in *" $w "*) ;; *) miss="$miss $w";; esac; done
		[ -z "$miss" ] && break
		sleep 0.1
		i=$((i + 1))
	done
	echo "GB_SENSORS: waited $((i * 100)) ms for:$want; iio:$(iio_names | tr '\n' ' ') missing:${miss:- none}"
	;;
diag)
	for d in /sys/bus/iio/devices/iio:device*; do
		[ -d "$d" ] || continue
		echo "GB_SENSORS: $(basename $d) name=$(cat $d/name) label=$(cat $d/label 2>/dev/null) scale=$(cat $d/scale 2>/dev/null) freq=$(cat $d/sampling_frequency 2>/dev/null) avail=[$(cat $d/sampling_frequency_available 2>/dev/null)] buffer_enable=$(ls -l $d/buffer/enable 2>/dev/null | cut -d' ' -f1,3,4)"
	done
	for p in /sys/module/hid_sensor_accel_3d/parameters /sys/module/hid_sensor_gyro_3d/parameters /sys/module/apds9960/parameters; do
		[ -d $p ] && echo "GB_SENSORS: $p: $(for f in $p/*; do echo -n "$(basename $f)=$(cat $f) "; done)"
	done
	dumpsys sensorservice 2>&1 | head -n 60 | while IFS= read -r l; do echo "GB_SENSORS: $l"; done
	# (W7, safety audit W6-S5) GbSb3SettingsOverlay.apk (readiness A-L3 D2) already makes Auto-rotate the default of
	# every user created on the device: while it is enabled, nothing here writes a rotation setting, whatever
	# persist.gb.sensors.rotinit says. The one-time write stays only as the fallback for an overlay that is not applied.
	if timeout 20 cmd overlay list --user 0 2>/dev/null |
		grep -q '^ *\[x\] com\.android\.providers\.settings\.gb_sb3_rotation_rro_vendor__$'; then
		echo "GB_SENSORS: auto-rotate default from GbSb3SettingsOverlay (enabled): no first-boot write"
	elif [ "$(getprop persist.gb.sensors.rotinit)" != 1 ]; then
		settings put system accelerometer_rotation 1 && setprop persist.gb.sensors.rotinit 1
		echo "GB_SENSORS: auto-rotate enabled (first boot)"
	fi
	echo "GB_SENSORS: accelerometer_rotation=$(settings get system accelerometer_rotation) screen_brightness_mode=$(settings get system screen_brightness_mode)"
	rot_report diag
	;;
user)
	u=${2:-}
	case "$u" in ''|0*|*[!0-9]*) echo "GB_SENSORS: user: refused user id '$(rot_val "$u")' (a full user, never 0)"; exit 0;; esac
	[ ${#u} -le 5 ] || { echo "GB_SENSORS: user: refused user id (more than 5 digits)"; exit 0; }
	v=$(rot_val "$(timeout 20 settings get --user "$u" system accelerometer_rotation 2>/dev/null | head -n 1)")
	if [ "$v" = 1 ]; then
		echo "GB_SENSORS: user $u: auto-rotate already on (accelerometer_rotation=1, the GbSb3SettingsOverlay default)"
	elif timeout 20 settings put --user "$u" system accelerometer_rotation 1 > /dev/null 2>&1; then
		echo "GB_SENSORS: user $u: auto-rotate enabled at its first unlock (accelerometer_rotation was $v)"
	else
		echo "GB_SENSORS: user $u: settings put accelerometer_rotation failed (was $v)"
	fi
	;;
rotation)
	st=${GB_SENSORS_SETTLE:-3}; case "$st" in ''|*[!0-9]*) st=3;; esac; [ ${#st} -gt 2 ] && st=30
	m=$(getprop vendor.gb.dtx.mode 2>/dev/null); n=0
	while :; do
		sleep "$st"     # DeviceStateManager and WindowManager apply a posture change asynchronously
		rot_report "dtx $(rot_val "$m" -)"
		n=$((n + 1)); m2=$(getprop vendor.gb.dtx.mode 2>/dev/null)
		# init ignores a 'start' while this oneshot still runs: report a change made meanwhile (3 lines at most)
		[ "$m2" != "$m" ] && [ $n -lt 3 ] || break
		m=$m2
	done
	;;
*)
	echo "usage: gb_sensors.sh wait|diag|rotation|user <id>"
	;;
esac
exit 0
