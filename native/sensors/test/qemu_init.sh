#!/bin/busybox sh
# /init of the QEMU test initramfs: GKI build #11 bzImage + the Android-mode sensor modules.
/bin/busybox --install -s /bin
mkdir -p /proc /sys /dev /tmp
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mknod /dev/null c 1 3
mknod /dev/kmsg c 1 11
mknod /dev/uhid c 10 239
echo "=== TEST BEGIN $(uname -r)"
M=/lib/modules
for m in mfd-core kfifo_buf industrialio-triggered-buffer hid-sensor-hub hid-sensor-iio-common \
         hid-sensor-trigger hid-sensor-accel-3d hid-sensor-gyro-3d apds9960 i2c-stub i2c-dev; do
  case $m in i2c-stub) a="chip_addr=0x39";; *) a="";; esac
  insmod $M/$m.ko $a && echo "insmod $m ok" || echo "insmod $m FAILED"
done

echo "=== HID part: fake ISH sensor hub over uhid"
/bin/fake_ish 120 2>/tmp/fake_ish.log &
sleep 3
for d in /sys/bus/iio/devices/*; do echo "$d: name=$(cat $d/name 2>/dev/null) label=$(cat $d/label 2>/dev/null)"; done
for d in /sys/bus/iio/devices/iio:device*; do
  echo "--- $d ($(cat $d/name))"; ls $d | tr '\n' ' '; echo
  for f in scale sampling_frequency sampling_frequency_available in_accel_x_raw in_accel_y_raw in_accel_z_raw \
           in_anglvel_x_raw in_anglvel_y_raw in_anglvel_z_raw in_accel_hysteresis in_anglvel_hysteresis \
           buffer/hwfifo_timeout current_timestamp_clock trigger/current_trigger; do
    [ -e $d/$f ] && echo "  $f = $(cat $d/$f 2>&1)"
  done
done
echo "=== hal_probe (HID) 10 Hz"
/bin/hal_probe 10 6
echo "=== hal_probe (HID) 50 Hz"
/bin/hal_probe 50 4

echo "=== I2C part: APDS-9960 on i2c-stub (RGBC preset C=10000 R=4000 G=5000 B=3000)"
B=$(for a in /sys/bus/i2c/devices/i2c-*; do grep -l "SMBus stub" $a/name >/dev/null 2>&1 && basename $a; done | head -n1)
N=${B#i2c-}
echo "stub bus: $B"
mknod /dev/i2c-$N c 89 $N
# C, R, G, B little endian at 0x94..0x9b
i2cset -y $N 0x39 0x94 0x10; i2cset -y $N 0x39 0x95 0x27
i2cset -y $N 0x39 0x96 0xa0; i2cset -y $N 0x39 0x97 0x0f
i2cset -y $N 0x39 0x98 0x88; i2cset -y $N 0x39 0x99 0x13
i2cset -y $N 0x39 0x9a 0xb8; i2cset -y $N 0x39 0x9b 0x0b
echo apds9960 0x39 > /sys/bus/i2c/devices/$B/new_device
sleep 1
dmesg | grep -i -E "apds9960|cros-ec|Android mode" | tail -n 8
for d in /sys/bus/iio/devices/iio:device*; do
  case "$(cat $d/name)" in cros-ec-light|apds9960)
    echo "--- $d ($(cat $d/name))"; ls $d | tr '\n' ' '; echo
    for f in scale sampling_frequency sampling_frequency_available in_illuminance_raw in_intensity_clear_raw; do
      [ -e $d/$f ] && echo "  $f = $(cat $d/$f 2>&1)"
    done;;
  esac
done
echo "=== hal_probe (all, light at 4 Hz)"
/bin/hal_probe 4 5
echo "  ATIME/CONTROL after streaming (expect 0xdb = 100 ms, AGAIN 1 = 4x): $(i2cget -f -y $N 0x39 0x81) $(i2cget -f -y $N 0x39 0x8f)"
echo "=== light auto-range: dim scene C=500 R=200 G=250 B=150 (stub counts do not follow the gain, so lux steps with each range change)"
i2cset -f -y $N 0x39 0x94 0xf4; i2cset -f -y $N 0x39 0x95 0x01
i2cset -f -y $N 0x39 0x96 0xc8; i2cset -f -y $N 0x39 0x97 0x00
i2cset -f -y $N 0x39 0x98 0xfa; i2cset -f -y $N 0x39 0x99 0x00
i2cset -f -y $N 0x39 0x9a 0x96; i2cset -f -y $N 0x39 0x9b 0x00
L=$(for d in /sys/bus/iio/devices/iio:device*; do [ "$(cat $d/name)" = cros-ec-light ] && echo $d; done)
mknod /dev/$(basename $L) c $(cut -d: -f1 $L/dev) $(cut -d: -f2 $L/dev) 2>/dev/null
echo 1 > $L/scan_elements/in_illuminance_en; echo 1 > $L/scan_elements/in_timestamp_en; echo 8 > $L/sampling_frequency; echo 1 > $L/buffer/enable
timeout 3 cat /dev/$(basename $L) > /tmp/light.bin
echo 0 > $L/buffer/enable
echo "  after dim streaming: ATIME=$(i2cget -f -y $N 0x39 0x81) CONTROL=$(i2cget -f -y $N 0x39 0x8f) (expect 0xdb, 0x03 = 64x)"
hexdump -v -e '16/1 "%02x" "\n"' /tmp/light.bin | head -n 24 | while read l; do echo "  kfifo: $l lux=$(( 0x${l:2:2}${l:0:2} ))"; done
echo "=== runtime param android_axes"
P=/sys/module/hid_sensor_accel_3d/parameters/android_axes
A=$(for d in /sys/bus/iio/devices/iio:device*; do [ "$(cat $d/name)" = cros-ec-accel ] && echo $d; done)
echo "  accel default: $(cat $P)  gyro default: $(cat /sys/module/hid_sensor_gyro_3d/parameters/android_axes)"
echo "x,y,z" > $P; echo "  axes=$(cat $P): x=$(cat $A/in_accel_x_raw) y=$(cat $A/in_accel_y_raw) (expect 0 -4096)"
echo "-y,x,-z" > $P; echo "  axes=$(cat $P): x=$(cat $A/in_accel_x_raw) y=$(cat $A/in_accel_y_raw) (expect 4096 0)"
echo "x,x,z" > $P 2>/dev/null && echo "  FAIL: x,x,z accepted" || echo "  x,x,z rejected (ok), still $(cat $P)"
echo "-x,-y,-z" > $P; echo "  axes=$(cat $P): y=$(cat $A/in_accel_y_raw) (expect 4096)"
echo "=== stock ABI regression: reload with android=0"
rmmod hid-sensor-accel-3d hid-sensor-gyro-3d && echo "rmmod ok"
insmod $M/hid-sensor-accel-3d.ko android=0 && insmod $M/hid-sensor-gyro-3d.ko android=0 && echo "insmod android=0 ok"
sleep 2
for d in /sys/bus/iio/devices/iio:device*; do n=$(cat $d/name); case $n in accel_3d|gyro_3d|cros-ec-accel|cros-ec-gyro)
  echo "--- $d name=$n label=$(cat $d/label 2>/dev/null)"; ls $d | tr '\n' ' '; echo
  for f in $d/scan_elements/*_type; do echo "  $(basename $f) = $(cat $f)"; done
  for f in in_accel_scale in_anglvel_scale in_accel_y_raw in_anglvel_x_raw; do [ -e $d/$f ] && echo "  $f = $(cat $d/$f)"; done;;
esac; done
echo "=== fake_ish log"
cat /tmp/fake_ish.log
echo "=== dmesg (sensor lines)"
dmesg | grep -i -E "hid-sensor|hid_sensor|accel|gyro|cros-ec|apds|iio|Android mode|WARNING|BUG|Oops" | tail -n 40
echo "=== TEST END"
sync
poweroff -f
