# Drivers and device status

Source of truth per device: the **kernel** side (config symbol / module, and where the module comes from: *ours* =
our GKI build, *lapis* = lapis's vendor_ramdisk / vendor_dlkm), **firmware**, and **userspace** (lapis's own HAL or
our component). Module load order: `stage_kernel.py` (`VM_MODS`, `NATIVE_EARLY`, `NATIVE_LATE`; `NATIVE_OPTIN` is
staged in the dGPU payload and loaded only by the nouveau policy); kernel config: [kernel/config/gki_defconfig.diff](../kernel/config/gki_defconfig.diff)
plus the component config lines that `kernel/scripts/kbuild13_native.sh` adds (build #13). Native images run build
#16 since wave 8 (`kernel/scripts/kbuild16_native.sh`: build #15 plus the escaped task name in the VNNI counter and
the Realtek SD-card reader modules, which load only by modalias; the same KMI symbol list,
[kernel/K16.md](../kernel/K16.md)). Build #15 (`kernel/scripts/kbuild15_native.sh`) is build #14, which is build #13
plus nouveau-only patches, plus the reworked AVX-VNNI emulator and the opt-in i915 gamma switch; same config and KMI as
#13 ([kernel/K15.md](../kernel/K15.md)). The full
device-by-device audit, with ACPI evidence and a first-boot checklist: [DEVICE-STATUS.md](DEVICE-STATUS.md). The
NVIDIA stack in detail: [NVIDIA.md](NVIDIA.md).

Status words: **works** = in daily use in the VM. **untested on hardware** = built and integrated, only checked
off-device (the test is named). **opt-in** = in the native tree but off until a property enables it.
**not supported** = no way known.

## Surface Book 3 13.5" (native, `gb.hw=native`)

