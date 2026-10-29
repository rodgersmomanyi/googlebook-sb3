#!/bin/sh
# keymint_native_graft.sh -- make inject/keymint/native/ (the NATIVE tree's KeyMint graft with private sonames; wave 5,
# readiness C-2) from inject/keymint/{bin,lib64} (the android16 emulator graft). Run on the builder (patchelf 0.18.0,
# readelf, sha256sum), then check the result with verify_keymint_graft.py on the PC:
#   sh keymint_native_graft.sh <dir with bin/ lib64/ = inject/keymint> <out dir (must not exist)>
#   (copy <out>/bin, <out>/lib64 and <out>/SHA256SUMS to inject\keymint\native\; keep lapis_linkref.json.gz there)
# The 9 libraries get "<stem>_gbkm16.so" names (patchelf --set-soname) and every graft ELF's DT_NEEDED (and verneed
# file names, which patchelf rewrites with them) is renamed to match (--replace-needed), so the native /vendor overlay
# no longer replaces /vendor/lib64/libcrypto.so (android17 libssl.so, wpa_supplicant, hostapd keep lapis's BoringSSL)
# nor lapis's own lib_android_keymaster_keymint_utils.so / libkeymaster_messages.so. --page-size 16384 keeps every new
# PT_LOAD congruent (p_offset == p_vaddr mod p_align 0x4000, the graft's own alignment). Nothing is stripped afterwards
# (strip/objcopy after patchelf is how C-1's mis-laid-out camera ELFs were made). --no-sort: patchelf's default
# section-header sort renumbers the sections but leaves some .dynsym st_shndx pointing at the wrong section; --no-sort
# keeps the section order, so every symbol keeps its section (verify_keymint_graft.py G4 checks it).
# Our own code (Apache-2.0). Every command runs under nice -n 19 ionice -c3.
set -eu
IN=$1; OUT=$2
[ ! -e "$OUT" ] || { echo "keymint_native_graft: $OUT exists (move it away first)" >&2; exit 2; }
LIBS="lib_android_keymaster_keymint_utils libcppbor libcppcose_rkp libcrypto libkeymaster_messages libkeymaster_portable libkeymint libpuresoftkeymasterdevice libsoft_attestation_cert"
BIN=android.hardware.security.keymint-service
N="nice -n 19 ionice -c3"
mkdir -p "$OUT/bin" "$OUT/lib64"
$N patchelf --version
renames() {   # the --replace-needed arguments for the graft names this ELF needs
    f=$1; args=""
    needed=$($N readelf -dW "$f" | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
    for l in $LIBS; do
        if printf '%s\n' "$needed" | grep -qx "$l.so"; then args="$args --replace-needed $l.so ${l}_gbkm16.so"; fi
    done
    printf '%s' "$args"
}
cp "$IN/bin/$BIN" "$OUT/bin/$BIN"
a=$(renames "$OUT/bin/$BIN")
echo "patchelf --no-sort --page-size 16384$a bin/$BIN"
# shellcheck disable=SC2086
$N patchelf --no-sort --page-size 16384 $a "$OUT/bin/$BIN"
for l in $LIBS; do
    cp "$IN/lib64/$l.so" "$OUT/lib64/${l}_gbkm16.so"
    a=$(renames "$OUT/lib64/${l}_gbkm16.so")
    echo "patchelf --no-sort --page-size 16384 --set-soname ${l}_gbkm16.so$a lib64/${l}_gbkm16.so"
    # shellcheck disable=SC2086
    $N patchelf --no-sort --page-size 16384 --set-soname "${l}_gbkm16.so" $a "$OUT/lib64/${l}_gbkm16.so"
done
chmod 0755 "$OUT/bin/$BIN"; chmod 0644 "$OUT/lib64/"*.so
cd "$OUT"
{ printf '%s\n' '# inject/keymint/native: the android16 KeyMint graft with private sonames (readiness C-2), made by'
  printf '%s\n' "# keymint_native_graft.sh from inject/keymint/{bin,lib64} ($(patchelf --version), --no-sort --page-size 16384,"
  printf '%s\n' '# --set-soname / --replace-needed only, never stripped); verify_keymint_graft.py proves nothing else changed.'
  $N sha256sum bin/* lib64/*; } > SHA256SUMS
cat SHA256SUMS
