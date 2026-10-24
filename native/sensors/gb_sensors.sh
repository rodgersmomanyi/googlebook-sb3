#!/system/bin/sh
# Googlebook SB3 native: sensors helper (staged as /odm/gb_sensors.sh, run by gb.rc services).
#   wait  - exec_start'ed at early-boot, before class hal starts the sensors multi-HAL: the lapis IIO
#           sub-HAL scans /sys/bus/iio/devices only once, so wait (bounded) until the ISH accelerometer
#           (cros-ec-accel) and the APDS-9960 light device (cros-ec-light) have registered.
#   diag  - after boot_completed: IIO + sensorservice state to the kernel log (gb_bootlog copies dmesg),
#           and enable auto-rotate once (first native boot only).
exec >/dev/kmsg 2>&1
mode=$1

iio_names() { for n in /sys/bus/iio/devices/iio:device*/name; do [ -f "$n" ] && cat "$n"; done; }

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
	if [ "$(getprop persist.gb.sensors.rotinit)" != 1 ]; then
		settings put system accelerometer_rotation 1 && setprop persist.gb.sensors.rotinit 1
		echo "GB_SENSORS: auto-rotate enabled (first boot)"
	fi
	echo "GB_SENSORS: accelerometer_rotation=$(settings get system accelerometer_rotation) screen_brightness_mode=$(settings get system screen_brightness_mode)"
	;;
*)
	echo "usage: gb_sensors.sh wait|diag"
	;;
esac
exit 0
