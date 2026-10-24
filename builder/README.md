# Builder VM

All Linux-side work runs on an Ubuntu 24.04 x86_64 VM (VMware, 3+ vCPU, 7+ GB RAM plus a 12 GB swap file, a second
~120 GB disk mounted on `/build`, passwordless sudo, SSH key login). Layout used by the scripts:

| Path | Content |
|---|---|
| `/build/gki` | repo checkout of the pinned GKI tree ([../kernel/manifest_16302624.xml](../kernel/manifest_16302624.xml)); `/build/.bazel` Bazel output root |
| `/build/dist` | kernel build output (`bzImage`, `*.ko`, `vmlinux.symvers`) read by `stage_kernel.py` |
| `/build/ndk` | Android NDK r27c |
| `/build/mesa-android`, `/build/swiftshader` | Mesa and SwiftShader builds |
| `/build/native/<component>` | native component work dirs |

Setup:

1. Ubuntu cloud image + a NoCloud seed made from [cloud-init/user-data.example](cloud-init/user-data.example) (put
   your SSH public key in) and [cloud-init/meta-data](cloud-init/meta-data); the seed formats and mounts the second
   disk on `/build`.
2. `bash kernel/scripts/gki_setup_sync.sh` (swap file, `repo`, pinned sync).
3. NDK r27c into `/build/ndk` (`graphics/swiftshader/ss_build.sh` shows the download).

`cvd_fetch.sh` fetched a public AOSP Cuttlefish build (for reference images and the SwiftShader comparison); it is not
needed for the pipeline.

Windows side: `stage_kernel.py` reaches the builder with `ssh -i $GB_BUILDER_KEY $GB_BUILDER`. Keep the private key
outside the repository.
