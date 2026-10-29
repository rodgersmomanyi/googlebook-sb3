"""Read-only classification evidence: for every file the two /odm trees put into the /vendor overlay upper layer
(/odm/vup), every GBINIT whiteout and every gb.rc bind target, does the boot slot's stock partition (vendor_b,
system_b, system_ext_b of lapis.bin's super, LP metadata slot of the boot suffix) have that path, and with which
size/sha256 (sha256 only for uncompressed EROFS files)? lapis.bin is opened read-only; prints a TSV-ish report.
  python -I -B stock_scan.py BUILD_ESP_PY [--slot b]"""
import hashlib, importlib.util, io, os, re, sys, contextlib
sys.dont_write_bytecode = True
os.environ["GB_NO_BUILDER"] = "1"
SCR = os.environ.get("GB_SCRATCH") or os.getcwd()   # scratch folder (carved partitions)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


VP = load("vnni_prepare", r"C:\GB-x86\vnni_prepare.py")
EL = load("erofslib", os.path.join(SCR, "erofslib.py"))


def cpio_entries(buf):
    out, o = [], 0
    while True:
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode()
        o = (o + 110 + nsz + 3) & ~3
        data = buf[o:o + fsz]
        o = (o + fsz + 3) & ~3
        if name == "TRAILER!!!":
            return out
        out.append((name, mode, data))


def main():
    be_path = sys.argv[1]
    slot = sys.argv[sys.argv.index("--slot") + 1] if "--slot" in sys.argv else None
    with contextlib.redirect_stdout(io.StringIO()):
        be = load("build_esp", be_path)
        nat = cpio_entries(be.native_cpio())
    vm = be._VM_ENTRIES
    lapis = r"C:\GB-x86\lapis.bin"
    with open(lapis, "rb") as f:
        parts = VP.gpt(f); base = parts["super"][0]; md = VP.lp_metadata(f, base)
        bslot = VP.boot_slot(parts)[0]
    slot = slot or bslot
    k = "ab".index(slot)
    f = open(lapis, "rb")
    fs = {}
    for p in ("vendor", "system", "system_ext", "product"):
        attr, exts = md[k][f"{p}_{slot}"]
        assert len(exts) == 1 and exts[0][1] == 0, (p, exts)
        fs[p] = EL.Erofs(f, base + exts[0][2] * 512)
    def stat(part, path):
        e = fs[part]
        ino = e.resolve(path)
        if not ino:
            return None
        typ = {0x8000: "file", 0x4000: "dir", 0xA000: "link"}.get(ino["mode"] & 0xF000, hex(ino["mode"]))
        data = e.read(ino) if typ == "file" else None
        h = hashlib.sha256(data).hexdigest() if data is not None else ("compressed" if typ == "file" else "-")
        return typ, ino["size"], h
    def where(abs_path):      # /vendor/x, /system/x, /system_ext/x -> (part, inner)
        for pre, part, inner in (("/vendor/", "vendor", ""), ("/system_ext/", "system_ext", ""),
                                 ("/product/", "product", ""), ("/system/", "system", "system/")):
            if abs_path.startswith(pre):
                return part, inner + abs_path[len(pre):]
        return None, None
    fp = {}
    for p, path, prop in (("vendor", "build.prop", "ro.vendor.build.fingerprint"),
                          ("system", "system/build.prop", "ro.system.build.fingerprint"),
                          ("system_ext", "etc/build.prop", "ro.system_ext.build.fingerprint")):
        d = fs[p].cat(path)
        fp[prop] = VP.read_prop(d, prop) if d else None
    print(f"# slot {slot} (boot slot {bslot}); fingerprints: {fp}")
    print("# kind\ttree\todm path\tstock path\tstock (type size sha256 | ABSENT)\tours sha256")
    for tree, ents in (("gbstage", vm), ("gbnative", nat)):
        for path, mode, data in sorted(ents):
            if not path.startswith(tree + "/vup/") or data is None:
                continue
            rel = path[len(tree) + len("/vup/"):]
            st = stat("vendor", rel)
            kind = "symlink" if (mode & 0o170000) == 0o120000 else "file"
            ours = hashlib.sha256(data).hexdigest()
            print(f"vup-{kind}\t{tree}\t{path[len(tree)+1:]}\t/vendor/{rel}\t"
                  f"{' '.join(map(str, st)) if st else 'ABSENT'}\t{ours}")
    gi = dict((p, d) for p, m, d in vm)["gbinit"].decode()
    for m in re.finditer(r"/busybox mknod /odm/vup/(\S+) c 0 0", gi):
        st = stat("vendor", m.group(1))
        print(f"whiteout\tgbinit\tvup/{m.group(1)}\t/vendor/{m.group(1)}\t{' '.join(map(str, st)) if st else 'ABSENT'}\t-")
    for tree, ents in (("gbstage", vm), ("gbnative", nat)):
        rc = dict((p, d) for p, m, d in ents)[f"{tree}/etc/init/gb.rc"].decode()
        for m in re.finditer(r"^\s*mount none (\S+) (\S+) bind$", rc, re.M):
            src, dst = m.groups()
            part, inner = where(dst)
            st = stat(part, inner) if part else None
            print(f"bind\t{tree}\t{src}\t{dst}\t{(' '.join(map(str, st)) if st else 'ABSENT') if part else 'n/a (apex/other)'}\t-")
    # the stock files our odm copies were made from
    for part, inner in (("vendor", "etc/fstab.android-desktop"), ("system_ext", "etc/vintf/manifest.xml"),
                        ("vendor", "etc/selinux/precompiled_sepolicy"), ("vendor", "etc/selinux/vendor_sepolicy.cil"),
                        ("system", "system/etc/selinux/plat_sepolicy_and_mapping.sha256"),
                        ("system_ext", "etc/selinux/system_ext_sepolicy_and_mapping.sha256"),
                        ("product", "etc/selinux/product_sepolicy_and_mapping.sha256"),
                        ("vendor", "etc/selinux/precompiled_sepolicy.plat_sepolicy_and_mapping.sha256")):
        st = stat(part, inner)
        print(f"stock\t-\t-\t/{part}/{inner}\t{' '.join(map(str, st)) if st else 'ABSENT'}\t-")


if __name__ == "__main__":
    main()
