# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Googlebook OS build (GB-build) authors.
#
# gen_manifest.py -- derive the vnni_patch.py manifest directly from the
# binaries, so every edit is provably located (not hand-typed) and cross-checked
# against the measured VNNI instruction addresses in scan.json.
#
# For each input ELF it looks for the Chromium-zlib adler32 dispatch gate by a
# byte signature and, independently, for the SSSE3 feature-flag writer:
#
#   branch_flip site (preferred): the sequence
#       83 3d ib ib ib ib 00     cmpl $0x0, <rip32>      ; test x86_cpu_enable_ssse3
#       0f 84 r0 r1 r2 r3        je   <scalar>           ; <-- the edit
#       e8 c0 c1 c2 c3           call <adler32_simd_>
#   The `je` becomes `90 e9 r0 r1 r2 r3` (nop ; jmp <scalar>): same target, same
#   length, so adler32_z's len>=64 path always takes the scalar branch and never
#   reaches adler32_simd_.  Confirmed three ways: (a) the cmpl's rip target is the
#   flag that _cpu_check_features fills with CPUID.1:ECX&0x200, (b) the call
#   target lands immediately before a run of VEX vpdpwssd addresses from
#   scan.json, (c) the je target (scalar) is inside the same function, earlier.
#
#   flag_zero site (fallback, e.g. snapuserd where the gate is inlined oddly):
#       0f a2                    cpuid
#       ... (<= 48 bytes) ...
#       25 00 02 00 00           and  $0x200, %eax       ; <-- the edit -> and $0
#       89 05 ib ib ib ib        mov  %eax, <rip32>       ; store the SSSE3 flag
#   The `and $0x200` becomes `and $0x0` (25 00 00 00 00): the SSSE3 flag is always
#   0, so adler32_z's own `je` (unpatched) always takes the scalar branch.
#
# A target gets the branch_flip edit when found; otherwise the flag_zero edit.
# The other option, when also found, is recorded under "alt_edits" for the record.
#
# Usage (on the builder, where scan.json and the extracted tree live):
#   python3 -I gen_manifest.py --scan scan.json --root <fs/apex root> \
#       --build "CL3B.260622.276.R4 / 16658739" FILE[:execution|disasm] ...
#   where each FILE is a path under --root; the optional suffix sets the proof
#   method recorded for that target (default disasm; the two exported libz.so
#   are execution).  Writes the manifest JSON to stdout.
#
#   --build-prop system=<path> --build-prop vendor=<path>   (review fix)
#   The exported system/vendor libz.so get a `deploy` block: the device path to
#   bind over, the /odm file name, and the build-fingerprint property that gates
#   the bind (vnni_patch.py --out-rc).  gate_value is read from the given
#   build.prop of the carved partition.  Every target also records the expected
#   `patched_sha256` (the edits applied with vnni_patch.apply_edits), and the
#   static copies record why they are not deployed (`deploy_note`).

import argparse
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vnni_patch  # noqa: E402  (same directory; -I drops the script dir)

# exported libz.so: (device bind path, /odm name, gate property, build.prop in an in-root)
DEPLOY = {
    "system/lib64/libz.so": ("/system/lib64/libz.so", "system_libz.so",
                             "ro.system.build.fingerprint", "system/build.prop", "system"),
    "vendor/lib64/libz.so": ("/vendor/lib64/libz.so", "vendor_libz.so",
                             "ro.vendor.build.fingerprint", "vendor/build.prop", "vendor"),
}

NOT_DEPLOYED = {
    "system/bin/init": "not deployable by bind: second-stage init is exec'd by first-stage init "
                       "before any rc runs, and ueventd / the vendor_init subcontext are the same "
                       "running binary (measured load: 16 traps in the native tree)",
    "system/bin/bootstrap/linker64": "not deployed: PT_INTERP of every bootstrap-namespace "
                                     "executable; a bad bind breaks every exec (load not captured)",
    "apex/com.android.runtime/bin/linker64": "not deployed: inside an APEX (private /apex per mount "
                                             "namespace) and PT_INTERP of every dynamic executable",
    "apex/com.google.android.appsearch/lib64/libicing_anywhere.so":
        "not deployed by default: inside an APEX -- /apex is a private mount point per mount "
        "namespace, so an init bind at early-init is not seen by default-namespace processes, and "
        "a /data/apex update re-mounts over it (INTEGRATION.md section 4)",
}
NOT_DEPLOYED_DEFAULT = ("not deployed by default: bindable in principle but no runtime load was "
                        "captured; flag_zero is proven by disassembly only")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_segments(data):
    assert data[:4] == b"\x7fELF" and data[4] == 2 and data[5] == 1, "not LE64 ELF"
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", data, 0x38)[0]
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", data, off)[0] != 1:
            continue
        p_flags = struct.unpack_from("<I", data, off + 4)[0]
        p_offset, p_vaddr = struct.unpack_from("<QQ", data, off + 8)
        p_filesz = struct.unpack_from("<Q", data, off + 32)[0]
        segs.append((p_vaddr, p_offset, p_filesz, p_flags))
    return segs


