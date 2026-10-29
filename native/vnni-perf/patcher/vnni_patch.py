# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Googlebook OS build (GB-build) authors.
#
# vnni_patch.py -- build-time patcher that steers zlib's adler32 dispatch on
# Googlebook OS (lapis / Android 17) away from the VEX AVX-VNNI code path, so
# it no longer traps into the k15 #UD emulator on Ice Lake.
#
# Idea credit: the gbOS86 research project observed that libz.so's adler32 is
# the dominant VEX AVX-VNNI trap source and that the `je` after the SSSE3
# feature-flag test in adler32_z gates the SIMD path. This is an independent,
# clean-room implementation (no gbOS86 code was copied); see README.md.
#
# Design (see README.md):
#   * Every patch is a tiny, in-place byte edit that flips a *dispatch branch*
#     or clears a *feature flag* so an existing non-VNNI (scalar / older-ISA)
#     path is chosen. We never rewrite arithmetic.
#   * Each target is pinned to the original file's sha256. No sha256 match =>
#     no patch is written for that file (the k15 emulator still covers
#     correctness at runtime, so skipping is always safe).
#   * Each edit also records the exact original bytes and is applied only if
#     those bytes are found at the computed offset. A single byte of drift =>
#     that target is refused, nothing is written.
#   * If the manifest records the expected `patched_sha256`, the result must
#     hash to exactly that value or the target is refused (determinism check).
#   * The patcher is ELF-aware only enough to map a virtual address to a file
#     offset through the program headers; it parses no sections and relies on
#     no external tools, so it runs identically on the Windows build host and
#     on the Linux builder.
#
# Deployment gate (review fix, 2026-10-26).  The sha256 pin above is a BUILD-time
# check.  On the device, gb_slotsync rebuilds the other slot's initrd from the
# SAME /gb/inject.gz after an A/B OTA, so whatever /odm tree and gb.rc this build
# stages is reused for a slot holding a DIFFERENT build.  An unconditional
# `on early-init` bind would then mount this build's (old) patched libz over the
# new build's libz.  Therefore every bind this tool emits (--out-rc) is gated on
# the partition's build fingerprint, read by init from the verified partition's
# own build.prop before early-init:
#     on early-init && property:ro.system.build.fingerprint=<exact value>
# A different build (OTA, other slot, rollback target) has a different
# fingerprint, the action never fires, and the stock library runs under k15
# emulation.  The fingerprint is taken from the in-root's build.prop of that
# partition when present (the image actually being built), else from the
# manifest's `gate_value`.  No bind is ever emitted without a gate.
#
# Usage:
#   python -I vnni_patch.py --manifest manifest.json --in-root <dir> \
#       --out-root <dir> [--out-manifest out-manifest.json] [--scan] [--strict] \
#       [--out-rc vnni.rc] [--odm-root <dir>]
#
#   --in-root    directory the manifest's `path` hints are resolved against
#                (e.g. a carved/extracted system+vendor tree, or an erofs mount);
#                put the partitions' build.prop there too (system/build.prop,
#                vendor/build.prop) so the bind gate uses the image's own value
#   --out-root   patched copies are written here, mirroring each target `out`
#   --out-rc     write the init rc fragment with the fingerprint-gated binds for
#                every patched target that has a `deploy` block (empty file =
#                nothing to bind)
#   --odm-root   also write each deployable patched copy as <dir>/<odm_name>
#                (what build_esp.py stages as /odm/vnni/<odm_name>)
#   --scan       if a `path` hint is missing or its sha256 does not match,
#                search --in-root for ANY regular file whose sha256 matches the
#                target's orig_sha256 (slower; off by default)
#   --strict     exit non-zero if any *expected* target was not patched
#                (default: skips are reported but the run still succeeds, which
#                is the safe pipeline default -- a skipped file just keeps
#                being emulated)
#
# Exit code 0 unless an unexpected error occurred (or --strict and a miss).

import argparse
import hashlib
import json
import os
import struct
import sys

PROG = "vnni_patch"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


class ElfError(Exception):
    pass