| Device | ID | Kernel | Firmware | Userspace | Status |
|---|---|---|---|---|---|
| Iris Plus graphics, eDP 3000x2000 panel | PCI 8086:8A52 | `CONFIG_DRM_I915=m` (ours) + `drm_display_helper`, `drm_buddy`, `i2c-algo-bit`, `video`, `wmi`, `intel-gtt` (ours, loaded before ueventd so lapis's copies never load) | `i915/icl_dmc_ver1_09.bin`, `icl_guc_70.1.1.bin`, `icl_huc_9.0.0.bin` | lapis: drm_hwcomposer (`vendor.hwc.drm.enable_color_pipeline=false`), Mesa ANV `vulkan.intel.so` (lists ICL GT2), ANGLE, minigbm i915 backend (has 0x8a52), iHD video; ours: 320 dpi (`ro.sf.lcd_density`, `gb_ui.sh`) | untested on hardware |
| NVIDIA GTX 1650 Max-Q (in the base) | PCI 10DE:1F91 (TU117) | `CONFIG_DRM_NOUVEAU=m` + `drm_exec`, `drm_gpuvm`, `gpu-sched`, `drm_ttm_helper` (ours; build #14 adds 37 linux-6.18.y nouveau backports and the hardening patches N1-N3, [kernel/patches/k14](../kernel/patches/k14/)), in the `gb/dgpu-<krel>.erofs` payload and in no load list. Idle or driverless: runtime PM -> root port 00:1d.4 D3cold via ACPI `_PR3` | `nvidia/tu117/*` (30 files: GSP-RM 570.144 + 535.113.01, ACR/GR/SEC2; linux-firmware tag 20260916, with WHENCE and `LICENCE.nvidia`) in the payload; you fetch it | ours: `gb_nouveau.sh` loads nouveau after `sys.boot_completed` in graphics mode Auto/NVIDIA when the base is attached (`runpm=1 modeset=2`, headless; crash-loop guard, `gb.nouveau=0` kill switch, `kernel.panic=30`); `gb_power` derives `vendor.gb.gfx_active`, gates detach on `refcnt`, unbinds/removes in a worker, guards the re-bind on reattach. Apps: GbGfxDriver (`ro.gfx.driver.0=gb.sb3.gfxdriver`) with a shim `vulkan.intel.so` that puts NVK (Mesa 26.3.0-rc2, patch set 8a) first for games and **Use NVIDIA** apps; HWUI and the system stay on ANV. GbGpu: QS tile and Settings page "Graphics". Self-test with `nvk_probe`; compute service `gb_gpu_compute` | untested on hardware (automatic in Auto mode; [NVIDIA.md](NVIDIA.md); `native/nouveau-kernel`, `native/power`, `native/nvk-android`, `native/gfx-driver-pkg`, `native/gpu-compute`, `native/gpu-ui`) |
| Touchscreen + Surface Pen (IPTS) | MEI 8086:34E4, HID 045E:09B2 | `mei-me` built-in + [patch 0003](../kernel/patches/) (34E4 id); `CONFIG_HID_IPTS=m` `ipts.ko` from linux-surface (ours) | - | ours: `iptsd` 3.1.1 static NDK build, `gb_iptsd.sh` launcher, IDC files ([native/iptsd](../native/iptsd)) | untested on hardware (iptsd end-to-end against a fake uhid device on the builder) |
| Surface Aggregator (SAM) | ACPI MSHW0084 on the LPSS UART | `CONFIG_SURFACE_AGGREGATOR=m` + `_REGISTRY`, `_HUB`, `_CDEV` (ours; `crc-itu-t`); UART: lapis `8250_dw`/`intel-lpss` | - | - | untested on hardware |
| Base keyboard + touchpad | SAM HID 045E:09AE / 09AF | `CONFIG_SURFACE_HID=m` (`surface_hid_core`, `surface_hid`, ours); `CONFIG_HID_SURFACE=m` `hid-surface` (build #13: drops the Fn "button" that makes Android see a gamepad; `native/hw-gap-audit` patch) | - | Android input; IDC files and `excluded-input-devices.xml` (hides the touchpad's vendor collections) from `native/hw-gap-audit`; `gb_power` inhibits them in studio mode | untested on hardware |
| CPU package power limits | MSR 0x610 | lapis `msr.ko`, powercap | - | `gb_hw.sh` restores the Surface firmware's PL1/PL2 after lapis's init sets 25/55 W (`native/hw-gap-audit`, `hw.json`) | untested on hardware |
| Batteries (clipboard BAT1 + base BAT2) | SAM | `CONFIG_BATTERY_SURFACE=m` + [native/power patch 0001](../native/power/patches/) (`scope_device=1`: health HAL skips them); `CONFIG_TEST_POWER=m` + patch 0002 (`charge_full`, temp) | - | lapis health HAL; ours: `gb_power` sums both packs energy-weighted into `test_battery` (level and time-to-full) | untested on hardware (`native/power/test/test_gb_power.c`) |
| Charger / AC | SAM ADP1 | `CONFIG_CHARGER_SURFACE=m` | - | `gb_power` mirrors ADP1 into `test_ac` | untested on hardware |
| Detach (DTX), tablet/studio mode | SAM | `CONFIG_SURFACE_DTX=m` + [patch 0003](../native/power/patches/) (latch event fix) | - | `gb_power` (detach handshake like surface-dtx-daemon; with nouveau bound: app graphics to Intel first, latch locked, GPU services stopped, `refcnt` gate, unbind + PCI remove in a worker, then confirm; refused with a notification while apps hold the dGPU or a base drive is in use; `vendor.gb.dtx.*`); `SW_TABLET_MODE` from surface_dtx | untested on hardware |
| Base hot-plug | SAM | `CONFIG_SURFACE_HOTPLUG=m` | - | `gb_power`: staged PCI rescan after a detach/cancel, the re-bind guard when the kernel re-probes the dGPU on reattach, the hot-attach start of `gb_nouveau` | untested on hardware |
| Platform profile | SAM | `CONFIG_SURFACE_PLATFORM_PROFILE=m` (ours, late pass) on lapis's `platform_profile` | - | `gb_power -p`, `persist.vendor.gb.perf_profile` | untested on hardware |
| Lid / wake GPE | ACPI | `CONFIG_SURFACE_GPE=m` | - | - | untested on hardware |
| Power + volume buttons | ACPI MSHW0040 | `CONFIG_INPUT_SOC_BUTTON_ARRAY=m` | - | Android input | untested on hardware |
| Wi-Fi AX201 | PCI 8086:34F0 | lapis `iwlwifi`/`iwlmvm` (vendor_dlkm) + ours: `libarc4`, `pps_core`, `ptp`, `rfkill` (lapis's signed copies are refused) | `iwlwifi-Qu*-hr-b0-77.ucode` | lapis AOSP Wi-Fi HAL, wpa_supplicant | untested on hardware |
| Bluetooth | USB 8087:0026 | ours: `bluetooth`, `btbcm`, `hidp`, `rfcomm`; USB transport from lapis vendor_dlkm (inferred: btusb/btintel) | `intel/ibt-19-*.sfi/.ddc` | lapis Bluetooth HAL (HCI user channel) | untested on hardware |
| Audio: DSP + ALC274 + DMICs + HDMI | PCI 8086:34C8 | lapis SOF (`snd-sof-pci-intel-icl`, IPC3) + lapis HDA codec modules preloaded by gbinit (GKI cannot `request_module`); `snd_intel_dspcfg.dsp_driver=3` | `intel/sof/sof-icl.ri`, `intel/sof-tplg/sof-hda-generic-2ch.tplg` | lapis DRAS HAL with our `sof-hda-dsp_sb3` card config, `gb_audio.sh`, `gb_audiodiag.sh` ([native/audio](../native/audio)) | untested on hardware |
| Accelerometer + gyroscope (ISH) | PCI 8086:34FC | lapis ISH transport; ours: `hid-sensor-hub` (late pass), `hid-sensor-accel-3d`, `-gyro-3d` (+ als, rotation, incl, magn) with [native/sensors patch 0001](../native/sensors/patches/) (build #13; named `cros-ec-accel`/`-gyro`, the ChromeOS EC devices lapis's IIO sub-HAL accepts) | ISH firmware is in the device | lapis sensors multi-HAL (Desktop IIO sub-HAL); ours: `gb_sensors.sh`, auto-rotate overlay, feature XML (`sensors.json`) | untested on hardware (QEMU with a fake ISH) |
| Ambient light | ACPI MSHW0184 (APDS-9960, I2C) | `CONFIG_APDS9960=m` + sensors patch 0002 (`cros-ec-light` companion) | - | same | as above |
| Thermal | ACPI | `CONFIG_ACPI_THERMAL=m` (ours) + lapis `processor_thermal` | - | lapis thermal HAL | untested on hardware |
| Internal NVMe | PCI behind root port 00:1d.0 | built-in | - | boot by `androidboot.boot_part_uuid` | untested on hardware (internal install) |
| USB stick boot | xHCI 00:14.0 / Thunderbolt xHCI 00:0d.0 | built-in | - | `gb.boot=usb` drops the vold `usb` entry | untested on hardware |
| USB Ethernet adapters, SurfaceDock | USB | ours: `usbnet`, `cdc_ether`, `cdc_ncm`, `r8152`, `ax88179_178a`, `mii`, `cdc-acm` | `rtl_nic/rtl815[36]*.fw` (optional PHY patches, you fetch them) | EthernetTracker | untested on hardware |
| Serial IO: LPSS UART/I2C, GPIO | PCI 34A8 / 34C5 / 34E8-34EB, INT3455 | lapis only: `intel-lpss(-pci)`, `mfd-core`, `pinctrl-intel(-platform)` in its first stage, `pinctrl-icelake`, `i2c-designware` from vendor_dlkm. Our builds of them are never loaded: preloading them broke lapis's first stage in the VM test of build #13. The 8250 core is built in with `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`, so the native command line keeps `8250.nr_uarts=4` (wave 3): without it the UART that carries the Surface Aggregator fails with `-22` | - | - | untested on hardware |
| Cameras: front OV5693, rear OV8865 (+ DW9719 focus), IR OV7251 | IPU4P PCI 8086:8A19 | no upstream Linux driver; ours (build #13): the IPU4P port of the ipu6 driver (`intel-ipu6`, `intel-ipu6-isys`, [native/camera-kernel](../native/camera-kernel)) + `ov5693`, `ov8865`, `ov7251`, `dw9719`, `ipu-bridge`, `intel_skl_int3472_common`/`_discrete`, `v4l2-cci`, loaded by gbinit before lapis's own copies | `intel/ipu/ipu4p_fw.bin`: Intel's, from your Windows camera driver, not included ([INPUTS.md](INPUTS.md#firmware-native-only)) | ours: libcamera 0.7.2 simple pipeline + software ISP as `camera.sb3.so` (`ro.hardware.camera=sb3`, [native/camera-userspace](../native/camera-userspace)) | untested on hardware (the kernel side is built in #13; the HAL was tested against vimc) |
| TPM, SPI-NOR, gsmi | - | blacklisted on the command line | - | - | intentionally off |

## VMware VM (`gb.hw=vm`)

| Device | Kernel | Userspace | Status |
|---|---|---|---|
| SVGA3D display + GPU | `CONFIG_DRM_VMWGFX=m` + [patch 0001](../kernel/patches/) (RGBA dumb buffers, ABGR cursor, dma-buf import and leak/UAF fixes), `efidrm`, `drm_ttm_helper` | Mesa svga 26.3.0-rc2 + [our patch](../graphics/mesa/patches/) as system EGL/GLES (SkiaGL), minigbm patched by `patch_minigbm.py`, drm_hwcomposer (no colour pipeline, plain primary plane, cursor plane), forced EDID, `gb_layout` 2560x1600 | **works** |
| Vulkan | - | SwiftShader `vulkan.pastel.so` overlaid as `vulkan.intel.so` (CPU) | **works** (slow) |
| Absolute mouse, seamless pointer | `psmouse` with `CONFIG_MOUSE_PS2_VMMOUSE` | `gb_absmouse` (uinput "GB Absolute Mouse" + "GB Mouse Wheel"), `gb_vmtools` (Tools "running") | **works** |
| Keyboard | i8042 built-in | IDC marks it external; touchpad-inhibitor stubbed | **works** |
| Network | `CONFIG_VMXNET3=m` | EthernetTracker (DHCP); `gb_netwatch.sh` cycles eth0 when it has carrier but no IPv4 for 30 s (Android's Ethernet stack sometimes stops re-provisioning it) | **works** |
| Sound | lapis legacy HDA (`snd-hda-intel` + codecs) on VMware `hdaudio` (VMX `sound.minProgressUsec`, `maxPeriodSizeMS`, `bufferIdealMS` for low latency) | `gb_vmaudio.sh`, DRAS with the `Generic_vmware` config | **works** |
| Battery / AC | `test_power` (lapis's ACPI battery unbound) | `gb_hostbatt` -> `gb_vmtools` (Windows' combined level and AC state, live) | **works** |
| Remote window | - | `gb_adb.sh` moves adbd to tcp:5555 on the NAT address (your ADB key); `host\Googlebook-scrcpy.cmd` (scrcpy 5.0.1: screen, input, clipboard, audio) | **works** (optional) |
| Power off / restart from VMware | - | `gb_vmtools` (TCLO OS_Halt / OS_Reboot) | **works** |
| Serial console | 8250 | `gb_console` on ttyS1 <-> `gbsh.py` | **works** |
| Bluetooth, sensors, touch, cameras | - | Bluetooth features marked unavailable | n/a |

## CPU instruction gaps

lapis binaries use AVX-VNNI unconditionally; Ice Lake (and the VM, whose CPUID hides it) lacks it. Kernel
[patch 0002](../kernel/patches/) emulates the VEX VNNI instructions in the #UD handler. AVX-IFMA is not emulated.
