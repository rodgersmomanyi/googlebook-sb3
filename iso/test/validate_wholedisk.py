r"""Whole-disk validation (DESIGN.md 7.2 T1a, T1b, T1c). Read-only on every file it opens.

  python -I validate_wholedisk.py exact <raw>        T1a: sha256 == the USB image's, verify_image.py RESULT: OK
  python -I validate_wholedisk.py grow  <raw>        T1b: grown userdata, backup GPT at the end, contents
  python -I validate_wholedisk.py nogrow <raw>       T1c: entries identical to the image, backup GPT at the end

The payload manifest is D:\GB-iso\googlebook-sb3-installer.manifest.json (copy of gbsb3/manifest.json, 1.4); the
reference GPT is read from C:\GB-x86\out\googlebook-sb3-usb.img (opened read-only).
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import json
import os
import struct
import subprocess
import sys

import common as C
import mkdisks


def load_manifest(path=C.PAYLOAD_MANIFEST):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def image_gpt():
    """(primary 17408 bytes, backup 16896 bytes) of the USB image, read-only."""
    with open(C.USB_IMG, "rb") as f:
        prim = f.read(17408)
        f.seek(C.USB_SIZE - 16896)
        back = f.read(16896)
    return prim, back


def entries_of(primary):
    return [primary[1024 + i * 128:1024 + (i + 1) * 128] for i in range(128)]


def sha256_range(f, off, n):
    import hashlib
    h = hashlib.sha256()
    f.seek(off)
    while n:
        b = f.read(min(n, 16 * C.MiB))
        if not b:
            raise IOError("short read")
        h.update(b)
        n -= len(b)
    return h.hexdigest()


def zero_range(f, off, n):
    z = bytes(16 * C.MiB)
    f.seek(off)
    while n:
        b = f.read(min(n, 16 * C.MiB))
        if not b:
            return False
        if b != z[:len(b)]:
            return False
        n -= len(b)
    return True


def zero_ranges_405(length):
    """4.0.5 for a blank partition of length L: L <= 256 MiB -> [0, L); else [0, 64 MiB) and [L - 64 MiB, L)."""
    if length <= C.BIG_BLANK:
        return [(0, length)]
    return [(0, C.HEAD_TAIL), (length - C.HEAD_TAIL, C.HEAD_TAIL)]


def run_verify_image(raw, res, control_ok):
    """verify_image.py <raw>: RESULT: OK and exit 0; INCONCLUSIVE (7.1) if the control run on the USB image was not
    OK (lapis.bin changed)."""
    p = subprocess.run([sys.executable, "-I", C.VERIFY_IMAGE, raw], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=7200)
    out = p.stdout.decode(errors="replace")
    res.evidence["verify_image"] = {"exit": p.returncode, "tail": out[-3000:]}
    ok = p.returncode == 0 and "RESULT: OK" in out
    if not control_ok:
        res.info("verify_image.py %s: %s (INCONCLUSIVE: the control run on the USB image was not OK)"
                 % (raw, "OK" if ok else "not OK"))
        res.evidence["verify_image_inconclusive"] = True
        return None
    res.check(ok, "verify_image.py <raw> prints RESULT: OK, exit 0 (exit %d)" % p.returncode)
    return ok


def check_exact(raw, res, control_ok=True):
    """T1a host-side criteria."""
    sha = C.sparse_sha256(raw)
    res.evidence["raw_sha256"] = sha
    res.check(os.path.getsize(raw) == C.USB_SIZE, "raw size == %d" % C.USB_SIZE)
    res.check(sha == C.USB_SHA, "sha256 of the raw file == the USB image's (%s)" % sha)
    run_verify_image(raw, res, control_ok)


def check_contents(f, res, man, layout):
    """Sourced partitions == object sha256 (+ zero tail), blank partitions zero per 4.0.5. layout: {slot: (first_lba,
    last_lba)} on the target."""
    objs = man["objects"]
    for p in man["whole_disk"]["partitions"]:
        first, last = layout[p["slot"]]
        off, L = first * C.SECTOR, (last - first + 1) * C.SECTOR
        if p["object"]:
            o = objs[p["object"]]
            got = sha256_range(f, off, o["raw_size"])
            res.check(got == o["sha256"], "%s: sha256 over %d raw bytes == object %s" % (p["name"], o["raw_size"],
                                                                                     p["object"]))
            if L > o["raw_size"]:
                res.check(zero_range(f, off + o["raw_size"], L - o["raw_size"]), "%s: tail after raw_size zero"
                          % p["name"])
        else:
            ok = all(zero_range(f, off + a, n) for a, n in zero_ranges_405(L))
            res.check(ok, "%s: blank, zero per 4.0.5 (%d MiB)" % (p["name"], L // C.MiB))


def check_grown(raw, res, grow=True, control_ok=True, man=None):
    """T1b (grow=True) / T1c (grow=False) host-side criteria."""
    man = man or load_manifest()
    size = os.path.getsize(raw)
    N = size // C.SECTOR
    info, probs = mkdisks.read_gpt(raw)
    res.evidence["gpt_problems"] = probs
    res.check(info is not None and not probs, "GPT valid (gpt_validate read_gpt: %s)" % (probs or "no problems"))
    if info is None:
        return
    iprim, _ = image_gpt()
    ient = entries_of(iprim)
    h1, h2 = info["h1"], info["h2"]
    res.check(h1["alt"] == N - 1 and h2["my"] == N - 1, "backup header at N-1 (%d)" % (N - 1))
    res.check(h2["elba"] == N - 33, "backup entries at N-33 (%d)" % (N - 33))
    res.check(h1["last"] == N - 34, "last_usable_lba == N-34 (%d)" % h1["last"])
    img_disk = str(__import__("uuid").UUID(bytes_le=iprim[512 + 56:512 + 72]))
    res.check(h1["disk"] == img_disk, "disk GUID identical to the image (%s)" % img_disk)
    res.check(h1["first"] == 34, "first usable LBA unchanged (34)")
    with open(raw, "rb") as f:
        f.seek(1024)
        cur = f.read(128 * 128)
    cent = [cur[i * 128:(i + 1) * 128] for i in range(128)]
    ud_slot = [p["slot"] for p in man["whole_disk"]["partitions"] if p["growable"]][0]
    if grow:
        want_last = ((N - 33 - 2048) // 2048) * 2048 - 1
        got_last = struct.unpack_from("<Q", cent[ud_slot], 40)[0]
        res.check(got_last == want_last, "userdata last_lba %d == floor((N-33-2048)/2048)*2048-1 = %d"
                  % (got_last, want_last))
        if N == 67108864:
            res.check(got_last == 67104767, "userdata last_lba == 67,104,767 (32 GiB disk)")
        res.check(cent[ud_slot][:40] == ient[ud_slot][:40] and cent[ud_slot][48:] == ient[ud_slot][48:],
                  "userdata name/GUID/type/attrs/first_lba as in the image")
        others = [i for i in range(128) if i != ud_slot]
        res.check(all(cent[i] == ient[i] for i in others), "the other 17 entries' 128 bytes (and unused slots) "
                                                           "identical to the image")
    else:
        res.check(cur == b"".join(ient), "entry array identical to the image")
    layout = {}
    for i, e in enumerate(cent):
        if e[:16] != bytes(16):
            a, b = struct.unpack_from("<QQ", e, 32)
            layout[i] = (a, b)
    with open(raw, "rb") as f:
        res.check(zero_range(f, 34 * C.SECTOR, (2048 - 34) * C.SECTOR), "LBA 34..2047 zero")
        ud_last = layout[ud_slot][1]
        gap_end = (N - 33) if grow else (man["whole_disk"]["total_lba"] - 33)
        res.check(zero_range(f, (ud_last + 1) * C.SECTOR, (gap_end - ud_last - 1) * C.SECTOR),
                  "gap after userdata zero (LBA %d..%d)" % (ud_last + 1, gap_end - 1))
        check_contents(f, res, man, layout)
    run_verify_image(raw, res, control_ok)


def written_areas(N, man, grow):
    """4.0.2/4.1.6: the byte ranges mode 1 writes on a disk of N sectors: head and tail, every sourced partition
    (object + zero tail), the zero ranges of every blank partition (4.0.5), the gap after userdata."""
    wd = man["whole_disk"]
    T = wd["total_lba"]
    out = [(0, 2048 * C.SECTOR), ((N - 2081) * C.SECTOR, N * C.SECTOR)]
    grown_last = ((N - 33 - 2048) // 2048) * 2048 - 1
    ud_last = None
    for p in wd["partitions"]:
        last = grown_last if (grow and p["growable"]) else p["last_lba"]
        f, L = p["first_lba"] * C.SECTOR, (last - p["first_lba"] + 1) * C.SECTOR
        if p["growable"]:
            ud_last = last
        if p["object"]:
            out.append((f, f + L))
        else:
            out += [(f + a, f + a + n) for a, n in zero_ranges_405(L)]
    gap_end = (N - 33) if grow else (T - 33)
    out.append(((ud_last + 1) * C.SECTOR, gap_end * C.SECTOR))
    return sorted(out)


def check_patterned(raw, res, grow, man=None):
    """T1d: on a disk that held pattern data, every pattern spot outside the written areas still holds the pattern
    (only the planned ranges were written) and every spot inside them was overwritten; the tail zero range
    [N-2081, N-33) is zero."""
    man = man or load_manifest()
    size = os.path.getsize(raw)
    N = size // C.SECTOR
    W = written_areas(N, man, grow)
    spots = mkdisks.t1d_spots(size, man)
    kept = over = partial = 0
    bad_kept, bad_over = [], []
    ud = [p for p in man["whole_disk"]["partitions"] if p["growable"]][0]
    ud_f = ud["first_lba"] * C.SECTOR
    ud_end = ((((N - 33 - 2048) // 2048) * 2048) if grow else (ud["last_lba"] + 1)) * C.SECTOR
    T = man["whole_disk"]["total_lba"]
    in_ud_middle = beyond_image = 0
    with open(raw, "rb") as f:
        for o in spots:
            e = o + mkdisks.SPOT
            inside = any(a <= o and e <= b for a, b in W)
            touch = any(o < b and a < e for a, b in W)
            f.seek(o)
            got = f.read(mkdisks.SPOT)
            if inside:
                over += 1
                if got == mkdisks.pattern_block(o):
                    bad_over.append(o)
            elif not touch:
                kept += 1
                if got != mkdisks.pattern_block(o):
                    bad_kept.append(o)
                if ud_f + C.HEAD_TAIL <= o and e <= ud_end - C.HEAD_TAIL:
                    in_ud_middle += 1
                if not grow and o >= (T - 33) * C.SECTOR:
                    beyond_image += 1
            else:
                partial += 1
        res.check(kept > 0 and not bad_kept, "%d pattern spots outside the written areas still hold the pattern "
                  "(userdata middle: %d, beyond the image end: %d; changed: %s)" % (kept, in_ud_middle, beyond_image,
                                                                                 bad_kept[:4]))
        res.check(in_ud_middle > 100, "the untouched middle of userdata was sampled (%d spots)" % in_ud_middle)
        if not grow:
            res.check(beyond_image > 0, "the space between the image end and the zeroed tail was sampled (%d "
                      "spots)" % beyond_image)
        res.check(over > 0 and not bad_over, "%d pattern spots inside the written areas were overwritten (still "
                  "pattern: %s)" % (over, bad_over[:4]))
        res.info("%d spots straddle a written-area boundary (not counted)" % partial)
        res.check(zero_range(f, (N - 2081) * C.SECTOR, (2081 - 33) * C.SECTOR), "tail LBA %d..%d zero (4.1.6)"
                  % (N - 2081, N - 34))
    res.evidence["t1d_spots"] = {"total": len(spots), "kept": kept, "overwritten": over, "partial": partial}


def main(argv):
    if len(argv) != 2 or argv[0] not in ("exact", "grow", "nogrow"):
        print(__doc__)
        return 2
    res = C.Result("validate-" + argv[0])
    if argv[0] == "exact":
        check_exact(argv[1], res)
    else:
        check_grown(argv[1], res, grow=argv[0] == "grow")
    res.finish()
    for ok, t in res.checks:
        print(("PASS " if ok else "FAIL " if ok is False else "INFO ") + t)
    print("RESULT:", res.status, res.reason)
    return 0 if res.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
