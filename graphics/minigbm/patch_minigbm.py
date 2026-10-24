"""Patch lapis's own minigbm libraries (libminigbm_gralloc.so, libgbm.so) for the VMware VM (vmwgfx).

This repository does not ship Google's vendor binaries. Take the two libraries from YOUR lapis vendor partition
(/vendor/lib64/libminigbm_gralloc.so and /vendor/lib64/libgbm.so) and run this script; it writes the patched copies
that build_esp.py overlays onto /vendor/lib64 in the VM tree (inject/vendor_gfx/). Not used natively: on the Surface
Book 3 the stock minigbm drives i915 unchanged.

Patches (each one is located by a byte pattern that must occur exactly once; nothing else is touched):
  1. backend driver name "komeda" -> "vmwgfx" (both libraries). minigbm has no vmwgfx backend, so its allocator
     aborts "Failed to initialize driver" on the vmwgfx DRM device. komeda's backend is a plain dumb-buffer backend,
     which works on vmwgfx, so renaming its driver-name string makes minigbm use it for vmwgfx.
  2. the 64-alignment of the dumb-buffer dimensions is removed (both libraries): `add r12d,0x3f; and r12d,-64;
     add r13d,0x3f; and r13d,-64` -> two NOPs (10 + 6 bytes) ("noalign" fix, used with the GB_RGBA_DUMB vmwgfx and
     Mesa svga).
  3. CPU-lock gate (libminigbm_gralloc.so only): a conditional jump in the buffer lock path becomes unconditional
     (jne -> jmp, 0x75 -> 0xEB).

Reference: the libraries of lapis CL3B.260622.271 (stock and result md5 below are checked when they match).

Usage: python -I patch_minigbm.py STOCK_DIR [OUT_DIR]     (OUT_DIR default: <workspace>/inject/vendor_gfx)
"""
import hashlib, os, sys

# (pattern, offset of the change inside the pattern, new bytes)
KOMEDA = (b"\x00komeda\x00", 1, b"vmwgfx")
ALIGN = (bytes.fromhex("4183c43f4183e4c04183c53f4183e5c0"), 0, bytes.fromhex("662e0f1f840000000000" "660f1f440000"))
LOCKGATE = (bytes.fromhex("80bc241802000000750980b880000000"), 8, b"\xeb")
LIBS = {
    "libminigbm_gralloc.so": {"stock_md5": "86508927ebc3e5c6e85feac4c9a29585",
                              "patched_md5": "a6b29a58b066f345c07e409a4439d82d",
                              "patches": [KOMEDA, ALIGN, LOCKGATE]},
    "libgbm.so": {"stock_md5": "af2b19c99d6115bc366f2fdd3d972daf",
                  "patched_md5": "805fdf748a4e094b4efa78a59db1a399",
                  "patches": [KOMEDA, ALIGN]},
}


def md5(b):
    return hashlib.md5(b).hexdigest()


def patch(name, data):
    spec = LIBS[name]
    buf = bytearray(data)
    for pat, rel, new in spec["patches"]:
        hits, i = [], buf.find(pat)
        while i >= 0:
            hits.append(i)
            i = buf.find(pat, i + 1)
        if len(hits) != 1:
            sys.exit(f"{name}: pattern {pat.hex()} found {len(hits)} times (need exactly 1) - unknown build, "
                     f"nothing written")
        off = hits[0] + rel
        old = bytes(buf[off:off + len(new)])
        buf[off:off + len(new)] = new
        print(f"  {name}: {off:#07x} {old.hex()} -> {new.hex()}")
    out = bytes(buf)
    if md5(data) == spec["stock_md5"] and md5(out) != spec["patched_md5"]:
        sys.exit(f"{name}: result md5 {md5(out)} != expected {spec['patched_md5']}")
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    here = os.environ.get("GB_HOME") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "inject", "vendor_gfx")
    outs = {}
    for name, spec in LIBS.items():
        data = open(os.path.join(sys.argv[1], name), "rb").read()
        ref = " (reference build)" if md5(data) == spec["stock_md5"] else " (not the reference build)"
        print(f"{name}: stock md5 {md5(data)}{ref}")
        outs[name] = patch(name, data)
    os.makedirs(dst, exist_ok=True)
    for name, out in outs.items():
        with open(os.path.join(dst, name), "wb") as f:
            f.write(out)
        print(f"wrote {os.path.join(dst, name)} (md5 {md5(out)})")


if __name__ == "__main__":
    main()
