"""PE/COFF DllCharacteristics helper (DESIGN.md 2.1, 2.5). Same offset and transformation as
build_internal.py clear_nx_compat(): DllCharacteristics at e_lfanew + 24 + 70 (PE32 and PE32+), NX_COMPAT = 0x0100.

Usage:
  python3 -I -B pe_tool.py get <file>                 prints "0x%04x"
  python3 -I -B pe_tool.py clear-nx <in> <out>        writes a copy with 0x0100 cleared; prints "<before> <after>"
The EFI loader ignores the PE checksum; an Authenticode signature becomes invalid (Secure Boot is off on the target).
"""
import struct
import sys

NX_COMPAT = 0x0100


def dll_offset(b):
    if b[:2] != b"MZ":
        raise SystemExit("pe_tool: no MZ header")
    off = struct.unpack_from("<I", b, 0x3C)[0]
    if b[off:off + 4] != b"PE\0\0":
        raise SystemExit("pe_tool: no PE signature")
    magic = struct.unpack_from("<H", b, off + 24)[0]
    if magic not in (0x10B, 0x20B):
        raise SystemExit(f"pe_tool: unknown optional header magic 0x{magic:04x}")
    return off + 24 + 70


def main(argv):
    if len(argv) == 2 and argv[0] == "get":
        b = open(argv[1], "rb").read()
        print(f"0x{struct.unpack_from('<H', b, dll_offset(b))[0]:04x}")
        return 0
    if len(argv) == 3 and argv[0] == "clear-nx":
        b = bytearray(open(argv[1], "rb").read())
        dc = dll_offset(b)
        v = struct.unpack_from("<H", b, dc)[0]
        struct.pack_into("<H", b, dc, v & ~NX_COMPAT)
        with open(argv[2], "wb") as f:
            f.write(b)
        print(f"0x{v:04x} 0x{v & ~NX_COMPAT:04x}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
