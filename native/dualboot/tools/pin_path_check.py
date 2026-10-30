"""pin_path_check.py -- (wave 7, safety audit W6-S4) compare the lock-screen PIN path of the installed Googlebook ESP
with the one an update would write, for install_dualboot.ps1 -UpdateEspOnly. Read-only.

  python -I -B pin_path_check.py --verify-image <verify_image.py> --old <img> [--old-offset N --old-length N]
                                 --new <EFI-SYSTEM.img>

--old is the installed ESP: the raw save esp-before-<stamp>.img of -Apply, or (image mode) the target disk image with
the ESP partition's offset and length. --new is the export's EFI-SYSTEM.img. verify_image.py (the repository root's,
or C:\\GB-x86\\verify_image.py) is loaded by path and decides with fat_files + pin_path_problems, the same comparison
build_esp.py (GB_WRITE_LAPIS) and gb_vmbackup.py (restore --esp-only) make before they write: the Gatekeeper binary,
fragment and rc service, the KeyMint service and its vup/lib64 graft, the Trusty/Weaver masks, the fstab and the PIN
SELinux types of the tree the default entry boots.
Prints "PIN path: ..." lines. Exit 0 = the same PIN path, 3 = it differs, 2 = it cannot be compared (fail closed:
the caller refuses unless -PinMigration).
"""
import argparse
import importlib.util
import struct
import sys
import zlib


def main(argv=None):
    ap = argparse.ArgumentParser(description="compare the PIN path of two ESP images (read-only)")
    ap.add_argument("--verify-image", required=True, help="verify_image.py to load")
    ap.add_argument("--old", required=True, help="the installed ESP (an ESP image, or a disk image with --old-offset)")
    ap.add_argument("--old-offset", type=int, default=0)
    ap.add_argument("--old-length", type=int, default=None)
    ap.add_argument("--new", required=True, help="the ESP image the update writes")
    a = ap.parse_args(argv)
    try:
        spec = importlib.util.spec_from_file_location("gb_pin_path_verify_image", a.verify_image)
        vi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vi)
        fat_files, problems = vi.fat_files, vi.pin_path_problems
    except (OSError, AttributeError, ImportError, SyntaxError) as e:
        print(f"PIN path: cannot load the check from {a.verify_image} ({e.__class__.__name__}: {e})")
        return 2
    try:
        with open(a.old, "rb") as f:
            size = f.seek(0, 2)
            n = size - a.old_offset if a.old_length is None else a.old_length
            if a.old_offset < 0 or n <= 0 or a.old_offset + n > size:
                print(f"PIN path: the installed ESP region {a.old_offset:,}+{n:,} B is not inside {a.old} "
                      f"({size:,} B; expected that many bytes)")
                return 2
            f.seek(a.old_offset)
            old = f.read(n)
        with open(a.new, "rb") as f:
            new = f.read()
    except (OSError, MemoryError) as e:
        print(f"PIN path: cannot read an image ({e.__class__.__name__}: {e})")
        return 2
    if len(old) != n:
        print(f"PIN path: read {len(old):,} B of the installed ESP, expected {n:,} B")
        return 2
    files = []
    for label, img in (("the installed", old), ("the new", new)):
        try:
            files.append(fat_files(img))
        except (ValueError, KeyError, IndexError, struct.error, zlib.error) as e:
            print(f"PIN path: {label} ESP is not a readable FAT32 ({e.__class__.__name__}: {e}): its PIN path is unknown")
            return 2
    probs = problems(files[0], files[1])
    for p in probs:
        print(f"PIN path: {p}")
    if not probs:
        print("PIN path: the same as the installed ESP")
        return 0
    return 2 if any("PIN path is unknown" in p for p in probs) else 3


if __name__ == "__main__":
    sys.exit(main())
