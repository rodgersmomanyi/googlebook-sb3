#!/bin/bash
# TEST media on the builder (DESIGN.md 7.1 "Builder media", 7.2 T2b/T2g/T2h/T2i, T3n(c), T4n, T5, T6, T6n).
#
#   bash /var/tmp/gbiso/test/builder_media.sh <medium>      -> /var/tmp/gbiso/test/<medium>.img
#   media: t2b t2g t2h t2i t2j t3nc t4n t4nb t5 t5e t6a t6b t6d t6g t6n t9s t9l   (t5e = T5 with partition type EF00)
#   bash /var/tmp/gbiso/test/builder_media.sh fatextract <name>   (read-only mtools copy out of <name>.img)
#   bash /var/tmp/gbiso/test/builder_media.sh clean         (end of a run: unmount/detach ours, delete media)
#
# Safety: works only on image FILES below /var/tmp/gbiso/test/. Every block device used is a loop device created
# here over such a file (losetup -f --show -P) and re-checked with `losetup -j <file>` + sysfs backing_file right
# before every mkfs / mount; anything that is not /dev/loopN[pM] is refused. The ISO is attached read-only (-r).
# sgdisk/sfdisk only ever get the image file path. Mounts live below /var/tmp/gbiso/test/mnt/. Exit status 0 and a
# final line "MEDIUM OK <medium> <bytes>" on success; everything is cleaned up on exit.
set -euo pipefail
umask 022
T=/var/tmp/gbiso/test
ISO=/var/tmp/gbiso/last/googlebook-sb3-installer.iso
GiB=1073741824
LOOPS=()
MNTS=()
LOOP=""

die() { echo "builder_media: $*" >&2; exit 1; }

