#!/bin/bash
# QEMU (TCG) test of the Android-mode sensor modules on the real GKI build #11 kernel (/build/dist/bzImage).
# Runs on the builder only; uses /build/native/sensors/vmtest. Nothing here touches /build/gki/common.
#   KSRC = private kernel copy with the patches applied and the test modules built by vmtest/build_mods.sh
set -e
W=/build/native/sensors/vmtest
KSRC=/build/native/sensors/ksrc
T=/build/native/sensors/test
R=$W/rootfs
sudo rm -rf $R; mkdir -p $R/bin $R/lib/modules $R/dev
sudo mknod $R/dev/console c 5 1
cp /bin/busybox $R/bin/busybox
gcc -O2 -static -Wall -o $R/bin/fake_ish $T/fake_ish.c
gcc -O2 -static -Wall -o $R/bin/hal_probe $T/hal_probe.c -lm
cp $T/qemu_init.sh $R/init; chmod 755 $R/init
for m in mfd-core kfifo_buf industrialio-triggered-buffer hid-sensor-hub hid-sensor-iio-common hid-sensor-trigger; do
  cp /build/dist/$m.ko $R/lib/modules/
done
cp $KSRC/drivers/iio/accel/hid-sensor-accel-3d.ko $KSRC/drivers/iio/gyro/hid-sensor-gyro-3d.ko \
   $KSRC/drivers/iio/light/apds9960.ko $W/extmod/i2c-stub.ko $W/extmod/i2c-dev.ko $R/lib/modules/
# our test-built modules must carry the build-#11 signature: MODULE_SIG_PROTECT keeps unsigned modules
# away from non-KMI exports. Key = the Kleaf signing key of the kernel build under test, copied to $W/key first:
#   mkdir -p $W/key && cp /build/.bazel/*/execroot/_main/bazel-out/k8-fastbuild/bin/common/kernel_x86_64/signing_key.{pem,x509} $W/key/
# Test modules come from vmtest/build_mods.sh (same vermagic as /build/dist).
for m in hid-sensor-accel-3d hid-sensor-gyro-3d apds9960 i2c-stub i2c-dev; do
  /build/native/sensors/kout/scripts/sign-file sha1 $W/key/signing_key.pem $W/key/signing_key.x509 $R/lib/modules/$m.ko
done
(cd $R && sudo find . | sudo cpio -o -H newc 2>/dev/null | gzip -1) > $W/initramfs.cpio.gz
timeout 900 qemu-system-x86_64 -machine q35 -cpu max -m 1024 -smp 2 -nographic -no-reboot \
  -kernel /build/dist/bzImage -initrd $W/initramfs.cpio.gz \
  -append "console=ttyS0 8250.nr_uarts=4 rdinit=/init panic=-1 loglevel=4" > $W/qemu.log 2>&1 || true
sed -n '/=== TEST BEGIN/,/=== TEST END/p' $W/qemu.log