def off_to_vaddr(segs, off):
    for p_vaddr, p_offset, p_filesz, _fl in segs:
        if p_offset <= off < p_offset + p_filesz:
            return p_vaddr + (off - p_offset)
    return None


def vaddr_to_off(segs, v):
    for p_vaddr, p_offset, p_filesz, _fl in segs:
        if p_vaddr <= v < p_vaddr + p_filesz:
            return p_offset + (v - p_vaddr)
    return None


def exec_ranges(segs):
    # file-offset ranges of executable (X) PT_LOAD segments
    return [(p_offset, p_offset + p_filesz) for (_v, p_offset, p_filesz, fl) in segs if fl & 1]


def rip_target(data, segs, insn_off, disp_off, insn_len):
    """Absolute vaddr a rip-relative disp32 at file offset disp_off points to,
    for an instruction that starts at insn_off and is insn_len long."""
    disp = struct.unpack_from("<i", data, disp_off)[0]
    v_next = off_to_vaddr(segs, insn_off) + insn_len
    return v_next + disp


def find_branch_flip(data, segs, vnni_vaddrs):
    hits = []
    for a, b in exec_ranges(segs):
        i = a
        end = b - 15
        buf = data
        while True:
            # cmpl $0x0, rip32 : 83 3d <disp32> 00
            j = buf.find(b"\x83\x3d", i, end)
            if j < 0:
                break
            i = j + 1
            if buf[j + 6] != 0x00:
                continue
            # immediately followed by je rel32 : 0f 84 <rel32>
            if buf[j + 7:j + 9] != b"\x0f\x84":
                continue
            je_off = j + 7
            # then call rel32 : e8 <rel32>
            call_off = je_off + 6
            if buf[call_off] != 0xe8:
                continue
            # (a) cmpl rip target == a feature flag (data, not exec)
            flag_v = rip_target(buf, segs, j, j + 2, 7)
            # (b) call target just before a vpdpwssd from scan.json
            call_target = rip_target(buf, segs, call_off, call_off + 1, 5)
            near = [v for v in vnni_vaddrs if 0 <= v - call_target <= 0x200]
            # (c) je target (scalar) earlier in the same exec range
            je_target = rip_target(buf, segs, je_off, je_off + 2, 6)
            if not near:
                continue
            je_va = off_to_vaddr(segs, je_off)
            raw = bytes(buf[je_off:je_off + 6])
            new = b"\x90\xe9" + raw[2:6]
            hits.append({
                "vaddr": "%#x" % je_va,
                "file_off": "%#x" % je_off,
                "orig": raw.hex(),
                "new": new.hex(),
                "desc": ("adler32_z: je(scalar)->nop;jmp(scalar); len>=64 path "
                         "skips adler32_simd_ (flag@%#x, call->%#x, simd@%#x, scalar@%#x)"
                         % (flag_v, call_target, min(near), je_target)),
                "_flag_vaddr": flag_v,
            })
    return hits


