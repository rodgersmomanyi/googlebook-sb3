# Common environment for the nvk-android build (sourced by the other scripts).
# Builder: Ubuntu 24.04 x86_64, work dir /build/native/nvk-android.
W=${W:-/build/native/nvk-android}
MESA_SRC=${MESA_SRC:-$W/src/mesa}    # git archive of mesa-26.3.0-rc2 (from /build/mesa-android/mesa)
MESA_TAG=${MESA_TAG:-mesa-26.3.0-rc2}
NDK=${NDK:-/build/ndk}               # NDK r27c
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
SYSROOT=$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot
API=${API:-35}                       # NDK r27c max API = compiler target; lapis is SDK 37 (forward compatible)
JOBS=${JOBS:-3}                      # builder has 3 vCPUs: never more than -j3
# Build profile (= scripts/apply_patches.py --set):
#   base = patches 0001+0002, -Dplatform-sdk-version=35, no shader cache: reproduces the 2026-10-24 ICD f7e9154f (NV-01)
#   8a   = 0001 0002 0004 0005 0006, platform-sdk-version 37, shader cache compiled in (static NDK zlib): shipped ICD
#          (bin/vulkan.nouveau.so) and the per-app driver copy libgb_nvk.so
#   8b   = 8a + 0003 (AHB): compile and API-level checks only, not shipped
PATCHSET=${PATCHSET:-8a}
case $PATCHSET in
  base) _psdk=35; _sc=disabled ;;
  8a|8b) _psdk=37; _sc=enabled ;;
  *) echo "unknown PATCHSET=$PATCHSET (base|8a|8b)" >&2; exit 2 ;;
esac
# Mesa's ANDROID_API_LEVEL; 37 = lapis' SDK. The NDK sysroot stays API 35 (compiler target above). Mesa 26.3.0-rc2 has
# no code guarded by ANDROID_API_LEVEL > 35 in the paths NVK builds (grep 2026-10-25), so 35 vs 37 changes no code
# today; it matters for later Mesa versions (see INTEGRATION_NOTES 9.3).
PLATFORM_SDK=${PLATFORM_SDK:-$_psdk}
SHADER_CACHE=${SHADER_CACHE:-$_sc}   # compiled in; on Android Mesa keeps it off unless MESA_SHADER_CACHE_DISABLE=false
B=${B:-$W/b-nvk-$PATCHSET}
STAGE=${STAGE:-$W/stage-$PATCHSET}
export RUSTUP_HOME=$W/rustup CARGO_HOME=$W/cargo
# meson 1.12.1 from the earlier Mesa-for-Android venv (used read-only), rustc/cargo/bindgen from our own rustup.
HOST_CLC=${HOST_CLC:-$W/host-clc}   # host mesa_clc/vtn_bindgen2 built from the SAME Mesa version (10_build_host_clc.sh)
export PATH=$W/cargo/bin:/build/mesa-android/venv/bin:$HOST_CLC/bin:$PATH
