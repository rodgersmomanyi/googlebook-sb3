# iptsd (native)

Touchscreen and Surface Pen. The linux-surface `ipts` kernel driver (MEI client on 8086:34E4) exposes the touch
controller as HID device "IPTS 045E:09B2"; [iptsd](https://github.com/linux-surface/iptsd) turns its heatmaps and pen
reports into two uinput devices that Android uses as a touchscreen and a stylus.

* `build/build_iptsd_android.sh` + `build/android-x86_64-ndk.ini` + `patches/0001-iptsd-android-ndk-build.patch`:
  iptsd **v3.1.1**, fully static NDK r27c build (meson wraps for every dependency; rebuilds are bit-identical).
  Config paths compiled in: `/odm/etc/iptsd/`.
* `gb_iptsd.sh` + `gb_iptsd.rc`: finds the hidraw node, writes missing IDC files to `/odm/usr/idc`, runs and restarts
  iptsd (exit, device reset, resume), reloads `ipts.ko` after the known resume failure. Knobs:
  `persist.gb.iptsd.disable`, `persist.gb.iptsd.reload`; user config `/data/vendor/iptsd/iptsd.conf`.
* `idc/`: IDC files for iptsd's virtual devices and the kernel's fallback HID input devices (13.5" = 09B2,
  15" = 09B1). Never add a `Vendor_045e_Product_09b2.idc`: it would match all of them.
* `etc/iptsd/`: iptsd's config and device presets (from iptsd, GPL-2.0), including the restored Surface Book 3
  presets.
* `test/`: `ipts_frames.py` + `uhid_e2e.py` (fake IPTS device on `/dev/uhid`), `run_tests.sh`.

Status: untested on hardware; end-to-end tested on the builder with the fake device.
