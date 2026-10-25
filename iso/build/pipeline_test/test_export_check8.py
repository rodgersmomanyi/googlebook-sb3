"""TB-5 (WAVE2_PLAN_v2 B-07): export cross-check 8 of build_iso.py, on the current USB image and export ESP.

  python -I -B C:\\GB-x86\\iso\\build\\pipeline_test\\test_export_check8.py [--usb IMG] [--export-esp ESP]

Read-only: build_iso.py and verify_image.py are imported by path (nothing runs their main()); the USB image and the
export EFI-SYSTEM.img are only read. Cases:
  P1 the current pair passes check 8 (only the entry differs: title, gb.boot=, androidboot.boot_part_uuid=)
  P2 an nvtest entry on both sides, differing the same way, still passes
  N1 one byte of vmlinuz changed on the USB side              -> "export cross-check 8 failed: ..."
  N2 a gb/dgpu-* file on one side only                         -> fails
  N3 an extra token in the export's nvtest entry               -> fails
  N4 a different initrd-<slot>.img / gb/inject.gz              -> fails
Prints PASS/FAIL lines and "TB-5 check8 RESULT: OK" or "... <n> failure(s)" (exit 0 / 1).
"""
import argparse
import hashlib
import importlib.util
import sys

BUILD_ISO = r"C:\GB-x86\iso\build\build_iso.py"
FAILS = []


def ok(cond, text):
    print(("PASS " if cond else "FAIL ") + text, flush=True)
    if not cond:
        FAILS.append(text)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def expect_fail(bi, a, b, label):
    try:
        bi.check_esp_tree(a, b)
    except bi.BuildError as e:
        msg = str(e)
        ok(msg.startswith("export cross-check 8 failed: the export ESP was not built from the same tree as the USB "
                          "image ("), f"{label}: fails with the 13.3 message ({msg[:150]})")
        return
    ok(False, f"{label}: check 8 did not fail")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usb", default=r"C:\GB-x86\out\googlebook-sb3-usb.img")
    ap.add_argument("--export-esp", default=r"C:\GB-x86\iso\work\export\EFI-SYSTEM.img")
    a = ap.parse_args()
    sys.dont_write_bytecode = True
    bi = load("gb_build_iso", BUILD_ISO)
    vi = bi.verify_image_module()
    usb = bi.read_usb_gpt(a.usb)
    # digest form, exactly as export_checks() builds it
    ua = bi.esp_digest_files(bi.usb_esp_bytes(usb))
    ea = bi.esp_digest_files(open(a.export_esp, "rb").read())
    slot = bi.read_usb_esp(usb)
    try:
        bi.check_esp_tree(ua, ea)
        ok(True, f"P1 the current pair passes check 8 ({len(ua)} files; entries: {sorted(k for k in ua if 'entries' in k)})")
    except bi.BuildError as e:
        ok(False, f"P1 the current pair passes check 8: {e}")
    e_name = f"loader/entries/gb-{slot}.conf"
    n_name = f"loader/entries/gb-{slot}-nvtest.conf"
    ua2, ea2 = dict(ua), dict(ea)
    ua2[n_name] = vi.entry_variant(ua[e_name].decode(), " [NVIDIA test]", " gb.nouveau=test").encode()
    ea2[n_name] = vi.entry_variant(ea[e_name].decode(), " [NVIDIA test]", " gb.nouveau=test").encode()
    try:
        bi.check_esp_tree(ua2, ea2)
        ok(True, "P2 nvtest entries on both sides that differ only in title/gb.boot=/boot_part_uuid= pass")
    except bi.BuildError as e:
        ok(False, f"P2 nvtest entries pass: {e}")
    # N1: one byte of vmlinuz (raw bytes from the USB ESP, digested the same way)
    raw = vi.fat_files(bi.usb_esp_bytes(usb))["vmlinuz"]
    flipped = raw[:0x5000] + bytes([raw[0x5000] ^ 0x01]) + raw[0x5001:]
    ok(hashlib.sha256(raw).digest() == ua["vmlinuz"], "the digest form matches the raw vmlinuz")
    expect_fail(bi, dict(ua, vmlinuz=hashlib.sha256(flipped).digest()), ea, "N1 one byte of vmlinuz changed")
    expect_fail(bi, dict(ua, **{"gb/dgpu-6.18.21-x.erofs": hashlib.sha256(b"x").digest()}), ea,
                "N2 a gb/dgpu-* file on the USB side only")
    ea3 = dict(ea2)
    ea3[n_name] = ea2[n_name].replace(b" gb.nouveau=test", b" gb.nouveau=test extra.token=1")
    expect_fail(bi, ua2, ea3, "N3 an extra token in the export's nvtest entry")
    expect_fail(bi, dict(ua, **{f"initrd-{slot}.img": hashlib.sha256(b"other").digest()}), ea,
                "N4 a different initrd")
    expect_fail(bi, dict(ua, **{"gb/inject.gz": hashlib.sha256(b"other").digest()}), ea, "N4 a different gb/inject.gz")
    print("TB-5 check8 RESULT: OK" if not FAILS else f"TB-5 check8 RESULT: {len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
