"""k14 acceptance checks (builder; run with python3 -I). Self-contained: no imports outside the standard library.

  k14check.py sig <dist> [ko ...]
      K-05. Extracts the module-signing certificate built into <dist>/vmlinux (system_certificate_list,
      module_cert_size), proves that the same DER bytes are inside the decompressed <dist>/bzImage, then checks
      every module (default: all <dist>/*.ko): appended PKCS#7 signature present, signer issuer+serial (or SKID)
      equals the certificate, and the RSA signature verifies (openssl dgst -verify) over the module payload.
  k14check.py unresolved <dist> <module> [...]
      K-04. Undefined symbols of each module and of its modinfo 'depends' closure must be exported by vmlinux or by
      a module of that closure (dist kernel_x86_64_Module.symvers). Same classification idea as
      native/nouveau-kernel/build/compile_test.sh, against the real in-tree build.
  k14check.py crc <symvers_dir> <ko_dir_or_file> [...]
      K-06. Modversion CRCs (__versions and __version_ext_crcs/__version_ext_names) of foreign (lapis) modules
      against <symvers_dir>/vmlinux.symvers and kernel_x86_64_Module.symvers, refresh_modules.sh style; symbols
      exported by another scanned module count as provided.
Exit status 0 = no problems.
"""
import os, struct, subprocess, sys, tempfile

MAGIC = b"~Module signature appended~\n"


