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
  on base hot-plug.
* **Platform profile**: `persist.vendor.gb.perf_profile` (performance | balanced-performance | balanced |
  low-power), `gb_power -p <profile>`.

Files: `gb_power.c`, `gb_power.rc`, `build.sh` (NDK, `-Wall -Wextra -Werror`), `modules.load.power` (required load
order), `patches/` (GPL-2.0 kernel patches), `test/test_gb_power.c` (fake-sysfs unit tests, `-r <root>`).

Status: untested on hardware; unit-tested.
