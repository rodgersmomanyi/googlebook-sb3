#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Googlebook OS build (GB-build) authors.
#
# run_equiv.sh -- builder-side equivalence + zero-VEX-AVX-VNNI proof for the libz
# patch.  It (1) patches the two carved libz.so with vnni_patch.py (and emits the
# fingerprint-gated bind rc), (2) builds the bionic vnni_probe with the NDK,
# (3) runs the probe under a chroot of the carved lapis tree (read-only) against
# the unpatched and patched copies, and (4) checks the verdict.
#
# The builder is itself an i7-1065G7 (Ice Lake, no VEX AVX-VNNI, stock kernel: no
# #UD emulator), so a VEX VNNI instruction that executes raises SIGILL and the
# probe counts it.  Expectation:
#   unpatched: every adler32-using op faults inside adler32_simd_ (len>=64) --
#              this also proves the inputs reach the SIMD path; crc32 ok.
#   patched  : every op completes, zero faults, all results match the scalar
#              reference -> zero VEX AVX-VNNI executed on those paths.
#
# Needs sudo (for the chroot mounts), like the existing nvk chroot tests.
# Usage: run_equiv.sh   (everything under /var/tmp/vnniperf, read-only on the
#                        carved tree; writes only under vnni/ and a private
#                        chroot.* dir it removes again)
#   EV=<dir>  where to write equiv_run.log (default: <this dir>/../evidence)
set -uo pipefail
ROOT=/var/tmp/vnniperf
SELF=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
PATCHER=$SELF/../patcher
CLANG=$(ls /build/ndk/toolchains/llvm/prebuilt/*/bin/x86_64-linux-android*-clang 2>/dev/null | sort | tail -1)
[ -x "$CLANG" ] || { echo "no NDK x86_64 clang found"; exit 1; }
OUT=$ROOT/vnni
EV=${EV:-$SELF/../evidence}
rm -rf "$OUT/in" "$OUT/out" "$OUT/odm"
mkdir -p "$OUT/in/system/lib64" "$OUT/in/vendor/lib64" "$OUT/out" "$OUT/odm" "$OUT/data" "$EV"

echo "===== 1. stage originals (+ the partitions' build.prop) into a clean in-root"
cp "$ROOT/fs/system_b/system/lib64/libz.so" "$OUT/in/system/lib64/libz.so"
cp "$ROOT/fs/vendor_b/lib64/libz.so"         "$OUT/in/vendor/lib64/libz.so"
cp "$ROOT/fs/system_b/system/build.prop"     "$OUT/in/system/build.prop"
cp "$ROOT/fs/vendor_b/build.prop"            "$OUT/in/vendor/build.prop"

echo "===== 2. patch (sha256-pinned, result hash-pinned) + gated bind rc"
python3 -I "$PATCHER/vnni_patch.py" --manifest "$PATCHER/manifest.json" \
    --in-root "$OUT/in" --out-root "$OUT/out" --out-manifest "$OUT/out-manifest.json" \
    --out-rc "$OUT/vnni.rc" --odm-root "$OUT/odm"
echo "--- generated rc:"; sed 's/^/  | /' "$OUT/vnni.rc"
n_ungated=$(grep -c '^on ' "$OUT/vnni.rc" | tr -d ' ')
n_gated=$(grep -c '^on early-init && property:ro\.\(system\|vendor\)\.build\.fingerprint=' "$OUT/vnni.rc" | tr -d ' ')
echo "rc actions: $n_ungated, fingerprint-gated: $n_gated"

echo "===== 3. build the bionic probe with the NDK"
"$CLANG" -O2 -Wall -Wextra -pthread -o "$OUT/data/vnni_probe" "$SELF/vnni_probe.c" -ldl || exit 1
file "$OUT/data/vnni_probe" | sed 's/^/  /'

echo "===== 4. assemble chroot data dir"
cp "$OUT/in/system/lib64/libz.so"  "$OUT/data/libz_system_unpatched.so"
cp "$OUT/out/system/lib64/libz.so" "$OUT/data/libz_system_patched.so"
cp "$OUT/in/vendor/lib64/libz.so"  "$OUT/data/libz_vendor_unpatched.so"
cp "$OUT/out/vendor/lib64/libz.so" "$OUT/data/libz_vendor_patched.so"

C=$(mktemp -d "$ROOT/chroot.XXXXXX")
mounted=()
cleanup() {
  for ((i=${#mounted[@]}-1; i>=0; i--)); do
    sudo umount "${mounted[$i]}" 2>/dev/null || sudo umount -l "${mounted[$i]}" 2>/dev/null
  done
  # the mount points were created by root: remove them as root (empty dirs only)
  if ! findmnt -rn -o TARGET | grep -qF "$C"; then
    sudo rmdir "$C"/apex/com.android.runtime "$C"/apex "$C"/system "$C"/vendor \
               "$C"/dev "$C"/proc "$C"/data 2>/dev/null
    rmdir "$C" 2>/dev/null || sudo rmdir "$C" 2>/dev/null || true
  fi
}
trap cleanup EXIT
mnt() { sudo mount "$@" && mounted+=("${@: -1}"); }

sudo mkdir -p "$C"/system "$C"/vendor "$C"/apex/com.android.runtime "$C"/dev "$C"/proc "$C"/data
mnt --bind "$ROOT/fs/system_b/system" "$C/system"; sudo mount -o remount,bind,ro "$C/system"
mnt --bind "$ROOT/fs/vendor_b" "$C/vendor"; sudo mount -o remount,bind,ro "$C/vendor"
mnt --bind "$ROOT/apex/system_b__com.android.runtime" "$C/apex/com.android.runtime"
sudo mount -o remount,bind,ro "$C/apex/com.android.runtime"
mnt --bind /dev "$C/dev"
mnt -t proc proc "$C/proc"
mnt --bind "$OUT/data" "$C/data"

ENV="LD_LIBRARY_PATH=/apex/com.android.runtime/lib64:/system/lib64:/vendor/lib64"
run() {
  echo "##### $*"
  sudo chroot "$C" /system/bin/env $ENV /system/bin/sh -c "$*"
  echo "exit=$?"
}

echo "===== 5. run probe (seed 0x1234: 3000 random + 19 fixed lengths, plus the extended ops)"
{
  echo "# $(date -u +%FT%TZ) $(uname -n) $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-)"
  echo "# vnni_probe.c sha256 $(sha256sum < "$SELF/vnni_probe.c" | cut -c1-16)  manifest sha256 $(sha256sum < "$PATCHER/manifest.json" | cut -c1-16)"
  echo "# generated rc: $n_ungated action(s), $n_gated fingerprint-gated"
  run "/data/vnni_probe /data/libz_system_unpatched.so 0x1234 3000"
  run "/data/vnni_probe /data/libz_system_patched.so   0x1234 3000"
  run "/data/vnni_probe /data/libz_vendor_unpatched.so 0x1234 3000"
  run "/data/vnni_probe /data/libz_vendor_patched.so   0x1234 3000"
} > "$EV/equiv_run.log" 2>&1
cat "$EV/equiv_run.log"

echo "===== 6. verdict"
python3 -I - "$EV/equiv_run.log" "$n_ungated" "$n_gated" <<'EOF' | tee -a "$EV/equiv_run.log"
import re, sys
log, n_on, n_gated = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
runs, cur = {}, None
for line in open(log):
    m = re.match(r"##### /data/vnni_probe /data/(\S+)", line)
    if m:
        cur = m.group(1); runs[cur] = {"ops": {}, "result": None, "faults": set()}; continue
    m = re.match(r"SUMMARY (\S+)\s+ok=(\d+) mismatch=(\d+) sigill=(\d+)", line)
    if m and cur:
        runs[cur]["ops"][m.group(1)] = tuple(int(x) for x in m.group(2, 3, 4))
    m = re.match(r"FAULT \S+\s+off=(0x[0-9a-f]+)", line)
    if m and cur:
        runs[cur]["faults"].add(int(m.group(1), 16))
    m = re.match(r"RESULT (\S+)", line)
    if m and cur:
        runs[cur]["result"] = m.group(1)
ok = n_on == n_gated and n_on >= 1
print("VERDICT rc: every generated bind is fingerprint-gated: %s" % ("PASS" if ok else "FAIL"))
adler_ops = ["threads", "adler32", "inflate_roundtrip", "adler_chain", "adler_unaligned",
             "adler32_z_large", "adler_combine", "stream"]
for part in ("system", "vendor"):
    p, u = runs.get("libz_%s_patched.so" % part), runs.get("libz_%s_unpatched.so" % part)
    if not p or not u:
        print("VERDICT %s: missing run -> FAIL" % part); ok = False; continue
    p_ok = p["result"] == "ok" and all(v[1] == 0 and v[2] == 0 and v[0] > 0 for v in p["ops"].values())
    u_reach = all(u["ops"].get(o, (0, 0, 0))[2] > 0 for o in adler_ops)
    u_crc = u["ops"].get("crc32", (0, 1, 1))[1:] == (0, 0)
    u_in_simd = bool(u["faults"]) and all(0x10fc0 <= f < 0x11352 for f in u["faults"])
    print("VERDICT %s patched: all %d ops correct with 0 SIGILL: %s" % (part, len(p["ops"]), "PASS" if p_ok else "FAIL"))
    print("VERDICT %s unpatched: every adler32 op reaches the SIMD path (SIGILL>0): %s; "
          "all faults inside adler32_simd_ [0x10fc0,0x11352) %s: %s; crc32 control clean: %s"
          % (part, "PASS" if u_reach else "FAIL", sorted(hex(f) for f in u["faults"]),
             "PASS" if u_in_simd else "FAIL", "PASS" if u_crc else "FAIL"))
    ok &= p_ok and u_reach and u_in_simd and u_crc
print("VERDICT overall: %s" % ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
EOF
rc=$?
echo "===== done (verdict rc=$rc); evidence in $EV/equiv_run.log and $OUT/out-manifest.json"
exit $rc
