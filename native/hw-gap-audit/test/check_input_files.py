"""Static checks for the IDC / KL / exclusion files: syntax, known keycode labels (from AOSP KeyEvent.java),
and that the file names match Android's lookup rules (libinput InputDevice.cpp: Vendor_%04x_Product_%04x, or the
device name with every char outside [A-Za-z0-9_-] replaced by '_')."""
import re, sys, pathlib, xml.etree.ElementTree as ET
# usage: python -I check_input_files.py TREE [KeyEvent.java]   (without KeyEvent.java the .kl label check is skipped)
root = pathlib.Path(sys.argv[1])
keyevent = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8") if len(sys.argv) > 2 else None
labels = set(re.findall(r"public static final int KEYCODE_([A-Z0-9_]+)\s*=", keyevent)) if keyevent else None
if labels is None:
    print("SKIP .kl keycode labels: no KeyEvent.java given (the syntax checks still run)")
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
            elif labels is not None and m.group(2) not in labels: fail(f"{f.name}:{n}: unknown keycode label {m.group(2)}")
    print("ok", f.relative_to(root))
# Wave 3 item 9: the touchpad IDC's exact property set; gestureProp.* values are numbers (Android parses them as the
# gestures library's typed properties: Change_Timeout is a double, in seconds). Credit: gbos-x86 issue #17.
tp = root / "usr" / "idc" / "Microsoft_Surface_045E_09AF_Touchpad.idc"
props = {}
for line in tp.read_text(encoding="ascii").splitlines():
    s = line.strip()
    if s and not s.startswith("#"):
        k, v = (x.strip() for x in s.split("=", 1))
        if k in props: fail(f"{tp.name}: {k} set twice")
        props[k] = v
want = {"device.internal": "1", "touch.deviceType": "touchPad", "gestureProp.Change_Timeout": "0.02"}
if props != want: fail(f"{tp.name}: properties {props} != {want}")
for k, v in props.items():
    if k.startswith("gestureProp."):
        try:
            fv = float(v)
        except ValueError:
            fail(f"{tp.name}: {k} = {v!r} is not a number"); continue
        if not 0 <= fv <= 1: fail(f"{tp.name}: {k} = {fv} s is out of range")
if any("Drumroll" in k for k in props): fail(f"{tp.name}: Drumroll_Suppression_Enable must not be set (item 9)")
print("ok", tp.name, "properties:", ", ".join(f"{k}={v}" for k, v in sorted(props.items())))
x = ET.parse(root / "etc" / "excluded-input-devices.xml").getroot()
assert x.tag == "devices" and all(d.tag == "device" and d.get("name") for d in x), "exclusion xml"
print("ok etc/excluded-input-devices.xml:", [d.get("name") for d in x])
sys.exit(0 if ok else 1)