def elf_load_segments(data):
    """Return [(p_vaddr, p_offset, p_filesz)] for every PT_LOAD of a 64-bit
    little-endian ELF.  Raises ElfError on anything we do not understand, so a
    non-ELF or odd file is refused rather than mispatched."""
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise ElfError("not an ELF file")
    ei_class, ei_data = data[4], data[5]
    if ei_class != 2 or ei_data != 1:
        raise ElfError("only 64-bit little-endian ELF is supported")
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", data, 0x36)[0]
    e_phnum = struct.unpack_from("<H", data, 0x38)[0]
    if e_phentsize < 56 or e_phoff == 0 or e_phoff + e_phnum * e_phentsize > len(data):
        raise ElfError("bad program header table")
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type = struct.unpack_from("<I", data, off)[0]
        if p_type != 1:  # PT_LOAD
            continue
        p_offset, p_vaddr = struct.unpack_from("<QQ", data, off + 8)
        p_filesz = struct.unpack_from("<Q", data, off + 32)[0]
        segs.append((p_vaddr, p_offset, p_filesz))
    if not segs:
        raise ElfError("no PT_LOAD segments")
    return segs


def vaddr_to_offset(segs, vaddr):
    """Map a virtual address to a file offset inside file-backed bytes of a
    PT_LOAD segment.  Returns None if the vaddr is not file-backed."""
    for p_vaddr, p_offset, p_filesz in segs:
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            return p_offset + (vaddr - p_vaddr)
    return None


def _hexbytes(s):
    """'0f8458ffffff' or '0f 84 58 ...' -> bytes."""
    return bytes.fromhex(s.replace(" ", "").replace("0x", ""))


def _as_int(v):
    return int(v, 0) if isinstance(v, str) else int(v)


def apply_edits(data, target):
    """Apply one target's edits to a mutable bytearray copy of `data`.

    Returns (patched_bytearray, applied_edits).  Raises ValueError if any edit
    does not line up exactly (wrong offset, original bytes differ, lengths
    differ), so the whole target is refused and nothing is written."""
    buf = bytearray(data)
    segs = elf_load_segments(data)
    applied = []
    for e in target["edits"]:
        orig = _hexbytes(e["orig"])
        new = _hexbytes(e["new"])
        if len(orig) != len(new):
            raise ValueError(
                "edit %r: orig/new length differ (%d vs %d); patches must be "
                "length-preserving" % (e.get("desc", ""), len(orig), len(new))
            )
        # Prefer an explicit file offset; otherwise derive it from the vaddr.
        off = None
        if "file_off" in e:
            off = _as_int(e["file_off"])
        if "vaddr" in e:
            voff = vaddr_to_offset(segs, _as_int(e["vaddr"]))
            if voff is None:
                raise ValueError("edit %r: vaddr %s not file-backed"
                                 % (e.get("desc", ""), e["vaddr"]))
            if off is None:
                off = voff
            elif off != voff:
                raise ValueError(
                    "edit %r: file_off %#x disagrees with vaddr-derived %#x"
                    % (e.get("desc", ""), off, voff)
                )
        if off is None:
            raise ValueError("edit %r: neither file_off nor vaddr given"
                             % e.get("desc", ""))
        if off + len(orig) > len(buf):
            raise ValueError("edit %r: offset %#x past end of file"
                             % (e.get("desc", ""), off))
        found = bytes(buf[off:off + len(orig)])
        if found != orig:
            raise ValueError(
                "edit %r at %#x: original bytes %s != expected %s"
                % (e.get("desc", ""), off, found.hex(), orig.hex())
            )
        buf[off:off + len(new)] = new
        applied.append({"file_off": "%#x" % off,
                        "vaddr": e.get("vaddr"),
                        "orig": orig.hex(), "new": new.hex(),
                        "desc": e.get("desc", "")})
    return buf, applied


def find_by_sha(in_root, want_sha):
    for dirpath, _dirs, files in os.walk(in_root):
        for name in files:
            p = os.path.join(dirpath, name)
            try:
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                if os.path.getsize(p) > 256 * 1024 * 1024:
                    continue
                if sha256_file(p) == want_sha:
                    return p
            except OSError:
                continue
    return None