def find_flag_zero(data, segs):
    hits = []
    for a, b in exec_ranges(segs):
        i = a
        while True:
            c = data.find(b"\x0f\xa2", i, b)  # cpuid
            if c < 0:
                break
            i = c + 1
            window = data[c:c + 64]
            k = window.find(b"\x25\x00\x02\x00\x00")  # and $0x200,%eax
            if k < 0:
                continue
            and_off = c + k
            if data[and_off + 5:and_off + 7] != b"\x89\x05":  # mov %eax, rip32
                continue
            flag_v = rip_target(data, segs, and_off + 5, and_off + 7, 6)
            and_va = off_to_vaddr(segs, and_off)
            hits.append({
                "vaddr": "%#x" % and_va,
                "file_off": "%#x" % and_off,
                "orig": "2500020000",
                "new": "2500000000",
                "desc": ("_cpu_check_features: and $0x200,%%eax -> and $0x0 "
                         "(SSSE3 flag@%#x always 0; adler32_z then always scalar)" % flag_v),
                "_flag_vaddr": flag_v,
            })
    return hits


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True, help="scan.json from scan_vnni.py")
    ap.add_argument("--root", required=True)
    ap.add_argument("--build", default=None)
    ap.add_argument("--build-prop", action="append", default=[],
                    help="PART=PATH (PART system|vendor): build.prop of the carved "
                         "partition, for the bind gate value; PATH relative to --root")
    ap.add_argument("files", nargs="+",
                    help="scanpath[>destpath][:execution|disasm]; scanpath is read "
                         "under --root, destpath is the manifest path/out (default scanpath)")
    args = ap.parse_args(argv)
    build_props = {}
    for bp in args.build_prop:
        part, _, p = bp.partition("=")
        build_props[part] = p if os.path.isabs(p) else os.path.join(args.root, p)

    with open(args.scan, encoding="utf-8") as f:
        scan = json.load(f)
    # scan.json is a list of ELF records; the verified VEX VNNI instruction
    # addresses of each are in rec["insns"][*]["addr"] (hex strings).
    # map: scan path -> set of verified VNNI vaddrs
    vnni_by_relpath = {}
    for rec in (scan["elfs"] if isinstance(scan, dict) and "elfs" in scan else scan):
        rel = rec.get("path") or rec.get("file")
        if not rel:
            continue
        vas = set()
        for h in rec.get("insns", []):
            v = h.get("addr")
            if v is not None:
                vas.add(int(v, 0) if isinstance(v, str) else int(v))
        vnni_by_relpath[rel] = vas

    targets = []
    for spec in args.files:
        proof = "disasm"
        if ":" in spec and spec.rsplit(":", 1)[1] in ("execution", "disasm"):
            spec, proof = spec.rsplit(":", 1)
        if ">" in spec:
            scanpath, destpath = spec.split(">", 1)
        else:
            scanpath = destpath = spec
        path = os.path.join(args.root, scanpath)
        with open(path, "rb") as f:
            data = f.read()
        segs = load_segments(data)
        # find the scan.json entry whose path ends with this scanpath
        vnni = set()
        for k, v in vnni_by_relpath.items():
            if k.replace("\\", "/").endswith(scanpath.replace("\\", "/")):
                vnni = v
                break
        bf = find_branch_flip(data, segs, vnni)
        fz = find_flag_zero(data, segs)
        if bf:
            chosen, strategy = bf, "branch_flip"
            alt = fz
        elif fz:
            chosen, strategy = fz, "flag_zero"
            alt = []
            sys.stderr.write("%s: no branch_flip site; using flag_zero\n" % scanpath)
        else:
            sys.stderr.write("%s: NO patch site found\n" % scanpath)
            continue
        edits = [{k: e[k] for k in ("vaddr", "file_off", "orig", "new", "desc")}
                 for e in chosen]
        t = {
            "name": destpath,
            "orig_sha256": sha256_file(path),
            "scan_path": scanpath,
            "path": destpath,
            "out": destpath,
            "exports_zlib": destpath.endswith("lib64/libz.so"),
            "proof": proof,
            "strategy": strategy,
            "edits": edits,
        }
        if alt:
            t["alt_edits"] = [{k: e[k] for k in ("vaddr", "file_off", "orig", "new", "desc")}
                              for e in alt]
        # expected result (determinism pin; vnni_patch refuses any other output)
        patched, _applied = vnni_patch.apply_edits(data, t)
        t["patched_sha256"] = hashlib.sha256(bytes(patched)).hexdigest()
        if destpath in DEPLOY:
            bind, odm_name, gprop, gfile, part = DEPLOY[destpath]
            gval = None
            if part in build_props:
                gval = vnni_patch.read_prop(build_props[part], gprop)
            if not gval:
                sys.stderr.write("%s: no %s (pass --build-prop %s=...); no deploy block\n"
                                 % (destpath, gprop, part))
            else:
                t["deploy"] = {"bind": bind, "odm_name": odm_name, "gate_prop": gprop,
                               "gate_prop_file": gfile, "gate_value": gval}
        else:
            t["deploy_note"] = NOT_DEPLOYED.get(destpath, NOT_DEPLOYED_DEFAULT)
        targets.append(t)

    manifest = {
        "build": args.build,
        "note": ("Build-time VEX AVX-VNNI avoidance for zlib adler32. Each target is "
                 "pinned to orig_sha256 (and its result to patched_sha256); no match => no "
                 "patch (k15 still emulates). Only targets with a deploy block are bound, "
                 "and every bind is gated at boot on the partition's build fingerprint "
                 "(gb_slotsync reuses the same initrd for an OTA'd slot). "
                 "Idea credit: gbOS86 (clean-room reimplementation, no code copied)."),
        "targets": targets,
    }
    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
