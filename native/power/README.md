# power (native)

`gb_power.c`: one event-driven NDK daemon (poll on kernel uevents + `/dev/surface/dtx`, idle at 0 % CPU), started
at early-init right after the module pass:

* **Battery**: sums BAT1 (clipboard) and BAT2 (base) energy-weighted into `test_power`'s `test_battery`, mirrors
  ADP1 into `test_ac`, keeps the health HAL off BAT1/BAT2 (kernel patch 0001 `scope_device=1`, or a bind mount of a
  file reading "Unknown" over `BATn/type` on a kernel without it). With patch 0002 the HAL gets real charge values,
  so the level and the charge-time estimate are right.
* **Detach (DTX)**: confirms detach requests after unbinding any driver from the dGPU, handles abort/cancel/latch
  events like linux-surface's surface-dtx-daemon, publishes `vendor.gb.dtx.{mode,base,latch}`, inhibits the base
  keyboard/touchpad in studio mode. Patch 0003 fixes a mainline surface_dtx latch event bug.
* **dGPU**: keeps every NVIDIA function unbound with runtime PM `auto`, so the root port drops to D3cold; re-applied
  on base hot-plug. **nouveau opt-in** (merged from `../nouveau-kernel/coordination`): with
  `persist.vendor.gb.nouveau=1` (or `-K <driver>`) a function bound to nouveau stays bound and nouveau's own runtime
  PM handles it; a detach request still unbinds it first, and the detach is confirmed only once no NVIDIA function
  has a driver (otherwise it is cancelled). With the property unset, every driver is unbound as before.
* **Platform profile**: `persist.vendor.gb.perf_profile` (performance | balanced-performance | balanced |
  low-power), `gb_power -p <profile>`.

Files: `gb_power.c`, `gb_power.rc`, `build.sh` (NDK, static: it starts at early-init, before the dynamic linker setup
can be relied on; `-Wall -Wextra -Werror`; it copies the DTX uAPI header from the kernel tree `$KSRC` when present),
`uapi/linux/surface_aggregator/dtx.h` (that header, copied from the kernel tree; GPL-2.0+ WITH Linux-syscall-note),
`modules.load.power` (required load order, for reference), `patches/` (GPL-2.0 kernel patches),
`test/test_gb_power.c` (fake-sysfs unit tests, `-r <root>`, including the nouveau opt-in and detach races).

Status: untested on hardware; unit-tested.
