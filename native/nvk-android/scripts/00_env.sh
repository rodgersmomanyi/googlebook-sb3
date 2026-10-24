# Common environment for the nvk-android build (sourced by the other scripts).
# Builder: Ubuntu 24.04 x86_64, work dir /build/native/nvk-android.
W=${W:-/build/native/nvk-android}
MESA_SRC=$W/src/mesa                 # git archive of mesa-26.3.0-rc2 (from /build/mesa-android/mesa)
MESA_TAG=mesa-26.3.0-rc2
NDK=${NDK:-/build/ndk}               # NDK r27c
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
SYSROOT=$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot
API=${API:-35}                       # NDK r27c max API; lapis is SDK 37 (forward compatible)
JOBS=${JOBS:-3}                      # builder has 3 vCPUs: never more than -j3
export RUSTUP_HOME=$W/rustup CARGO_HOME=$W/cargo
# meson 1.12.1 from the earlier Mesa-for-Android venv (used read-only), rustc/cargo/bindgen from our own rustup.
export PATH=$W/cargo/bin:/build/mesa-android/venv/bin:$W/host-clc/bin:$PATH