cleanup() {
    set +e
    sync
    for (( i=${#MNTS[@]}-1; i>=0; i-- )); do sudo -n umount "${MNTS[$i]}" 2>/dev/null; done
    for l in "${LOOPS[@]}"; do sudo -n losetup -d "$l" 2>/dev/null; done
    rm -rf "$T/oldtree" "$T/old.iso"
}
trap cleanup EXIT

below_t() { case "$(realpath -m "$1")" in "$T"/*) return 0;; *) return 1;; esac; }

new_image() {   # new_image <file> <bytes>: a sparse regular file (never a device)
    local f=$1 n=$2
    below_t "$f" || die "refusing $f: not below $T"
    [ -z "$(losetup -j "$f" 2>/dev/null || true)" ] || die "$f is attached to a loop device already"
    rm -f "$f"
    truncate -s "$n" "$f"
    [ -f "$f" ] && [ ! -L "$f" ] || die "$f is not a regular file"
}

attach() {      # attach <file> [ro]: sets LOOP
    local f=$1 mode=${2:-rw} real
    real=$(realpath "$f")
    [ -f "$real" ] && [ ! -L "$f" ] || die "attach: $f is not a regular file"
    if [ "$real" != "$ISO" ]; then below_t "$real" || die "attach: $f is not below $T"; fi
    [ -z "$(losetup -j "$real")" ] || die "attach: $real is already attached: $(losetup -j "$real")"
    if [ "$mode" = ro ]; then LOOP=$(sudo -n losetup -r -f --show "$real")
    else LOOP=$(sudo -n losetup -f --show -P "$real"); fi
    [[ "$LOOP" =~ ^/dev/loop[0-9]+$ ]] || die "attach: unexpected device '$LOOP'"
    LOOPS+=("$LOOP")
    check_loop "$real" "$LOOP"
}

check_loop() {  # check_loop <file> <dev>: dev is /dev/loopN or /dev/loopNpM and loopN is backed by <file>
    local f dev=$2 base back
    f=$(realpath "$1")
    [[ "$dev" =~ ^/dev/loop[0-9]+(p[0-9]+)?$ ]] || die "refusing device $dev (loop devices only)"
    base=$(basename "$dev"); [[ "$base" =~ ^(loop[0-9]+)(p[0-9]+)?$ ]] || die "bad device name $dev"; base=${BASH_REMATCH[1]}
    back=$(cat "/sys/block/$base/loop/backing_file" 2>/dev/null || true)
    [ "$back" = "$f" ] || die "$dev is backed by '$back', not $f"
    losetup -j "$f" | grep -q "^/dev/$base:" || die "losetup -j $f does not list /dev/$base"
    [ -b "$dev" ] || die "$dev is not a block device"
}

part() {        # part <file> <n>: echo the partition node of LOOP after waiting for it
    local f=$1 n=$2 p="${LOOP}p$2" i
    for i in $(seq 1 50); do [ -b "$p" ] && break; sleep 0.1; done
    check_loop "$f" "$p"
    echo "$p"
}

mnt() {         # mnt <file> <dev> <dir> <mount args...>
    local f=$1 dev=$2 d=$3; shift 3
    check_loop "$f" "$dev"
    case "$d" in "$T"/mnt/*) ;; *) die "mount point $d not below $T/mnt";; esac
    mkdir -p "$d"
    sudo -n mount "$@" "$dev" "$d"
    MNTS+=("$d")
}

umnt() {
    local d=$1
    sync
    sudo -n umount "$d"
    local keep=() m
    for m in "${MNTS[@]}"; do [ "$m" = "$d" ] || keep+=("$m"); done
    MNTS=("${keep[@]+"${keep[@]}"}")
}

detach() {      # detach LOOP and wait until the kernel has really released it (partitioned loops detach lazily)
    local f i
    f=$(cat "/sys/block/$(basename "$LOOP")/loop/backing_file")
    sudo -n losetup -d "$LOOP"
    for i in $(seq 1 100); do [ -z "$(losetup -j "$f")" ] && break; sleep 0.1; done
    [ -z "$(losetup -j "$f")" ] || die "$LOOP still attached to $f after 10 s"
    local keep=() l
    for l in "${LOOPS[@]}"; do [ "$l" = "$LOOP" ] || keep+=("$l"); done
    LOOPS=("${keep[@]+"${keep[@]}"}")
    LOOP=""
}

mk_vfat()  { check_loop "$1" "$2"; sudo -n mkfs.vfat -F "$3" -n "$4" "$2" >/dev/null; }
mk_exfat() { check_loop "$1" "$2"; sudo -n mkfs.exfat -L "$3" "$2" >/dev/null; }
mk_ntfs()  { check_loop "$1" "$2"; sudo -n mkntfs -Q -q -L "$3" "$2"; }

gpt_one() {     # gpt_one <file> <type> [size]: new GPT, partition 1 from 1 MiB (size or to the end)
    local f=$1 t=$2 sz=${3:-0}
    below_t "$f" || die "gpt_one: $f"
    sgdisk -o "$f" >/dev/null
    if [ "$sz" = 0 ]; then sgdisk -n 1:2048:0 -t 1:"$t" -c 1:"Basic data partition" "$f" >/dev/null
    else sgdisk -n 1:2048:+"$sz" -t 1:"$t" -c 1:"Basic data partition" "$f" >/dev/null; fi
    sgdisk -v "$f" | grep -q "No problems found" || die "sgdisk -v reports problems on $f"
    # sgdisk -v also says "No problems found" for a file without any GPT: check the partition really exists
    sgdisk -i 1 "$f" | grep -q "^First sector: 2048 " || die "partition 1 was not created on $f"
}

nparts() { sgdisk -p "$1" | awk '/^ +[0-9]+ +[0-9]+ +[0-9]+ /{n++} END{print n+0}'; }

iso_check() {
    [ -f "$ISO" ] || die "no $ISO"
    if [ -f "$T/iso.sha256" ]; then
        local want got
        want=$(cut -d' ' -f1 "$T/iso.sha256")
        got=$(sha256sum "$ISO" | cut -d' ' -f1)
        [ "$want" = "$got" ] || die "ISO sha256 $got != build record $want"
    else die "$T/iso.sha256 missing (the harness writes the build record's iso_sha256 there)"; fi
}

space_check() {
    local avail
    avail=$(df --output=avail -B1 "$T" | tail -1)
    [ "$avail" -ge $((15 * GiB)) ] || die "builder $T has only $avail bytes free (< 15 GiB)"
}

copy_iso_tree() {   # copy_iso_tree <dstdir>: all files of the ISO tree (cp -r from a read-only loop mount)
    local dst=$1 il
    attach "$ISO" ro; il=$LOOP
    mnt "$ISO" "$il" "$T/mnt/iso" -t iso9660 -o ro
    sudo -n cp -r "$T/mnt/iso/." "$dst/"
    sync
    [ -f "$dst/gbsb3/manifest.json" ] || die "copy incomplete"
    umnt "$T/mnt/iso"
    LOOP=$il; detach
}

F="$T/${1:-}.img"
case "${1:-}" in
t2b)    # 32 GiB, GPT, one Basic Data partition formatted NTFS (R_WINDOWS_DISK)
    space_check; new_image "$F" $((32 * GiB)); gpt_one "$F" 0700 1G
    attach "$F"; P=$(part "$F" 1); mk_ntfs "$F" "$P" DATA; detach ;;
t2g)    # GPT, 1 GiB Basic Data FAT32 with /sources/boot.wim + /EFI/Microsoft/Boot/BCD (winmedia)
    space_check; new_image "$F" $((32 * GiB)); gpt_one "$F" 0700 1G
    attach "$F"; P=$(part "$F" 1); mk_vfat "$F" "$P" 32 WINSTUB
    mnt "$F" "$P" "$T/mnt/t2g" -t vfat -o rw
    sudo -n mkdir -p "$T/mnt/t2g/sources" "$T/mnt/t2g/EFI/Microsoft/Boot"
    sudo -n sh -c "head -c 1024 /dev/zero > '$T/mnt/t2g/sources/boot.wim'; printf 'stub\n' > '$T/mnt/t2g/EFI/Microsoft/Boot/BCD'"
    umnt "$T/mnt/t2g"; detach ;;
t2h)    # GPT, 1 GiB exFAT with /GB-backup/x/gpt_pre_shrink_primary.bin (gbbackup)
    space_check; new_image "$F" $((32 * GiB)); gpt_one "$F" 0700 1G
    attach "$F"; P=$(part "$F" 1); mk_exfat "$F" "$P" BACKUP
    mnt "$F" "$P" "$T/mnt/t2h" -t exfat -o rw
    sudo -n mkdir -p "$T/mnt/t2h/GB-backup/x"
    sudo -n sh -c "head -c 17408 /dev/zero > '$T/mnt/t2h/GB-backup/x/gpt_pre_shrink_primary.bin'"
    umnt "$T/mnt/t2h"; detach ;;
t2i)    # Ventoy layout, MBR: p1 exFAT "Ventoy" 1 GiB from 1 MiB, p2 FAT16 "VTOYEFI" 32 MiB at the end (ventoy)
    space_check; new_image "$F" $((32 * GiB))
    printf 'label: dos\nstart=2048, size=2097152, type=7\nstart=%d, size=65536, type=6\n' \
        $(( 32 * GiB / 512 - 65536 )) | sfdisk -q --no-reread --no-tell-kernel "$F" >/dev/null
    attach "$F"; P1=$(part "$F" 1); P2=$(part "$F" 2)
    mk_exfat "$F" "$P1" Ventoy; mk_vfat "$F" "$P2" 16 VTOYEFI; detach ;;
t3nc|t4n|t4nb)  # host-shaped disk (gpt_validate mkimage), C: real NTFS with /hiberfil.sys starting HIBR; t4nb: C:
                # starts with a BitLocker boot sector (-FVE-FS-) instead (Fast Startup cannot be checked)
    space_check
    below_t "$F" || die "$F"
    [ -z "$(losetup -j "$F" 2>/dev/null || true)" ] || die "$F attached"
    rm -f "$F"
    python3 -I "$T/gpt_validate.py" mkimage "$F" --c-mib 1024 --d-mib 6144 >/dev/null
    if [ "$1" = t4n ] || [ "$1" = t4nb ]; then
        # shrink D: (partition 4, slot 3) to 2 GiB keeping start/type/GUID/name, then 18 x 1 MiB partitions in
        # export placement order with the export manifest's names, types, GUIDs and attribute bits (t4n_parts.txt)
        [ -f "$T/t4n_parts.txt" ] || die "t4n_parts.txt missing"
        read -r dnum dfirst dlast dtype dguid dname < <(grep '^D ' "$T/t4n_parts.txt" | cut -d' ' -f2-)
        sgdisk -d "$dnum" -n "$dnum:$dfirst:$dlast" -t "$dnum:$dtype" -u "$dnum:$dguid" -c "$dnum:$dname" "$F" >/dev/null
        args=()
        while read -r tag num first last ptype pguid pname bits; do
            [ "$tag" = P ] || continue
            args+=(-n "$num:$first:$last" -t "$num:$ptype" -u "$num:$pguid" -c "$num:$pname")
            if [ "$bits" != - ]; then for b in ${bits//,/ }; do args+=(-A "$num:set:$b"); done; fi
        done < "$T/t4n_parts.txt"
        sgdisk "${args[@]}" "$F" >/dev/null
        sgdisk -v "$F" | grep -q "No problems found" || die "sgdisk -v problems on $F"
        [ "$(nparts "$F")" = 23 ] || die "t4n: expected 23 partitions, have $(nparts "$F")"
        sgdisk -i "$dnum" "$F" | grep -q "^Last sector: $dlast " || die "t4n: D: was not shrunk"
    fi
    if [ "$1" = t4nb ]; then
        # C: = partition 3: an NTFS-like boot sector with the BitLocker OEM ID at offset 3 (written into the FILE)
        cstart=$(sgdisk -i 3 "$F" | awk '/^First sector:/{print $3}')
        [[ "$cstart" =~ ^[0-9]+$ ]] || die "t4nb: no partition 3"
        python3 -I -c 'import struct,sys
s=bytearray(512); s[0:3]=bytes.fromhex("eb5890"); s[3:11]=b"-FVE-FS-"; struct.pack_into("<HB",s,11,512,8)
s[21]=0xF8; s[510:512]=bytes.fromhex("55aa")
f=open(sys.argv[1],"r+b"); f.seek(int(sys.argv[2])*512); f.write(bytes(s)); f.close()' "$F" "$cstart"
        sync; echo "MEDIUM OK ${1} $(stat -c %s "$F")"; exit 0
    fi
    attach "$F"; P=$(part "$F" 3); mk_ntfs "$F" "$P" Windows
    mnt "$F" "$P" "$T/mnt/c" -t ntfs3 -o rw
    sudo -n sh -c "printf 'HIBR' > '$T/mnt/c/hiberfil.sys'; head -c 1048572 /dev/zero >> '$T/mnt/c/hiberfil.sys'"
    umnt "$T/mnt/c"; detach
    command -v ntfsls >/dev/null && { attach "$F"; P=$(part "$F" 3); sudo -n ntfsls -a -s -p / "$P" | grep -qx 'hiberfil.sys' || die "hiberfil.sys not listed"; detach; } ;;
t5|t5e) # Rufus ISO-mode equivalent: 9 GiB GPT, p1 from 1 MiB FAT32 GBSB3INST (0700, t5e: EF00), ISO files copied
    iso_check; space_check; new_image "$F" $((9 * GiB))
    if [ "$1" = t5 ]; then gpt_one "$F" 0700; else gpt_one "$F" EF00; fi
    attach "$F"; fl=$LOOP; P=$(part "$F" 1); mk_vfat "$F" "$P" 32 GBSB3INST
    mnt "$F" "$P" "$T/mnt/fat" -t vfat -o rw,shortname=mixed,utf8
    copy_iso_tree "$T/mnt/fat"
    umnt "$T/mnt/fat"; LOOP=$fl; detach ;;
t6a|t6n)    # Ventoy equivalent: 9 GiB GPT, one exFAT (t6a) / NTFS (t6n) partition "Ventoy", /ISO/<the ISO>
    iso_check; space_check; new_image "$F" $((9 * GiB)); gpt_one "$F" 0700
    attach "$F"; P=$(part "$F" 1)
    if [ "$1" = t6a ]; then mk_exfat "$F" "$P" Ventoy; mnt "$F" "$P" "$T/mnt/v" -t exfat -o rw
    else mk_ntfs "$F" "$P" Ventoy; mnt "$F" "$P" "$T/mnt/v" -t ntfs3 -o rw; fi
    sudo -n mkdir -p "$T/mnt/v/ISO"
    sudo -n cp "$ISO" "$T/mnt/v/ISO/googlebook-sb3-installer.iso"
    sync
    cmp -s "$ISO" "$T/mnt/v/ISO/googlebook-sb3-installer.iso" || die "ISO copy differs"
    if [ "$1" = t6a ]; then
        # where the ISO file lies on the disk (FIBMAP of its first block), for the T6m dm-linear map that stands in
        # for Ventoy's /dev/mapper/ventoy; contiguity is proven below by hashing that disk range
        off=$(sudo -n python3 -I -c 'import fcntl,os,struct,sys
fd=os.open(sys.argv[1],os.O_RDONLY)
bsz=struct.unpack("i",fcntl.ioctl(fd,2,struct.pack("i",0)))[0]
blk=struct.unpack("i",fcntl.ioctl(fd,1,struct.pack("i",0)))[0]
print(blk*bsz)' "$T/mnt/v/ISO/googlebook-sb3-installer.iso")
    fi
    umnt "$T/mnt/v"; detach
    if [ "$1" = t6a ]; then
        isz=$(stat -c %s "$ISO")
        lba=$(( 2048 + off / 512 ))
        got=$(python3 -I -c 'import hashlib,sys
f=open(sys.argv[1],"rb"); f.seek(int(sys.argv[2])*512); n=int(sys.argv[3]); h=hashlib.sha256()
while n:
    b=f.read(min(n,1<<24))
    if not b: break
    h.update(b); n-=len(b)
print(h.hexdigest())' "$F" "$lba" "$isz")
        [ "$got" = "$(cut -d' ' -f1 "$T/iso.sha256")" ] || die "the ISO file is not contiguous at LBA $lba"
        echo "EXTENT lba=$lba sectors=$(( isz / 512 ))"
    fi ;;
t6d)    # 64 MiB GPT, one FAT16 partition "TOOLS" holding dmsetup and its libraries (T6m: the Ventoy-like dm map)
    space_check; new_image "$F" $((64 * 1048576)); gpt_one "$F" 0700
    attach "$F"; P=$(part "$F" 1); mk_vfat "$F" "$P" 16 TOOLS
    mnt "$F" "$P" "$T/mnt/t6d" -t vfat -o rw
    sudo -n mkdir -p "$T/mnt/t6d/dm"
    for f in /usr/sbin/dmsetup $(ldd /usr/sbin/dmsetup | awk '/=> \//{print $3}'); do
        sudo -n cp -L "$f" "$T/mnt/t6d/dm/"
    done
    [ -f "$T/mnt/t6d/dm/libdevmapper.so.1.02.1" ] || die "libdevmapper missing"
    umnt "$T/mnt/t6d"; detach ;;
t6g)    # Ventoy GRUB2-mode equivalent: 9 GiB GPT, p1 exFAT "Ventoy" with /ISO/<the ISO>, p2 FAT ESP "VTOYEFI" whose
        # \EFI\BOOT\BOOTX64.EFI is a stock GRUB (grub-mkstandalone, Ubuntu archive) that loop-mounts the ISO file and
        # runs the ISO's own grub.cfg with root=(loop) - no CD, nothing tells the installer where the ISO is
    iso_check; space_check; new_image "$F" $((9 * GiB))
    sgdisk -o "$F" >/dev/null
    sgdisk -n 1:2048:-34M -t 1:0700 -c 1:"Ventoy" -n 2:0:0 -t 2:EF00 -c 2:"VTOYEFI" "$F" >/dev/null
    sgdisk -v "$F" | grep -q "No problems found" || die "sgdisk -v reports problems on $F"
    cat > "$T/loop.cfg" <<'GRUBCFG'
insmod part_gpt
insmod exfat
insmod iso9660
insmod loopback
if search --no-floppy --file --set=vroot /ISO/googlebook-sb3-installer.iso; then
  loopback loop ($vroot)/ISO/googlebook-sb3-installer.iso
  set root=(loop)
  configfile (loop)/boot/grub/grub.cfg
fi
echo "T6g: /ISO/googlebook-sb3-installer.iso not found"
sleep 30
GRUBCFG
    grub-mkstandalone -O x86_64-efi -o "$T/t6g_bootx64.efi" --locales="" --fonts="" --themes="" \
        "boot/grub/grub.cfg=$T/loop.cfg"
    attach "$F"; P1=$(part "$F" 1); P2=$(part "$F" 2)
    mk_exfat "$F" "$P1" Ventoy; mk_vfat "$F" "$P2" 16 VTOYEFI
    mnt "$F" "$P2" "$T/mnt/g" -t vfat -o rw
    sudo -n mkdir -p "$T/mnt/g/EFI/BOOT"; sudo -n cp "$T/t6g_bootx64.efi" "$T/mnt/g/EFI/BOOT/BOOTX64.EFI"
    umnt "$T/mnt/g"
    mnt "$F" "$P1" "$T/mnt/v" -t exfat -o rw
    sudo -n mkdir -p "$T/mnt/v/ISO"
    sudo -n cp "$ISO" "$T/mnt/v/ISO/googlebook-sb3-installer.iso"
    sync
    cmp -s "$ISO" "$T/mnt/v/ISO/googlebook-sb3-installer.iso" || die "ISO copy differs"
    umnt "$T/mnt/v"; detach; rm -f "$T/loop.cfg" "$T/t6g_bootx64.efi" ;;
t2j)    # 32 GiB, NO partition table: exFAT on the whole device ("superfloppy") with /GB-backup/x/... (gbbackup)
    space_check; new_image "$F" $((32 * GiB))
    attach "$F"; mk_exfat "$F" "$LOOP" BACKUP
    mnt "$F" "$LOOP" "$T/mnt/t2j" -t exfat -o rw
    sudo -n mkdir -p "$T/mnt/t2j/GB-backup/x"
    sudo -n sh -c "head -c 17408 /dev/zero > '$T/mnt/t2j/GB-backup/x/gpt_pre_shrink_primary.bin'"
    umnt "$T/mnt/t2j"; detach ;;
t9s|t9l)    # GPT, one FAT32 Basic Data partition: the test 'sticks' of T9: t9s (1 GiB, "GPTCOPY") takes the off-disk
            # GPT copy, t9l (2 GiB, "LOGS") the logs of tools w (a stick holding a GPT copy is never a log target)
    space_check
    if [ "$1" = t9s ]; then new_image "$F" $((1 * GiB)); lab=GPTCOPY; else new_image "$F" $((2 * GiB)); lab=LOGS; fi
    gpt_one "$F" 0700
    attach "$F"; P=$(part "$F" 1); mk_vfat "$F" "$P" 32 "$lab"; detach ;;
fatextract) # fatextract <name>: copy /gbsb3-logs and /gbsb3-gptbackup out of the FAT image $T/<name>.img (mtools,
            # read-only) into $T/<name>_out/
    n=${2:-}; [[ "$n" =~ ^[a-z0-9_]+$ ]] || die "fatextract: bad name"
    rm -rf "$T/${n}_out"; mkdir -p "$T/${n}_out"
    for d in gbsb3-logs gbsb3-gptbackup; do
        if MTOOLS_SKIP_CHECK=1 mdir -i "$T/$n.img" "::/$d" >/dev/null 2>&1; then
            MTOOLS_SKIP_CHECK=1 mcopy -s -n -i "$T/$n.img" "::/$d" "$T/${n}_out/"
        fi
    done
    echo "FATEXTRACT OK $n"; exit 0 ;;
t6b)    # 1 GiB GPT plain exFAT "DATA" with /old/googlebook-sb3-installer-old.iso = a fake older build
    space_check; new_image "$F" $((1 * GiB)); gpt_one "$F" 0700
    rm -rf "$T/oldtree" "$T/old.iso"; mkdir -p "$T/oldtree/gbsb3"
    printf 'GBSB3-PAYLOAD-MARKER 1\nbuild_id=00000000-000000-00000000\nvolume_label=GBSB3INST\nmanifest=gbsb3/manifest.json\nmanifest_sha256=0000000000000000000000000000000000000000000000000000000000000000\n' \
        > "$T/oldtree/gbsb3/GBSB3-00000000-000000-00000000.marker"
    head -c $((64 * 1048576)) /dev/zero > "$T/oldtree/zero64m.bin"
    xorriso -as mkisofs -quiet -V GBSB3INST -o "$T/old.iso" "$T/oldtree" >/dev/null 2>&1
    [ "$(stat -c %s "$T/old.iso")" -ge $((64 * 1048576)) ] || die "old ISO too small"
    attach "$F"; P=$(part "$F" 1); mk_exfat "$F" "$P" DATA
    mnt "$F" "$P" "$T/mnt/d" -t exfat -o rw
    sudo -n mkdir -p "$T/mnt/d/old"; sudo -n cp "$T/old.iso" "$T/mnt/d/old/googlebook-sb3-installer-old.iso"
    umnt "$T/mnt/d"; detach ;;
clean)  # end of a run: unmount our mount points, detach loop devices over our files, delete media and scratch
    while read -r _ mp _; do
        case "$mp" in "$T"/mnt/*) sudo -n umount "$mp" || die "cannot unmount $mp";; esac
    done < /proc/mounts
    for s in /sys/block/loop*/loop/backing_file; do
        [ -f "$s" ] || continue
        case "$(cat "$s")" in "$T"/*) d=/dev/$(basename "$(dirname "$(dirname "$s")")"); sudo -n losetup -d "$d";; esac
    done
    rm -rf "$T"/*.img "$T"/*_out "$T/t0" "$T/esp_gbinstall" "$T/oldtree" "$T/old.iso" "$T/mnt" "$T/__pycache__" \
        "$T/loop.cfg" "$T/t6g_bootx64.efi"
    echo "CLEAN OK"; exit 0 ;;
*)  die "usage: builder_media.sh t2b|t2g|t2h|t2i|t2j|t3nc|t4n|t4nb|t5|t5e|t6a|t6b|t6d|t6g|t6n|t9s|t9l|clean|fatextract <n>" ;;
esac
sync
echo "MEDIUM OK ${1} $(stat -c %s "$F")"
