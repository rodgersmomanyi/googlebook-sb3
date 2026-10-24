"""Static checks for the IDC / KL / exclusion files: syntax, known keycode labels (from AOSP KeyEvent.java),
and that the file names match Android's lookup rules (libinput InputDevice.cpp: Vendor_%04x_Product_%04x, or the
device name with every char outside [A-Za-z0-9_-] replaced by '_')."""
import re, sys, pathlib, xml.etree.ElementTree as ET
root = pathlib.Path(sys.argv[1]); keyevent = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
labels = set(re.findall(r"public static final int KEYCODE_([A-Z0-9_]+)\s*=", keyevent))
canon = lambda n: "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "_" for c in n)
ok = True
def fail(m):
    global ok; ok = False; print("FAIL", m)
expect_names = {"Microsoft_Surface_045E_09AE_Keyboard.idc": "Microsoft Surface 045E:09AE Keyboard",
                "Microsoft_Surface_045E_09AF_Touchpad.idc": "Microsoft Surface 045E:09AF Touchpad",
                "Surface_Slim_Pen_2_Keyboard.kl": "Surface Slim Pen 2 Keyboard"}
for f in sorted((root / "usr").rglob("*")):
    if not f.is_file(): continue
    if f.name in expect_names and canon(expect_names[f.name]) + f.suffix != f.name:
        fail(f"{f.name}: canonical name mismatch")
    if not re.fullmatch(r"(Vendor_[0-9a-f]{4}_Product_[0-9a-f]{4}|[A-Za-z0-9_-]+)\.(idc|kl)", f.name):
        fail(f"{f.name}: bad file name")
    for n, line in enumerate(f.read_text(encoding="ascii").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"): continue
        if f.suffix == ".idc":
            m = re.fullmatch(r"([A-Za-z0-9_.]+)\s*=\s*(\S.*)", s)
            if not m: fail(f"{f.name}:{n}: not 'key = value': {s}")
        else:
            m = re.fullmatch(r"key\s+(\d+)\s+([A-Z0-9_]+)(\s+[A-Z_]+)*", s)
            if not m: fail(f"{f.name}:{n}: bad kl line: {s}")
            elif m.group(2) not in labels: fail(f"{f.name}:{n}: unknown keycode label {m.group(2)}")
    print("ok", f.relative_to(root))
x = ET.parse(root / "etc" / "excluded-input-devices.xml").getroot()
assert x.tag == "devices" and all(d.tag == "device" and d.get("name") for d in x), "exclusion xml"
print("ok etc/excluded-input-devices.xml:", [d.get("name") for d in x])
sys.exit(0 if ok else 1)