def resolve_target_path(in_root, target, scan):
    """Return (path, sha, matched) for a target, or (path_or_None, sha_or_None, False)."""
    hint = target.get("path")
    hint_path = hint_sha = None
    if hint:
        hint_path = os.path.join(in_root, hint.replace("/", os.sep))
        # a symlink is never patched (its target may be outside the pinned tree)
        if os.path.isfile(hint_path) and not os.path.islink(hint_path):
            hint_sha = sha256_file(hint_path)
            if hint_sha == target["orig_sha256"]:
                return hint_path, hint_sha, True
    if scan:
        p = find_by_sha(in_root, target["orig_sha256"])
        if p:
            return p, target["orig_sha256"], True
    if hint_path is not None and not os.path.lexists(hint_path):
        hint_path = None
    return hint_path, hint_sha, False


_PROP_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "0123456789._-/:+,@~")


def read_prop(path, name):
    """Value of `name` in an Android build.prop, or None (last assignment wins,
    as in init's loader)."""
    val = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == name:
                val = v.strip()
    return val


def bind_gate(in_root, deploy):
    """(prop, value, source) for a deploy block, or raise ValueError.  The value
    must be a non-empty init-safe token (init matches property triggers by exact
    string; whitespace would split the trigger)."""
    prop = deploy["gate_prop"]
    val = src = None
    pf = deploy.get("gate_prop_file")
    if pf:
        p = os.path.join(in_root, pf.replace("/", os.sep))
        if os.path.isfile(p):
            val = read_prop(p, prop)
            src = "in-root " + pf
            if val is None:
                raise ValueError("%s has no %s" % (pf, prop))
    if val is None:
        val = deploy.get("gate_value")
        src = "manifest gate_value"
    if not val or any(c not in _PROP_SAFE for c in val):
        raise ValueError("unusable gate value %r for %s" % (val, prop))
    if not all(c in _PROP_SAFE for c in prop) or "=" in prop:
        raise ValueError("bad gate property name %r" % prop)
    return prop, val, src


def rc_block(deploy, prop, val, odm_dir):
    """init actions for one gated bind.  The bind is remounted read-only in
    init's mount namespace only after the relabel (restorecon needs a writable
    mount); the inode stays writable through the rw /odm tmpfs (same trust as
    every other /odm injection)."""
    src = "%s/%s" % (odm_dir.rstrip("/"), deploy["odm_name"])
    dst = deploy["bind"]
    return ("on early-init && property:%s=%s\n"
            "    restorecon %s\n"
            "    mount none %s %s bind\n"
            "    restorecon %s\n"
            "    mount none none %s remount bind ro\n"
            % (prop, val, src, src, dst, dst, dst))