# ---------------------------------------------------------------- ELF
class Elf:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if d[:4] != b"\x7fELF" or d[4] != 2 or d[5] != 1:
            raise ValueError("%s: not a little-endian ELF64 file" % path)
        shoff, = struct.unpack_from("<Q", d, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 0x3A)
        self.sections = []
        for i in range(shnum):
            name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack_from(
                "<IIQQQQIIQQ", d, shoff + i * shentsize)
            self.sections.append(dict(name_off=name, type=typ, addr=addr, off=off, size=size, link=link))
        strtab = self.sections[shstrndx]
        for s in self.sections:
            s["name"] = self._cstr(strtab["off"] + s["name_off"])

    def _cstr(self, off):
        end = self.data.index(b"\0", off)
        return self.data[off:end].decode("latin-1")

    def section(self, name):
        for s in self.sections:
            if s["name"] == name:
                return self.data[s["off"]:s["off"] + s["size"]]
        return None

    def symbols(self):
        """yield (name, value, shndx, bind)"""
        for s in self.sections:
            if s["type"] != 2:          # SHT_SYMTAB
                continue
            strtab = self.sections[s["link"]]
            for i in range(s["size"] // 24):
                name, info, other, shndx, value, size = struct.unpack_from("<IBBHQQ", self.data, s["off"] + i * 24)
                if name == 0:
                    continue
                yield self._cstr(strtab["off"] + name), value, shndx, info >> 4

    def read_vaddr(self, addr, n):
        for s in self.sections:
            if s["addr"] and s["addr"] <= addr < s["addr"] + s["size"] and s["type"] != 8:   # not NOBITS
                o = s["off"] + addr - s["addr"]
                return self.data[o:o + n]
        raise KeyError("address %#x not in a file-backed section" % addr)


def undefined(elf):
    return sorted({n for n, v, shndx, bind in elf.symbols() if shndx == 0 and bind in (1, 2)})


# ---------------------------------------------------------------- DER (just enough for PKCS#7 SignedData)
def tlv(b, o):
    tag = b[o]
    ln = b[o + 1]
    h = 2
    if ln & 0x80:
        k = ln & 0x7F
        ln = int.from_bytes(b[o + 2:o + 2 + k], "big")
        h = 2 + k
    return tag, o + h, ln, o + h + ln       # tag, value start, value length, end


def children(b, start, end):
    out, o = [], start
    while o < end:
        t, vs, vl, e = tlv(b, o)
        out.append((t, o, vs, vl, e))
        o = e
    return out


OIDS = {bytes.fromhex("2b0e03021a"): "sha1", bytes.fromhex("608648016503040201"): "sha256",
        bytes.fromhex("608648016503040202"): "sha384", bytes.fromhex("608648016503040203"): "sha512"}


def parse_pkcs7(p7):
    """-> dict(issuer=DER, serial=int|None, skid=bytes|None, digest=str, signature=bytes)"""
    t, vs, vl, e = tlv(p7, 0)                       # ContentInfo
    ci = children(p7, vs, e)
    _, _, ivs, ivl, ie = ci[1]                      # [0] EXPLICIT
    t, svs, svl, se = tlv(p7, ivs)                  # SignedData SEQUENCE
    sd = children(p7, svs, se)
    signer_infos = sd[-1]                           # last element: signerInfos SET
    if signer_infos[0] != 0x31:
        raise ValueError("signerInfos SET not found")
    si = children(p7, signer_infos[2], signer_infos[4])
    if len(si) != 1:
        raise ValueError("expected exactly one SignerInfo, got %d" % len(si))
    f = children(p7, si[0][2], si[0][4])
    r = dict(issuer=None, serial=None, skid=None)
    sid = f[1]
    if sid[0] == 0x30:                              # issuerAndSerialNumber
        ias = children(p7, sid[2], sid[4])
        r["issuer"] = p7[ias[0][1]:ias[0][4]]
        r["serial"] = int.from_bytes(p7[ias[1][2]:ias[1][4]], "big")
    else:                                           # [0] subjectKeyIdentifier
        r["skid"] = p7[sid[2]:sid[4]]
    alg = children(p7, f[2][2], f[2][4])            # digestAlgorithm
    r["digest"] = OIDS.get(p7[alg[0][2]:alg[0][4]], "unknown")
    if any(x[0] == 0xA0 for x in f[3:-2]):
        raise ValueError("signed attributes present (not a sign-file signature)")
    if f[-1][0] != 0x04:
        raise ValueError("encryptedDigest not found")
    r["signature"] = p7[f[-1][2]:f[-1][4]]
    return r


def module_signature(blob):
    """-> (payload, pkcs7 DER) or None"""
    if not blob.endswith(MAGIC):
        return None
    info = blob[-len(MAGIC) - 12:-len(MAGIC)]
    algo, hsh, id_type, signer_len, key_id_len = info[:5]
    sig_len, = struct.unpack(">I", info[8:12])
    if id_type != 2:
        raise ValueError("signature id_type %d is not PKCS#7" % id_type)
    end = len(blob) - len(MAGIC) - 12
    return blob[:end - sig_len], blob[end - sig_len:end]


def cert_fields(der, tmp):
    p = os.path.join(tmp, "cert.der")
    open(p, "wb").write(der)
    out = subprocess.run(["openssl", "x509", "-inform", "DER", "-in", p, "-noout", "-serial", "-subject",
                          "-fingerprint", "-sha256", "-ext", "subjectKeyIdentifier"],
                         capture_output=True, text=True, check=True).stdout
    serial = int(out.split("serial=")[1].split()[0], 16)
    skid = None
    lines = out.splitlines()
    for i, l in enumerate(lines):
        if "Subject Key Identifier" in l and i + 1 < len(lines):
            skid = bytes.fromhex(lines[i + 1].strip().replace(":", ""))
    subprocess.run(["openssl", "x509", "-inform", "DER", "-in", p, "-pubkey", "-noout", "-out",
                    os.path.join(tmp, "pub.pem")], check=True)
    # issuer Name DER: tbsCertificate fields [version], serial, sigalg, issuer
    t, vs, vl, e = tlv(der, 0)
    tbs = children(der, vs, e)[0]
    tf = children(der, tbs[2], tbs[4])
    i = 1 if tf[0][0] == 0xA0 else 0
    issuer = der[tf[i + 2][1]:tf[i + 2][4]]
    return dict(serial=serial, skid=skid, issuer=issuer, text=out.strip())


def builtin_cert(vmlinux):
    syms = {n: v for n, v, shndx, bind in vmlinux.symbols()
            if n in ("system_certificate_list", "module_cert_size", "system_certificate_list_size")}
    for k in ("system_certificate_list", "module_cert_size"):
        if k not in syms:
            raise KeyError("vmlinux has no symbol %s" % k)
    size, = struct.unpack("<Q", vmlinux.read_vaddr(syms["module_cert_size"], 8))
    return vmlinux.read_vaddr(syms["system_certificate_list"], size)


def cmd_sig(dist, kos):
    tmp = tempfile.mkdtemp(prefix="k14sig.", dir="/build/k14")
    try:
        vml = Elf(os.path.join(dist, "vmlinux"))
        der = builtin_cert(vml)
        c = cert_fields(der, tmp)
        print("built-in module signing certificate (%d bytes DER):" % len(der))
        print("  " + c["text"].replace("\n", "\n  "))
        ex = os.path.join(tmp, "vmlinux.bin")
        with open(ex, "wb") as f:
            subprocess.run(["sh", "/build/gki/common/scripts/extract-vmlinux", os.path.join(dist, "bzImage")],
                           stdout=f, check=True)
        inbz = open(ex, "rb").read().find(der) >= 0
        print("certificate DER found inside decompressed bzImage: %s" % ("yes" if inbz else "NO"))
        bad = 0 if inbz else 1
        if not kos:
            kos = sorted(os.path.join(dist, n) for n in os.listdir(dist) if n.endswith(".ko"))
        ok = 0
        for ko in kos:
            blob = open(ko, "rb").read()
            name = os.path.basename(ko)
            try:
                ms = module_signature(blob)
                if ms is None:
                    print("UNSIGNED", name); bad += 1; continue
                payload, p7 = ms
                s = parse_pkcs7(p7)
                if s["skid"] is not None:
                    match = s["skid"] == c["skid"]
                else:
                    match = s["serial"] == c["serial"] and s["issuer"] == c["issuer"]
                if not match:
                    print("WRONG SIGNER", name, s.get("serial"), s.get("skid")); bad += 1; continue
                pp, sp = os.path.join(tmp, "payload"), os.path.join(tmp, "sig")
                open(pp, "wb").write(payload); open(sp, "wb").write(s["signature"])
                v = subprocess.run(["openssl", "dgst", "-" + s["digest"], "-verify", os.path.join(tmp, "pub.pem"),
                                    "-signature", sp, pp], capture_output=True, text=True)
                if v.returncode != 0 or "Verified OK" not in v.stdout:
                    print("BAD SIGNATURE", name, v.stdout.strip(), v.stderr.strip()[:200]); bad += 1; continue
                ok += 1
            except Exception as e:      # report and continue with the next module
                print("ERROR", name, e); bad += 1
        print("modules: %d checked, %d signed by the built-in key with a valid signature, %d problems"
              % (len(kos), ok, bad - (0 if inbz else 1)))
        return bad
    finally:
        subprocess.run(["rm", "-rf", tmp])


# ---------------------------------------------------------------- symvers / unresolved / crc
def load_symvers(path):
    d = {}
    for l in open(path):
        f = l.rstrip("\n").split("\t")
        if len(f) >= 3:
            d[f[1]] = (int(f[0], 16), f[2])
    return d


def modinfo_depends(ko):
    out = subprocess.run(["/sbin/modinfo", "-F", "depends", ko], capture_output=True, text=True, check=True).stdout
    return [x for x in out.strip().split(",") if x]


def find_ko(dist, name):
    for n in (name, name.replace("_", "-"), name.replace("-", "_")):
        p = os.path.join(dist, n + ".ko")
        if os.path.exists(p):
            return p
    return None


def cmd_unresolved(dist, mods):
    allsyms = load_symvers(os.path.join(dist, "kernel_x86_64_Module.symvers"))
    vml = load_symvers(os.path.join(dist, "vmlinux.symvers"))
    closure, todo, problems = {}, list(mods), 0
    while todo:
        m = todo.pop()
        key = m.replace("-", "_")
        if key in closure:
            continue
        p = find_ko(dist, m)
        if p is None:
            print("MISSING module", m); problems += 1; closure[key] = None; continue
        closure[key] = p
        todo += modinfo_depends(p)
    names = {k for k, v in closure.items() if v}
    print("closure (%d): %s" % (len(names), " ".join(sorted(names))))
    total = 0
    for key in sorted(names):
        u = undefined(Elf(closure[key]))
        bad = []
        for s in u:
            if s in vml:
                continue
            owner = allsyms.get(s, (None, None))[1]       # Module.symvers names the module by its build path
            if owner and os.path.basename(owner).replace("-", "_") in names:
                continue
            bad.append("%s(%s)" % (s, "exported by %s, not a dependency" % owner if owner else "not exported"))
        total += len(u)
        print("%-16s %4d undefined, %d unresolved%s" % (key, len(u), len(bad), (": " + " ".join(bad)) if bad else ""))
        problems += len(bad)
    print("unresolved total: %d (of %d undefined references)" % (problems, total))
    return problems


def versions(elf):
    """-> {symbol: crc} from __versions and __version_ext_*"""
    out, clash = {}, []
    v = elf.section("__versions")
    if v:
        for i in range(0, len(v), 64):
            crc = struct.unpack_from("<Q", v, i)[0] & 0xFFFFFFFF
            out[v[i + 8:i + 64].split(b"\0")[0].decode()] = crc
    crcs, names = elf.section("__version_ext_crcs"), elf.section("__version_ext_names")
    if crcs and names:
        nl = names.split(b"\0")
        for i in range(len(crcs) // 4):
            n = nl[i].decode()
            c, = struct.unpack_from("<I", crcs, i * 4)
            if n in out and out[n] != c:
                clash.append(n)
            out[n] = c
    return out, clash


def cmd_crc(symdir, paths):
    vml = load_symvers(os.path.join(symdir, "vmlinux.symvers"))
    allsyms = load_symvers(os.path.join(symdir, "kernel_x86_64_Module.symvers"))
    kos = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                kos += [os.path.join(root, f) for f in sorted(files) if f.endswith(".ko")]
        else:
            kos.append(p)
    elfs = {k: Elf(k) for k in kos}
    provided = set()
    for e in elfs.values():
        for n, v, shndx, bind in e.symbols():
            if shndx != 0 and (n.startswith("__ksymtab_") or n.startswith("__crc_")):
                provided.add(n.split("_", 3)[-1] if n.startswith("__crc_") else n[len("__ksymtab_"):])
    hard = soft = unres = refs = 0
    for k in kos:
        req, clash = versions(elfs[k])
        refs += len(req)
        rel = os.path.relpath(k)
        for n in clash:
            print("%s BASIC/EXT CRC DISAGREE %s" % (rel, n)); hard += 1
        for n, c in sorted(req.items()):
            if n in vml:
                if vml[n][0] != c:
                    print("%s CRC MISMATCH vmlinux %s (module %#010x, kernel %#010x)" % (rel, n, c, vml[n][0])); hard += 1
            elif n in allsyms:
                if allsyms[n][0] != c:
                    print("%s crc differs from GKI module %s: %s" % (rel, allsyms[n][1], n)); soft += 1
            elif n not in provided:
                print("%s UNRESOLVED %s" % (rel, n)); unres += 1
    print("modules: %d, versioned references: %d, vmlinux CRC mismatches: %d, GKI-module CRC differences: %d, "
          "unresolved: %d" % (len(kos), refs, hard, soft, unres))
    return hard + unres


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "sig":
        rc = cmd_sig(a[0], a[1:])
    elif cmd == "unresolved":
        rc = cmd_unresolved(a[0], a[1:])
    elif cmd == "crc":
        rc = cmd_crc(a[0], a[1:])
    else:
        sys.exit(__doc__)
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
