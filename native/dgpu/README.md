# dgpu: specification and integration records of the NVIDIA (TU117) work

This folder holds the documents the NVIDIA components were built against. They are working documents: they cite the
development layout (`C:\GB-build\native\<component>` = `native/<component>` here, `C:\GB-x86` = the repository
root, `builder:` = the Linux builder) and planning files that are not published (`NVIDIA_PLAN.md`,
`GRAPHICS_SWITCHING_REQ.md`; INTERFACES.md quotes the parts that matter). For what the NVIDIA support does and how to
use it, read [docs/NVIDIA.md](../../docs/NVIDIA.md) first.

| File | What |
|---|---|
| `INTERFACES.md` | The normative interface spec, version 1.2 r2: kernel command line, properties, paths, the dGPU payload, the nouveau policy, the gb_power state model and control socket, the compute service, notifications, packages, the per-app driver shim, init services, boot entries, graphics switching (sections 22-27) and every change with the review finding it answers (21, 28, 29). |
| `INTERFACES-v1.1.md`, `INTERFACES-v1.2r1.md` | The earlier revisions, byte for byte, as INTERFACES.md cites them. |
| `integration/README.md` | The Wave 2 checklist (W2-00 .. W2-16) for the shared image-pipeline files. |
| `integration/*.md` | One record per component: artifacts with sha256, test results, the exact Wave 2 edits and the hardware-only checks (`nouveau-policy`, `gb-power`, `k14-kernel`, `nvk-gfxdriver`, `gpu-compute`, `ux-app`, `gfx-switching`). |
| `1b/TASKS_1B.md` | Task split and acceptance tests of the graphics-switching wave ("1b"). |
| `1b/REVIEW.md` | The review of the first graphics-switching draft. |
| `1b/WAVE2_PLAN_v2.md` | The executed integration plan for the image pipeline (USB image, dual-boot export, installer ISO). |

Components: [`nouveau-kernel`](../nouveau-kernel/) (policy scripts), [`power`](../power/) (gb_power),
[`nvk-android`](../nvk-android/) (NVK build and `nvk_probe`), [`gfx-driver-pkg`](../gfx-driver-pkg/) (per-app driver
package), [`gpu-compute`](../gpu-compute/) (compute service), [`gpu-ui`](../gpu-ui/) (the GbGpu app) and the k14 kernel
([`kernel/patches/k14`](../../kernel/patches/k14/)). Nothing in this folder has run on the real GPU.
