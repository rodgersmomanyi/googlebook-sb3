# dgpu: specification and integration records of the NVIDIA (TU117) work

This folder holds the documents the NVIDIA components were built against. They are working documents: they cite the
development layout (`C:\GB-build\native\<component>` = `native/<component>` here, `C:\GB-x86` = the repository
root, `builder:` = the Linux builder) and planning files that are not published (`NVIDIA_PLAN.md`,
`GRAPHICS_SWITCHING_REQ.md`; INTERFACES.md quotes the parts that matter). For what the NVIDIA support does and how to
use it, read [docs/NVIDIA.md](../../docs/NVIDIA.md) first.

| File | What |
|---|---|
| `INTERFACES.md` | The normative interface spec, version 1.2 r4: kernel command line, properties, paths, the dGPU payload, the nouveau policy, the gb_power state model and control socket, the compute service, notifications, packages, the per-app driver shim, init services, boot entries, the ESP lock (5.4), graphics switching (sections 22-27) and every change with the review finding it answers (21, 28, 29, the Wave 2b errata 30 and the Wave 3 errata 31: the ESP lock r4, the read-only ESP mount of the bring-up config at rdinit, the "System update held" notification). |
| `INTERFACES-v1.1.md`, `INTERFACES-v1.2r1.md`, `INTERFACES-v1.2r2.md`, `INTERFACES-v1.2r3.md` | The earlier revisions, byte for byte, as INTERFACES.md cites them. |
| `integration/README.md` | The Wave 2 checklist (W2-00 .. W2-16) for the shared image-pipeline files. |
| `integration/*.md` | One record per component: artifacts with sha256, test results, the exact Wave 2 edits and the hardware-only checks (`nouveau-policy`, `gb-power`, `k14-kernel`, `nvk-gfxdriver`, `gpu-compute`, `ux-app`, `gfx-switching`). |
| `1b/TASKS_1B.md` | Task split and acceptance tests of the graphics-switching wave ("1b"). |
| `1b/REVIEW.md` | The review of the first graphics-switching draft. |
| `1b/WAVE2_PLAN_v2.md` | The executed integration plan for the image pipeline (USB image, dual-boot export, installer ISO). |
| `w3/WAVE3_PLAN.md`, `w3/WAVE3_REVIEW.md` | Wave 3 (2026-10-26): the executed plan for the findings taken from gbOS86 (8250 `nr_uarts`, DRAS card id, the `#UD` emulator rework and kernel build #15, fs-verity, the boot-log summary, apgetty, the ESP bring-up config, the gb_slotsync kernel check, `-UpdateEspOnly`, firmware pinning, the HWUI and white-veil checks, docs) and its adversarial review (`[W3R-nn]` corrections). |
| `w4/GATEKEEPER.md`, `w4/REVIEW.md` | Wave 4 (2026-10-26): the design of the software Gatekeeper (lock-screen PIN) with its sources, persistence and throttling analysis, integration as built (7.9), lock-out recovery (8), test plan and the device rehearsal results (9.6), and the adversarial review of that design and of the VM upgrade plan. User summary: [docs/GATEKEEPER.md](../../docs/GATEKEEPER.md); sources: [`native/gatekeeper`](../gatekeeper/). |
| `w4/VM_UPGRADE.md`, `w4/tools/` | Wave 4: backing up a VMware VM, then upgrading its ESP (k15, zram, fs-verity, the Gatekeeper), with the rehearsal on a throwaway VM, the rollback paths, and the backup/preview tools (`gb_vmbackup.py`, `vm_esp_preview.py`, `esp_tree_diff.py`, `sb_probe.py`). |

Components: [`nouveau-kernel`](../nouveau-kernel/) (policy scripts), [`power`](../power/) (gb_power),
[`nvk-android`](../nvk-android/) (NVK build and `nvk_probe`), [`gfx-driver-pkg`](../gfx-driver-pkg/) (per-app driver
package), [`gpu-compute`](../gpu-compute/) (compute service), [`gpu-ui`](../gpu-ui/) (the GbGpu app) and the k14 kernel
([`kernel/patches/k14`](../../kernel/patches/k14/); native images now run build #15 = k14 plus two non-nouveau
patches, [`kernel/K15.md`](../../kernel/K15.md)). Nothing in this folder has run on the real GPU.
