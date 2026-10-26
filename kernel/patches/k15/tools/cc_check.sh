#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Fast compile check of the k15 sources before the Kleaf build: re-runs the exact clang command lines that the
# last Kleaf build saved (.traps.o.cmd, .intel_display.o.cmd, .intel_display_params.o.cmd in the --config=local
# cache) with a clang VFS overlay that maps the patched files over /build/gki/common. Output objects and dep
# files go to the work dir only; the kernel tree and the Kleaf cache are only read.
# Usage (builder): bash cc_check.sh <tools dir> <work dir>
# shellcheck disable=SC2015
set -u
T=${1:?tools dir}; W=${2:?work dir}
SRC=/build/gki/common
OUTC=$(readlink -f /build/gki/out/cache/last_build)/common
CLANG=/build/gki/prebuilts/clang/host/linux-x86/clang-r584948c/bin
rm -rf "$W"; mkdir -p "$W/f"
git -C "$SRC" show k14-nouveau:arch/x86/kernel/traps.c > "$W/f/traps.c"
python3 -I "$T/apply_vnni4.py" "$W/f/traps.c" || exit 1
D=drivers/gpu/drm/i915/display
mkdir -p "$W/i915/$D"
for f in intel_display.c intel_display_params.c intel_display_params.h; do git -C "$SRC" show "k14-nouveau:$D/$f" > "$W/i915/$D/$f"; done
python3 -I "$T/apply_i915_gamma.py" "$W/i915" || exit 1
cat > "$W/overlay.yaml" <<EOF
{ 'version': 0, 'roots': [
  { 'name': '$SRC/arch/x86/kernel', 'type': 'directory', 'contents': [
    { 'name': 'traps.c', 'type': 'file', 'external-contents': '$W/f/traps.c' },
    { 'name': 'gb_vnni_core.h', 'type': 'file', 'external-contents': '$W/f/gb_vnni_core.h' } ] },
  { 'name': '$SRC/$D', 'type': 'directory', 'contents': [
    { 'name': 'intel_display.c', 'type': 'file', 'external-contents': '$W/i915/$D/intel_display.c' },
    { 'name': 'intel_display_params.c', 'type': 'file', 'external-contents': '$W/i915/$D/intel_display_params.c' },
    { 'name': 'intel_display_params.h', 'type': 'file', 'external-contents': '$W/i915/$D/intel_display_params.h' } ] } ] }
EOF
rc=0
for o in arch/x86/kernel/traps.o $D/intel_display.o $D/intel_display_params.o; do
  cmdf="$OUTC/$(dirname "$o")/.$(basename "$o").cmd"
  cmd=$(sed -n '1s/^savedcmd_[^ ]* := //p' "$cmdf")
  [ -n "$cmd" ] || { echo "no saved command in $cmdf"; rc=1; continue; }
  b=$(basename "$o" .o)
  cmd=${cmd//"-Wp,-MMD,$(dirname "$o")/.$b.o.d"/"-Wp,-MMD,$W/$b.d"}
  cmd=${cmd//" -o $o "/" -o $W/$b.o "}
  case "$cmd" in *"-o $W/$b.o"*) ;; *) echo "could not redirect -o for $o"; rc=1; continue ;; esac
  # the saved command may end with ; <objtool or other post-steps>: keep only the compile
  cmd=${cmd%%;*}
  if (cd "$OUTC" && PATH=$CLANG:$PATH eval "$cmd -ivfsoverlay $W/overlay.yaml") > "$W/$b.log" 2>&1; then
    echo "compile OK: $o ($(stat -c %s "$W/$b.o") bytes, warnings: $(grep -c 'warning:' "$W/$b.log"))"
  else
    echo "compile FAILED: $o"; cat "$W/$b.log"; rc=1
  fi
done
# the overlay really was used: the objects carry the new strings
grep -q -a 'gb_vnni: emulating VEX AVX-VNNI' "$W/traps.o" && echo "traps.o: carries the gb_vnni strings" || { echo "traps.o: overlay not used?"; rc=1; }
grep -q -a 'gb_ignore_user_gamma' "$W/intel_display_params.o" && echo "intel_display_params.o: carries gb_ignore_user_gamma" || { echo "params.o: overlay not used?"; rc=1; }
grep -q "gb_vnni_core.h" "$W/traps.d" && echo "traps.d: includes gb_vnni_core.h" || { echo "traps.d: no gb_vnni_core.h"; rc=1; }
exit $rc
