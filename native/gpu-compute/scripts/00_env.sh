# Common environment for the gpu-compute build and tests (sourced; builder Ubuntu 24.04).
# shellcheck disable=SC2034  # variables are used by the scripts that source this file
# Component scratch (INTERFACES 1.1): /build/native/dgpu/gpu-compute
W=${W:-/build/native/dgpu/gpu-compute}
SRC=${SRC:-$W/src}                    # copy of C:\GB-build\native\gpu-compute
LLAMA=${LLAMA:-$W/llama.cpp}          # pinned llama.cpp checkout (10_fetch_llama.sh)
INC=${INC:-$W/inc}                    # Vulkan-Headers 1.3.275 (libvulkan-dev) + SPIRV-Headers (15_headers.sh)
NDK=${NDK:-/build/ndk}                # NDK r27c
NDKBIN=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
API=${API:-35}                        # NDK r27c max API; lapis is SDK 37 (forward compatible, same as NVK)
JOBS=${JOBS:-2}                       # builder has 3 vCPUs shared with other components
LLAMA_TAG=b11552                      # see LLAMA_PIN
LLAMA_COMMIT=23b0202a189c44a54625aadcb37a946dd1d6278d
LVP_ICD=/usr/lib/x86_64-linux-gnu/libvulkan_lvp.so
NICE="nice -n 19 ionice -c3"
export SOURCE_DATE_EPOCH=1791660338   # commit time of the pinned llama.cpp tag (reproducible builds)

# INTERFACES 1.4: start heavy jobs only when the 1-min load is < 2 and no gbiso job runs. Waits for both; a gbiso
# process that is still there after 10 min (e.g. a long image transfer) is logged and the job then starts anyway at
# nice 19 / ionice idle (it never touches /var/tmp/gbiso). The load condition is never skipped before 60 min.
gb_wait_quiet() {
  _n=0
  while :; do
    _load=$(cut -d. -f1 /proc/loadavg)
    _iso=$(pgrep -f gbiso 2>/dev/null | head -1 || true)
    [ "$_load" -lt 2 ] && { [ -z "$_iso" ] || [ $_n -ge 20 ]; } && break
    [ $_n -eq 0 ] && echo "waiting: load $(cut -d' ' -f1 /proc/loadavg), gbiso pid ${_iso:-none}"
    _n=$((_n + 1))
    [ $_n -ge 120 ] && { echo "builder still busy after 60 min; continuing at nice 19"; break; }
    sleep 30
  done
  if [ -n "${_iso:-}" ]; then
    echo "note: gbiso process $_iso ($(ps -o args= -p "$_iso" | cut -c1-80 || true)) still running; starting at nice 19"
  fi
  echo "start: load $(cut -d' ' -f1 /proc/loadavg) at $(date -u +%FT%TZ)"
}

gb_check_space() {
  for d in / /build; do
    _f=$(df -BG --output=avail "$d" | tail -1 | tr -dc 0-9)
    [ "$_f" -ge 6 ] || { echo "only ${_f} GB free on $d (rule: keep > 5 GB)"; return 1; }
  done
}
