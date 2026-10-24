#!/usr/bin/env python3
# Turns the raw diff -Naur output (p1.diff, p2.diff) into git-am/git-apply/patch -p1 compatible patch files.
import re
import sys

d = sys.argv[1]


def gitify(raw):
    out = []
    lines = raw.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("--- a/") and i + 1 < len(lines) and lines[i + 1].startswith("+++ b/"):
            pa = l[6:].split("\t")[0].strip()
            pb = lines[i + 1][6:].split("\t")[0].strip()
            new = "1970-01-01" in l
            out.append("diff --git a/%s b/%s\n" % (pa, pb))
            if new:
                out.append("new file mode 100644\n")
                out.append("--- /dev/null\n")
            else:
                out.append("--- a/%s\n" % pa)
            out.append("+++ b/%s\n" % pb)
            i += 2
            continue
        out.append(l)
        i += 1
    return "".join(out)


HDR = """From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Googlebook SB3 native bring-up <gb-sb3@localhost>
Date: Sat, 24 Oct 2026 19:00:00 +0000
Subject: [PATCH {n}/2] {subject}

{body}
---
"""

P1_SUBJ = "iio: hid-sensors: present ISH accel/gyro as cros-ec-accel/cros-ec-gyro for the Android IIO sensors HAL"
P1_BODY = """The lapis sensors sub-HAL (android.hardware.sensors@2.1-Desktop-IIO-Subhal.so) only accepts IIO
devices named like ChromeOS EC sensors and decodes samples the cros_ec_sensors way: s16 channels packed
in the first 8 bytes of a 16-byte scan, CLOCK_BOOTTIME timestamp in the last 8 bytes, one shared
"scale" and "sampling_frequency" (+_available), label accel-display/accel-base, Android axis signs.
With android=1 (default) hid-sensor-accel-3d (usage 0x200073 only; gravity keeps its ABI) and
hid-sensor-gyro-3d register as cros-ec-accel / cros-ec-gyro with that layout: samples are converted to
s16 counts (accel 1/4096 g, gyro 1 mrad/s), axes remapped by android_axes (accel default -x,-y,-z: HID/Windows gravity sign), timestamps
from ktime_get_boottime_ns(), change sensitivity set to 0, 0 Hz writes ignored. android=0 restores
the upstream ABI. Parameters: android, android_label, android_axes (0644), android_hyst0."""

P2_SUBJ = "iio: light: apds9960: add a cros-ec-light (lux) companion IIO device for the Android sensors HAL"
P2_BODY = """The Surface Book 3 ambient light sensor (ACPI MSHW0184) is an APDS-9960, whose IIO device only exposes
raw RGBC counts and is ignored by the lapis sensors sub-HAL. With android=1 (default) the driver also
registers "cros-ec-light": u16 lux in scan channel 0, CLOCK_BOOTTIME timestamp in the last 8 bytes of a
16-byte scan, shared "scale" (1) and "sampling_frequency" (+_available 1/2/4/8 Hz), kfifo buffer fed by a
freezable delayed work while enabled, auto-range over gain/integration time, ams DN40 RGBC lux equation
with a glass attenuation parameter (android_ga_x100, 0644). A missing IRQ is no longer fatal in this mode
(only threshold events/gestures need it). The original apds9960 IIO device is unchanged."""


def stat(raw):
    files = re.findall(r"^\+\+\+ b/(\S+)", raw, re.M)
    add = sum(1 for l in raw.splitlines() if l.startswith("+") and not l.startswith("+++"))
    rem = sum(1 for l in raw.splitlines() if l.startswith("-") and not l.startswith("---"))
    return " %d files changed, %d insertions(+), %d deletions(-)\n %s\n\n" % (len(files), add, rem, "\n ".join(files))


for n, (src, dst, subj, body) in enumerate([
        ("p1.diff", "0001-iio-hid-sensors-android-cros-ec-accel-gyro.patch", P1_SUBJ, P1_BODY),
        ("p2.diff", "0002-iio-apds9960-android-cros-ec-light.patch", P2_SUBJ, P2_BODY)], 1):
    raw = open("%s/%s" % (d, src), newline="\n").read()
    g = gitify(raw)
    open("%s/%s" % (d, dst), "w", newline="\n").write(HDR.format(n=n, subject=subj, body=body) + stat(raw) + g + "-- \n2.43.0\n")
    print("wrote", dst)
