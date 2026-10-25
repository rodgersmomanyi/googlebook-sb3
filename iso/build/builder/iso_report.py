"""ISO tree and hybrid ISO checks for the Googlebook SB3 installer (DESIGN.md 1.1-1.4, 2.4). Read-only except
for writing <tree>/gbsb3/SHA256SUMS in the 'tree' step.

  python3 -I -B iso_report.py tree --tree DIR --build-id ID
      Exact file set of 1.1 (plus the chunks named by the manifest), every file < 1 GiB, marker/manifest/GPT/export
      hashes consistent, chunk sizes and zst_sha256 as in the manifest; writes gbsb3/SHA256SUMS (sha256sum format,
      every file except itself and boot/boot.cat). Last line "TREE-JSON {...}".
  python3 -I -B iso_report.py iso --iso FILE --tree DIR --efiboot IMG --bootx64 EFI --build-id ID
      The 2.4 properties: one El Torito platform-0xEF no-emulation entry with Ldsiz = efiboot bytes / 512, valid GPT
      (sgdisk -v), partition 1 = ISO9660 GBSB3INST from LBA 64, partition 2 = EFI System holding efiboot.img (and
      /EFI/BOOT/BOOTX64.EFI), PVD volume id, size multiple of 2048 and <= 8 GiB, the ISO's file list == the tree +
      boot/boot.cat. Last line "ISO-JSON {...}".
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

GiB = 1 << 30
LABEL = "GBSB3INST"
ISO_CEILING = 8 * GiB
TYPE_ESP_SGDISK = "EF00"


def die(msg):
    print(f"iso_report: ERROR: {msg}", flush=True)
    sys.exit(1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def tree_files(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if os.path.islink(p) or not os.path.isfile(p):
                die(f"{rel} is not a regular file")
            out[rel] = os.path.getsize(p)
    return out


def cmd_tree(a):
    root = a.tree
    man_path = os.path.join(root, "gbsb3", "manifest.json")
    with open(man_path, "rb") as f:
        man_bytes = f.read()
    man = json.loads(man_bytes)
    if man.get("build_id") != a.build_id or man.get("format") != "gbsb3-iso-payload/1":
        die("manifest format/build_id mismatch")
    marker_rel = f"gbsb3/GBSB3-{a.build_id}.marker"
    expected = {"EFI/BOOT/BOOTX64.EFI", "boot/grub/grub.cfg", "boot/vmlinuz", "boot/initrd.img", marker_rel,
                "gbsb3/manifest.json", "gbsb3/wd/gpt-primary.bin", "gbsb3/wd/gpt-backup.bin",
                "gbsb3/dualboot/manifest.json", "README.txt"}
    chunks = {}
    for oid, o in man["objects"].items():
        off = 0
        for c in o["chunks"]:
            if c["raw_offset"] != off:
                die(f"{oid}: chunks not contiguous")
            off += c["raw_size"]
            chunks[c["path"]] = c
        if off != o["raw_size"]:
            die(f"{oid}: chunks do not cover raw_size")
    expected |= set(chunks)
    files = tree_files(root)
    files.pop("gbsb3/SHA256SUMS", None)
    if "boot/boot.cat" in files:
        die("boot/boot.cat must not be in the tree (xorriso creates it)")
    missing = sorted(expected - set(files))
    extra = sorted(set(files) - expected)
    if missing or extra:
        die(f"tree mismatch: missing {missing} extra {extra}")
    big = [k for k, v in files.items() if v >= GiB]
    if big:
        die(f"files >= 1 GiB: {big}")
    with ThreadPoolExecutor(3) as ex:
        hashes = dict(zip(sorted(files), ex.map(lambda r: sha256_file(os.path.join(root, r)), sorted(files))))
    for rel, c in chunks.items():
        if files[rel] != c["zst_size"] or hashes[rel] != c["zst_sha256"]:
            die(f"{rel}: size/sha256 differ from the manifest")
    marker = open(os.path.join(root, marker_rel), "rb").read()
    want = (f"GBSB3-PAYLOAD-MARKER 1\nbuild_id={a.build_id}\nvolume_label={LABEL}\nmanifest=gbsb3/manifest.json\n"
            f"manifest_sha256={hashlib.sha256(man_bytes).hexdigest()}\n").encode("ascii")
    if marker != want:
        die("marker content is not the 1.2 form for this manifest")
    wd = man["whole_disk"]
    for key in ("gpt_primary", "gpt_backup"):
        e = wd[key]
        if files[e["path"]] != e["bytes"] or hashes[e["path"]] != e["sha256"]:
            die(f"{e['path']}: differs from the manifest")
    db = hashes["gbsb3/dualboot/manifest.json"]
    if db != man["dual_boot"]["export_manifest_sha256"] or db != man["source"]["dualboot_export"]["manifest_sha256"]:
        die("dualboot/manifest.json sha256 differs from the manifest")
    lines = "".join(f"{hashes[r]}  {r}\n" for r in sorted(files))
    tmp = os.path.join(root, "gbsb3", "SHA256SUMS.tmp")
    with open(tmp, "w", newline="\n") as f:
        f.write(lines)
    os.replace(tmp, os.path.join(root, "gbsb3", "SHA256SUMS"))
    total = sum(files.values())
    print(f"tree: {len(files)} files, {total} bytes, {len(chunks)} chunks; SHA256SUMS written")
    print("TREE-JSON " + json.dumps({"files": len(files) + 1, "bytes": total, "chunks": len(chunks),
                                     "boot_sha256": {k: hashes[k] for k in ("EFI/BOOT/BOOTX64.EFI", "boot/vmlinuz",
                                                                            "boot/initrd.img",
                                                                            "boot/grub/grub.cfg")}}))


def parse_eltorito(text):
    imgs = []
    for line in text.splitlines():
        m = re.match(r"^El Torito boot img :\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)", line)
        if m:
            imgs.append({"n": int(m[1]), "platform": m[2], "bootable": m[3], "emul": m[4], "ld_seg": m[5],
                         "hdpt": m[6], "ldsiz": int(m[7]), "lba": int(m[8])})
    return imgs


def parse_sgdisk_p(text):
    parts = []
    for line in text.splitlines():
        m = re.match(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+([0-9A-F]{4})\s*(.*)$", line)
        if m:
            parts.append({"n": int(m[1]), "start": int(m[2]), "end": int(m[3]), "code": m[4], "name": m[5].strip()})
    return parts


def pvd_label(f, off):
    f.seek(off)
    b = f.read(2048)
    if b[1:6] != b"CD001" or b[0] != 1:
        return None
    return b[40:72].decode("ascii", "replace").rstrip(" ")


def cmd_iso(a):
    iso = a.iso
    size = os.path.getsize(iso)
    if size % 2048:
        die(f"ISO size {size} is not a multiple of 2048")
    if size > ISO_CEILING:
        die(f"ISO size {size} exceeds the 8.0 GiB ceiling")
    efi_bytes = os.path.getsize(a.efiboot)
    if efi_bytes >= 32 << 20 or efi_bytes % 512:
        die("efiboot.img size invalid")
    rc, out, err = run(["xorriso", "-indev", iso, "-report_el_torito", "plain", "-report_system_area", "plain"])
    if rc != 0:
        die(f"xorriso report failed: {err.strip()[-400:]}")
    report = out + err
    imgs = parse_eltorito(report)
    if len(imgs) != 1:
        die(f"expected exactly one El Torito boot image, found {len(imgs)}")
    et = imgs[0]
    if et["platform"] != "UEFI" or et["emul"] != "none" or et["ldsiz"] != efi_bytes // 512:
        die(f"El Torito entry wrong: {et} (want UEFI, none, Ldsiz {efi_bytes // 512})")
    rc, outp, errp = run(["sgdisk", "-p", iso])
    if rc != 0:
        die(f"sgdisk -p failed: {(outp + errp).strip()[-400:]}")
    parts = parse_sgdisk_p(outp)
    rc, outv, errv = run(["sgdisk", "-v", iso])
    if rc != 0 or "No problems found" not in outv:
        die(f"sgdisk -v reports problems: {(outv + errv).strip()[-600:]}")
    p1 = next((p for p in parts if p["n"] == 1), None)
    p2 = next((p for p in parts if p["n"] == 2), None)
    if not p1 or p1["start"] != 64:
        die(f"GPT partition 1 does not start at LBA 64: {parts}")
    if not p2 or p2["code"] != TYPE_ESP_SGDISK:
        die(f"GPT partition 2 is not EFI System: {parts}")
    with open(iso, "rb") as f:
        lab0 = pvd_label(f, 32768)
        lab1 = pvd_label(f, p1["start"] * 512 + 32768)
        f.seek(p2["start"] * 512)
        p2_bytes = f.read((p2["end"] - p2["start"] + 1) * 512)
    if lab0 != LABEL or lab1 != LABEL:
        die(f"PVD volume id wrong: whole={lab0!r} partition1={lab1!r}")
    with open(a.efiboot, "rb") as f:
        efi_img = f.read()
    if p2_bytes[:len(efi_img)] != efi_img or p2_bytes[len(efi_img):].strip(b"\0"):
        die("GPT partition 2 does not hold exactly efiboot.img")
    rc, b1, e1 = run(["blkid", "-p", "-o", "export", "-O", str(p1["start"] * 512), iso])
    if "TYPE=iso9660" not in b1 or f"LABEL={LABEL}" not in b1:
        die(f"partition 1 does not probe as iso9660 {LABEL}: {(b1 + e1).strip()}")
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    r = subprocess.run(["mcopy", "-n", "-i", f"{iso}@@{p2['start'] * 512}", "::/EFI/BOOT/BOOTX64.EFI", "-"],
                       capture_output=True, env=env)
    if r.returncode != 0 or r.stdout != open(a.bootx64, "rb").read():
        die("partition 2 /EFI/BOOT/BOOTX64.EFI differs from BOOTX64.EFI")
    rc, lst, le = run(["xorriso", "-indev", iso, "-find", "/", "-type", "f", "-exec", "lsdl", "--"])
    if rc != 0:
        die(f"xorriso -find failed: {le.strip()[-400:]}")
    in_iso = {}
    for line in lst.splitlines():
        m = re.match(r"^\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+.*?'(/.*)'$", line)
        if m:
            in_iso[m[2].lstrip("/")] = int(m[1])
    # the El Torito catalog node is not listed by -find -type f; its path comes from the report
    if not re.search(r"^El Torito cat path :\s+/boot/boot\.cat\s*$", report, re.M):
        die("El Torito catalog is not /boot/boot.cat")
    in_iso.pop("boot/boot.cat", None)
    want = tree_files(a.tree)
    if set(in_iso) != set(want):
        die(f"ISO file list differs from the tree: only in ISO {sorted(set(in_iso) - set(want))}, "
            f"only in tree {sorted(set(want) - set(in_iso))}")
    bad = [k for k in want if in_iso[k] != want[k]]
    if bad:
        die(f"file sizes differ in the ISO: {bad}")
    iso_sha = sha256_file(iso)
    res = {"iso_size": size, "iso_sha256": iso_sha, "eltorito": et, "gpt": parts, "volume_id": lab0,
           "efiboot_img_bytes": efi_bytes, "files": len(in_iso), "sgdisk_v": outv.strip().splitlines()[0]}
    print(f"ISO {size} bytes sha256 {iso_sha}: El Torito {et['platform']} Ldsiz {et['ldsiz']}; GPT {parts}")
    print("ISO-JSON " + json.dumps(res))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tree")
    t.add_argument("--tree", required=True)
    t.add_argument("--build-id", required=True)
    i = sub.add_parser("iso")
    i.add_argument("--iso", required=True)
    i.add_argument("--tree", required=True)
    i.add_argument("--efiboot", required=True)
    i.add_argument("--bootx64", required=True)
    i.add_argument("--build-id", required=True)
    a = ap.parse_args()
    (cmd_tree if a.cmd == "tree" else cmd_iso)(a)


if __name__ == "__main__":
    main()
