#!/bin/bash
# T3 criterion 6 (DESIGN.md 7.2): compare the installed dual-boot ESP with the export's EFI-SYSTEM.img.
#
#   bash /var/tmp/gbiso/test/check_esp_tree.sh <installed_esp.img> <export_esp.img> <super_guid>
#
# Both arguments are image FILES below /var/tmp/gbiso/test/ (the harness streams them there). They are attached
# read-only (losetup -r) and mounted read-only; nothing is written to them. Prints "CHECK ok|fail <text>" lines and
# finally "ESP-TREE PASS" or "ESP-TREE FAIL"; the /gbinstall/ files are copied to /var/tmp/gbiso/test/esp_gbinstall/.
set -uo pipefail
T=/var/tmp/gbiso/test
NEW=$1; REF=$2; SUPER=$3
LOOPS=(); MNTS=(); FAILS=0
EXPECT="README.txt SHA256SUMS efivars_post.json efivars_pre.json gpt_post_install_backup.bin gpt_post_install_primary.bin gpt_pre_install_backup.bin gpt_pre_install_primary.bin install.log install_state.json layout_post_install.json layout_pre_install.json manifest_installed.json plan.json"

cleanup() {
    for (( i=${#MNTS[@]}-1; i>=0; i-- )); do sudo -n umount "${MNTS[$i]}" 2>/dev/null; done
    for l in "${LOOPS[@]}"; do sudo -n losetup -d "$l" 2>/dev/null; done
}
trap cleanup EXIT
die() { echo "check_esp_tree: $*" >&2; echo "ESP-TREE FAIL"; exit 1; }
check() { if [ "$1" = 0 ]; then echo "CHECK ok $2"; else echo "CHECK fail $2"; FAILS=$((FAILS+1)); fi; }

ro_mount() {    # ro_mount <file> <dir>
    local f d=$2 l base back
    f=$(realpath "$1")
    case "$f" in "$T"/*) ;; *) die "$f not below $T";; esac
    if [ ! -f "$f" ] || [ -L "$1" ]; then die "$f is not a regular file"; fi
    [ -z "$(losetup -j "$f")" ] || die "$f already attached"
    l=$(sudo -n losetup -r -f --show "$f")
    [[ "$l" =~ ^/dev/loop[0-9]+$ ]] || die "unexpected device $l"
    LOOPS+=("$l")
    base=$(basename "$l"); back=$(cat "/sys/block/$base/loop/backing_file")
    [ "$back" = "$f" ] || die "$l backed by $back"
    [ "$(cat "/sys/block/$base/ro")" = 1 ] || die "$l is not read-only"
    mkdir -p "$d"
    sudo -n mount -t vfat -o ro,shortname=mixed,utf8 "$l" "$d" || die "mount $f"
    MNTS+=("$d")
}

# fsck.vfat -n: exit 0, or exactly the reference image's own findings (the export EFI-SYSTEM.img made by
# build_internal.py has its label only in the boot sector; dosfstools 4.2 reports that and exits 1 on the untouched
# image too). The installer applies the same rule (run.fat_check_verdict).
fsck_norm() { grep -v -E '^fsck\.fat [0-9]|: [0-9]+ files, [0-9]+/[0-9]+ clusters$|^$'; }
out=$(fsck.vfat -n "$NEW" 2>&1); rc=$?
ref=$(fsck.vfat -n "$REF" 2>&1); rrc=$?
echo "INFO fsck.vfat -n on the export EFI-SYSTEM.img (reference) exits $rrc"
[ $rrc = 0 ] || printf '%s\n' "$ref" | sed 's/^/  fsck(reference): /'
[ $rc = 0 ] || printf '%s\n' "$out" | sed 's/^/  fsck(installed): /'
if [ $rc = 0 ]; then
    check 0 "fsck.vfat -n on the installed ESP exits 0"
elif [ $rc = $rrc ] && [ "$(printf '%s\n' "$out" | fsck_norm)" = "$(printf '%s\n' "$ref" | fsck_norm)" ]; then
    check 0 "fsck.vfat -n on the installed ESP (exit $rc) reports exactly what it reports on the export image"
else
    check 1 "fsck.vfat -n on the installed ESP exits 0 or matches the export image's report (exit $rc vs $rrc)"
fi
grep -a -q "androidboot.boot_part_uuid=$SUPER" "$NEW"; check $? "raw ESP contains androidboot.boot_part_uuid=$SUPER"
grep -a -q "gb.boot=internal" "$NEW"; check $? "raw ESP contains gb.boot=internal"

ro_mount "$NEW" "$T/mnt/esp_new"
ro_mount "$REF" "$T/mnt/esp_ref"
N=$T/mnt/esp_new; R=$T/mnt/esp_ref

# Wave 2 (DESIGN 13.5): the [NVIDIA test] entry and the dGPU payload on the installed ESP; X = the reference ESP's
# default slot ('default gb-X.conf' since wave 8, SLOT-DEFAULT; 'default gb-X' before). The nvtest entry is never the
# default and carries gb.nouveau=test; no entry has the retired gfxtest
# form or gb.gfxdriver=; exactly one gb/dgpu-*.erofs with its .sha256 line (sha256sum format, names the image).
X=$(sed -n 's/^default gb-\([a-z]\)\(\.conf\)\{0,1\}[[:space:]]*$/\1/p' "$R/loader/loader.conf" | head -n 1)
[ -n "$X" ]; check $? "the reference loader.conf names the default slot (gb-${X:-?})"
E=$N/loader/entries
X=${X:-none}
grep -q -- ' gb\.nouveau=test' "$E/gb-$X-nvtest.conf" 2>/dev/null; check $? "gb-$X-nvtest.conf contains gb.nouveau=test"
grep -q -- "androidboot\.boot_part_uuid=$SUPER" "$E/gb-$X-nvtest.conf" 2>/dev/null
check $? "gb-$X-nvtest.conf contains androidboot.boot_part_uuid=$SUPER"
[ -f "$E/gb-$X.conf" ] && ! grep -q 'gb\.nouveau=' "$E/gb-$X.conf"; check $? "gb-$X.conf exists and has no gb.nouveau="
n=$(find "$E" -maxdepth 1 -iname '*-gfxtest.conf' 2>/dev/null | wc -l)
[ "$n" = 0 ]; check $? "no *-gfxtest.conf entry (found $n)"
n=$(grep -l -- 'gb\.gfxdriver=' "$E"/*.conf 2>/dev/null | wc -l)
[ "$n" = 0 ]; check $? "no entry contains gb.gfxdriver= (found $n)"
er=$(find "$N/gb" -maxdepth 1 -type f -name 'dgpu-*.erofs' -printf '%f\n' 2>/dev/null)
ne=$(printf '%s' "$er" | grep -c .)
ns=$(find "$N/gb" -maxdepth 1 -type f -name 'dgpu-*.sha256' 2>/dev/null | wc -l)
[ "$ne" = 1 ] && [ "$ns" = 1 ] && [ -f "$N/gb/${er%.erofs}.sha256" ]
check $? "exactly one gb/dgpu-*.erofs ($(printf '%s' "$er" | tr '\n' ' ')) and its .sha256 (found $ne image(s), $ns sha file(s))"
if [ "$ne" = 1 ]; then
    want=$(sha256sum "$N/gb/$er" | cut -d' ' -f1)
    have=$(cat "$N/gb/${er%.erofs}.sha256" 2>/dev/null)
    [ "$have" = "$want  $er" ] && [ "$(wc -l < "$N/gb/${er%.erofs}.sha256")" = 1 ]
    check $? "gb/${er%.erofs}.sha256 is one line '<sha256 of the image>  $er'"
fi

missing=0; differ=0
while IFS= read -r -d '' f; do
    if [ ! -f "$N/$f" ]; then echo "  missing: $f"; missing=$((missing+1)); continue; fi
    cmp -s "$R/$f" "$N/$f" || { echo "  differs: $f"; differ=$((differ+1)); }
done < <(cd "$R" && find . -type f -print0)
check $missing "every export ESP file present on the installed ESP"
check $differ "every export ESP file byte-identical"
mdirs=0
while IFS= read -r -d '' d; do [ -d "$N/$d" ] || { echo "  missing dir: $d"; mdirs=$((mdirs+1)); }; done \
    < <(cd "$R" && find . -mindepth 1 -type d -print0)
check $mdirs "every export ESP directory present"

extra=0
while IFS= read -r -d '' f; do
    [ -e "$R/$f" ] && continue
    case "$f" in ./gbinstall|./gbinstall/*) ;; *) echo "  unexpected addition: $f"; extra=$((extra+1));; esac
done < <(cd "$N" && find . -mindepth 1 -print0)
check $extra "additions only under /gbinstall/"

gbdir=$(cd "$N" && find . -mindepth 1 -maxdepth 1 -type d -iname gbinstall | head -1)
[ -n "$gbdir" ]; check $? "/gbinstall/ exists"
if [ -n "$gbdir" ]; then
    have=$(cd "$N/$gbdir" && find . -mindepth 1 -printf '%P\n' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')
    want=$(echo "$EXPECT" | tr ' ' '\n' | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')
    [ "$have" = "$want" ]; check $? "/gbinstall/ holds exactly the 4.5 files (have: $have)"
    (cd "$N/$gbdir" && sha256sum -c --quiet SHA256SUMS >/dev/null 2>&1); check $? "/gbinstall/SHA256SUMS verifies"
    listed=$(cd "$N/$gbdir" && awk '{sub(/^\*/,"",$2); print $2}' SHA256SUMS | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')
    want2=$(echo "$EXPECT" | tr ' ' '\n' | grep -vx SHA256SUMS | LC_ALL=C sort | tr '\n' ' ' | sed 's/ $//')
    [ "$listed" = "$want2" ]; check $? "SHA256SUMS lists every other /gbinstall/ file"
    rm -rf "$T/esp_gbinstall"; mkdir -p "$T/esp_gbinstall"
    cp "$N/$gbdir/"* "$T/esp_gbinstall/"
fi
if [ "$FAILS" = 0 ]; then echo "ESP-TREE PASS"; else echo "ESP-TREE FAIL ($FAILS)"; exit 1; fi