def main(argv):
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--in-root", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--out-manifest", default=None)
    ap.add_argument("--scan", action="store_true",
                    help="search --in-root by sha256 when the path hint misses")
    ap.add_argument("--strict", action="store_true",
                    help="fail if any expected target was not patched")
    ap.add_argument("--out-rc", default=None,
                    help="write the fingerprint-gated init bind fragment here")
    ap.add_argument("--odm-root", default=None,
                    help="also write deployable patched copies as <dir>/<odm_name>")
    ap.add_argument("--odm-dir", default="/odm/vnni",
                    help="on-device directory the rc binds from (default /odm/vnni)")
    args = ap.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    results = []
    rc_parts = []
    n_patched = n_skipped = n_refused = 0
    for target in manifest["targets"]:
        name = target.get("name", target["orig_sha256"][:12])
        path, sha, matched = resolve_target_path(args.in_root, target, args.scan)
        rec = {"name": name, "orig_sha256": target["orig_sha256"],
               "path": None if path is None else os.path.relpath(path, args.in_root),
               "found_sha256": sha, "proof": target.get("proof"),
               "strategy": target.get("strategy"), "status": None}
        if not matched:
            rec["status"] = "skipped-no-sha256-match"
            rec["note"] = ("file absent" if path is None
                           else "sha256 %s != orig %s" % (sha, target["orig_sha256"]))
            n_skipped += 1
            results.append(rec)
            print("[skip ] %-28s %s" % (name, rec["note"]))
            continue
        with open(path, "rb") as f:
            data = f.read()
        try:
            patched, applied = apply_edits(data, target)
        except (ValueError, ElfError) as ex:
            rec["status"] = "refused"
            rec["note"] = str(ex)
            n_refused += 1
            results.append(rec)
            print("[REFUSE] %-27s %s" % (name, ex), file=sys.stderr)
            continue
        patched_sha = sha256_bytes(bytes(patched))
        want_patched = target.get("patched_sha256")
        if want_patched and want_patched != patched_sha:
            rec["status"] = "refused"
            rec["note"] = ("patched sha256 %s != manifest patched_sha256 %s"
                           % (patched_sha, want_patched))
            n_refused += 1
            results.append(rec)
            print("[REFUSE] %-27s %s" % (name, rec["note"]), file=sys.stderr)
            continue
        out_rel = target["out"]
        out_path = os.path.join(args.out_root, out_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(patched)
        rec["status"] = "patched"
        rec["out"] = out_rel
        rec["patched_sha256"] = patched_sha
        rec["edits"] = applied
        deploy = target.get("deploy")
        if deploy:
            try:
                prop, val, src = bind_gate(args.in_root, deploy)
            except (ValueError, KeyError, OSError) as ex:
                # patched copy is still written for inspection, but it is NOT
                # deployable: no gate => no bind
                rec["deploy"] = {"status": "no-bind", "note": str(ex)}
                print("[nobind] %-27s %s" % (name, ex), file=sys.stderr)
            else:
                rc_parts.append(rc_block(deploy, prop, val, args.odm_dir))
                rec["deploy"] = {"status": "bind", "bind": deploy["bind"],
                                 "odm_name": deploy["odm_name"],
                                 "gate_prop": prop, "gate_value": val,
                                 "gate_source": src}
                if args.odm_root:
                    op = os.path.join(args.odm_root, deploy["odm_name"])
                    os.makedirs(os.path.dirname(op) or ".", exist_ok=True)
                    with open(op, "wb") as f:
                        f.write(patched)
        else:
            rec["deploy"] = {"status": "not-deployable",
                             "note": target.get("deploy_note", "no deploy block")}
        n_patched += 1
        results.append(rec)
        print("[patch] %-28s %s -> %s (%d edit%s) %s%s"
              % (name, target["orig_sha256"][:12], patched_sha[:12],
                 len(applied), "" if len(applied) == 1 else "s", out_rel,
                 "  [bind gated on %s]" % rec["deploy"]["gate_prop"]
                 if rec["deploy"]["status"] == "bind" else ""))

    out_manifest = {
        "tool": PROG, "version": 1,
        "build": manifest.get("build"),
        "in_root": os.path.abspath(args.in_root),
        "out_root": os.path.abspath(args.out_root),
        "counts": {"patched": n_patched, "skipped": n_skipped, "refused": n_refused},
        "results": results,
    }
    rc_text = ""
    if rc_parts:
        rc_text = ("# vnni libz patch (generated by vnni_patch.py, Apache-2.0).\n"
                   "# Each bind fires ONLY on the exact build it was patched for:\n"
                   "# after an A/B OTA gb_slotsync reuses this initrd for the new\n"
                   "# slot, whose fingerprint differs, so nothing is bound there.\n"
                   + "\n".join(rc_parts))
    out_manifest["rc"] = rc_text
    if args.out_rc:
        with open(args.out_rc, "w", encoding="utf-8", newline="\n") as f:
            f.write(rc_text)
    if args.out_manifest:
        with open(args.out_manifest, "w", encoding="utf-8") as f:
            json.dump(out_manifest, f, indent=2, sort_keys=False)
            f.write("\n")
    print("\n%s: %d patched, %d skipped (no sha256 match), %d refused "
          "(byte/hash mismatch), %d gated bind(s)"
          % (PROG, n_patched, n_skipped, n_refused, len(rc_parts)))

    if n_refused:
        return 2
    if args.strict:
        expected = sum(1 for t in manifest["targets"] if t.get("expected", True))
        if n_patched < expected:
            print("%s: --strict: patched %d of %d expected targets"
                  % (PROG, n_patched, expected), file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
