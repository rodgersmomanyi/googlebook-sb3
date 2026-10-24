# Kernel

We boot our own build of the **same GKI source** lapis ships (AOSP `kernel/common`, branch android17-6.18-2026-06 at
`3938c8d97142`, 6.18.21, build number 16302624), so lapis's vendor modules keep loading, plus our patches and extra
modules. Built with Kleaf on the Linux builder ([builder/README.md](../builder/README.md)).

## Patches (apply in `common/`, in this order)

| File | What | Used by |
|---|---|---|
| [patches/0001-drm-vmwgfx-...](patches/) | vmwgfx fixes for Android (GB_RGBA_DUMB, GB_HWCURSOR, GB_DUMB_IMPORT, GB_PRIME_LEAK, GB_PRIME_NOHANDLE, GB_DUMB_BASE_OWNS, GB_DUMB_DETACH) | VM |
| [patches/0002-x86-traps-...](patches/) | #UD emulation of VEX AVX-VNNI (GB_VNNI_EMULATION) | VM + native |
| [patches/0003-mei-me-...](patches/) | MEI device ID 8086:34E4 (IPTS) | native |
| linux-surface `patches/6.18/0005-ipts.patch`, **drivers/hid part only**: `git apply --include='drivers/hid/*' 0005-ipts.patch` (not included here; GPL-2.0, md5 `5af0a1a8bbfb9d7038a43674508e5938`) | IPTS touch/pen driver `drivers/hid/ipts` + its Kconfig/Makefile hooks ([patches/0004](patches/) records the hooks; do not apply it as well) | native |
| [../native/power/patches/0001-0003](../native/power/patches/) | `surface_battery scope_device`, `test_power charge_full/temp`, `surface_dtx` latch event fix | native (`test_power` also VM) |
| [../native/sensors/patches/0001-0002](../native/sensors/patches/) | HID sensors / APDS-9960 in the form lapis's IIO sensors HAL accepts | native, **not yet in the default build** |
| [../native/hw-gap-audit/kernel/0001-HID-surface-...](../native/hw-gap-audit/kernel/) | `hid-surface`: drop the SAM keyboard's bogus Fn button (`CONFIG_HID_SURFACE=m`) | native, proposed for build #12 |
| [config/gki_defconfig.diff](config/gki_defconfig.diff) | config delta: vmwgfx, efidrm, psmouse+vmmouse, vmxnet3, ACPI ac/battery/thermal, test_power, i915, Surface Aggregator stack, SAM HID, batteries/charger, DTX, GPE, hot-plug, platform profile, soc_button_array, HID sensors, APDS-9960, IPTS | |
| [config/BUILD.bazel.diff](config/BUILD.bazel.diff) | the extra `.ko` outputs in `module_implicit_outs` / `modules_superset` | |

Every file applies with `git apply --check` to pristine base files (checked 2026-10-24).
`config/power-and-dtx.diff` is a snapshot of the power patches as applied in our build tree, for reference.

Experimental, not in the default build: `native/nouveau-kernel/kernel/` (nouveau fragment + `kleaf_add_nouveau.py`),
`native/camera-kernel/kernel/` (IPU4P port).

## Build

```bash
# once: sync the pinned tree (manifest_16302624.xml) into /build/gki
bash kernel/scripts/gki_setup_sync.sh
cd /build/gki/common && git apply .../kernel/patches/000{1,2,3}-*.patch && git apply --include='drivers/hid/*' .../0005-ipts.patch
git apply .../native/power/patches/*.patch .../kernel/config/gki_defconfig.diff .../kernel/config/BUILD.bazel.diff
cd /build/gki
BUILD_NUMBER=16302624 ./tools/bazel --output_user_root=/build/.bazel \
    run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=/build/dist
```

`--config=local` keeps an incremental out dir (a vmwgfx-only change rebuilds in minutes). Then on Windows:
`python -I -B stage_kernel.py <tag>` copies `bzImage` and the VM + native modules into the workspace, writes
`modules.load` / `modules.load.late` with the right order and parameters, and refuses to stage when a dependency is
loaded too late or the vermagics differ (`--dry-run` to only check).

Rules learned the hard way:

* lapis's Google-signed GKI modules (`system_dlkm`) are refused by our kernel (protected symbols), so every GKI-side
  module we need is built here.
* Our helper modules' exports are protected/trimmed: never load our copy of a module that lapis's vendor modules
  link against (e.g. `mfd-core`, `platform_profile`): those come from lapis (late load pass).

## History

`scripts/` holds the build scripts in the order they were used (`kbuild_rgba_psmouse.sh` = build #3 ...
`kbuild11_native.sh` = build #11) and the source patchers (`patch_*.py`, `apply_vnni*.py`) that produced the
changes now captured in `patches/`. They hard-code the builder's paths (`/build/gki`, `/build/dist`).
