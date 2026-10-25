#!/usr/bin/env python3
"""UI-01 / UI-02 static checks of GbGpu.apk (run with python3 -I on the builder).

  python3 -I check_apk.py <GbGpu.apk> <build-tools dir> <android.jar> <BUILD_INFO.txt> [<source manifest> <lint.xml>]

UI-01: aapt2 dump badging (package, versionCode = epoch minutes of the build, versionName, min/target SDK 36),
       apksigner verify (v2 + v3, no v1; installs with min SDK 36), zipalign -c, resources.arsc stored and aligned,
       classes.dex present, no native code, not debuggable / testOnly.
UI-02 / UI-1B-10 (INTERFACES 13.2 v1.2): the manifest (aapt2 dump xmltree of the built APK) has exactly the 13.2
       components with the right exported flags, guard permissions, intent actions and meta-data (Settings entry
       "Graphics" in category ia.system with keyhint graphics and summary_uri, no switch_uri; tile label "Graphics";
       the non-exported ActionReceiver); every requested permission (POST_NOTIFICATIONS, QUERY_ALL_PACKAGES) is
       normal or dangerous and not privileged, and every guard permission is signature level, both judged by the
       platform's own framework manifest inside android.jar; the QUERY_ALL_PACKAGES justification is written down
       (source manifest comment, lint suppression); classes.dex carries the v1.2 command and intent strings.
Prints one line per check; exit 1 if any check fails.
"""
import os
import re
import struct
import subprocess
import sys
import zipfile

CHECKS = [0]
FAILS = []
NS = "http://schemas.android.com/apk/res/android:"


def check(ok, what):
    CHECKS[0] += 1
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)
    return ok


def run(*cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return p.returncode, p.stdout.decode("utf-8", "replace")


class El:
    def __init__(self, tag, depth):
        self.tag, self.depth, self.attrs, self.kids = tag, depth, {}, []

    def find(self, tag):
        return [k for k in self.kids if k.tag == tag]

    def walk(self, tag):
        out = [self] if self.tag == tag else []
        for k in self.kids:
            out += k.walk(tag)
        return out


def parse_xmltree(text):
    """Parses `aapt2 dump xmltree` output into an element tree (indentation gives the nesting)."""
    root = El("#root", -1)
    stack = [root]
    for line in text.splitlines():
        m = re.match(r"^( *)(E|A): (.*)$", line)
        if not m:
            continue
        depth, kind, rest = len(m.group(1)), m.group(2), m.group(3)
        if kind == "E":
            tag = rest.split(" ")[0]
            while stack[-1].depth >= depth:
                stack.pop()
            e = El(tag, depth)
            stack[-1].kids.append(e)
            stack.append(e)
        else:
            am = re.match(r'^(.*?)(\(0x[0-9a-f]+\))?=(.*)$', rest)
            if not am:
                continue
            name = am.group(1)
            if name.startswith(NS):
                name = "android:" + name[len(NS):]
            val = am.group(3)
            sm = re.match(r'^"(.*)" \(Raw: ', val)
            if sm:
                val = sm.group(1)
            stack[-1].attrs[name] = val
    return root


def resources(apk, aapt2):
    """id -> value (string or file) from `aapt2 dump resources`."""
    _, out = run(aapt2, "dump", "resources", apk)
    res, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^\s*resource (0x[0-9a-f]{8}) (\S+)", line)
        if m:
            cur = m.group(1)
            res[cur] = {"name": m.group(2)}
            continue
        m = re.match(r'^\s*\(\) "(.*)"$', line)
        if m and cur:
            res[cur]["value"] = m.group(1)
            continue
        m = re.match(r"^\s*\(\) \(file\) (\S+)", line)
        if m and cur:
            res[cur]["value"] = m.group(1)
    return res


def protection_levels(jar, aapt2):
    """permission name -> protectionLevel bitmask, from the framework manifest in android.jar."""
    _, out = run(aapt2, "dump", "xmltree", "--file", "AndroidManifest.xml", jar)
    t = parse_xmltree(out)
    lv = {}
    for p in t.walk("permission"):
        n, v = p.attrs.get("android:name"), p.attrs.get("android:protectionLevel")
        if n and v:
            lv[n] = int(v, 16) if v.startswith("0x") else int(v)
    return lv


def data_offset(path, info):
    with open(path, "rb") as f:
        f.seek(info.header_offset)
        sig, _, _, _, _, _, _, _, _, nlen, xlen = struct.unpack("<IHHHHHIIIHH", f.read(30))
        assert sig == 0x04034B50
        return info.header_offset + 30 + nlen + xlen


def actions(el):
    return sorted(a.attrs.get("android:name", "") for f in el.find("intent-filter") for a in f.find("action"))


def meta(el):
    out = {}
    for m in el.find("meta-data"):
        out[m.attrs.get("android:name", "")] = m.attrs.get("android:value", m.attrs.get("android:resource"))
    return out


def main():
    if len(sys.argv) not in (5, 7):
        print(__doc__)
        return 2
    apk, bt, jar, info_path = sys.argv[1:5]
    src_manifest, lint_xml = (sys.argv[5], sys.argv[6]) if len(sys.argv) == 7 else (None, None)
    aapt2, apksigner, zipalign = (os.path.join(bt, x) for x in ("aapt2", "apksigner", "zipalign"))
    info = dict(l.split("=", 1) for l in open(info_path, encoding="ascii").read().splitlines() if "=" in l)

    # ---------------- UI-01
    print("== UI-01 build and signing")
    rc, badging = run(aapt2, "dump", "badging", apk)
    check(rc == 0, "aapt2 dump badging runs")
    m = re.search(r"package: name='([^']*)' versionCode='(\d+)' versionName='([^']*)'", badging)
    check(m is not None and m.group(1) == "gb.sb3.gpu", "package gb.sb3.gpu")
    vc = int(m.group(2)) if m else -1
    check(str(vc) == info.get("versionCode"), "versionCode %d recorded in BUILD_INFO" % vc)
    bt_min = None
    bm = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)Z", info.get("built_utc", ""))
    if bm:
        import calendar
        bt_min = calendar.timegm(tuple(int(x) for x in bm.groups()) + (0, 0, 0)) // 60
    check(bt_min is not None and 0 <= bt_min - vc <= 2, "versionCode = epoch minutes at build time (%s vs %s)"
          % (vc, bt_min))
    check(m is not None and m.group(3) == "1.0.%d" % vc, "versionName 1.0.<versionCode>")
    check("minSdkVersion:'36'" in badging, "minSdkVersion 36")
    check("targetSdkVersion:'36'" in badging, "targetSdkVersion 36")
    check("application-debuggable" not in badging, "not debuggable")
    check("testOnly" not in badging, "not testOnly")
    rc, ver24 = run(apksigner, "verify", "-v", "--min-sdk-version", "24", apk)
    check(rc == 0 and "Verifies" in ver24, "apksigner verify")
    check("Verified using v1 scheme (JAR signing): false" in ver24, "no v1 signature")
    check("Verified using v2 scheme (APK Signature Scheme v2): true" in ver24, "v2 signature")
    check("Verified using v3 scheme (APK Signature Scheme v3): true" in ver24, "v3 signature")
    rc, ver36 = run(apksigner, "verify", "--min-sdk-version", "36", apk)
    check(rc == 0, "verifies for SDK 36 (the device)")
    rc, certs = run(apksigner, "verify", "--print-certs", apk)
    cm = re.search(r"certificate SHA-256 digest: ([0-9a-f]{64})", certs)
    check(cm is not None and cm.group(1) == info.get("signer_cert_sha256"), "signer certificate recorded")
    check("Signer #2" not in certs, "exactly one signer")
    rc, _ = run(zipalign, "-c", "-p", "4", apk)
    check(rc == 0, "zipalign -c -p 4")
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
        arsc = z.getinfo("resources.arsc")
        check(arsc.compress_type == zipfile.ZIP_STORED and data_offset(apk, arsc) % 4 == 0,
              "resources.arsc stored and 4-byte aligned (targetSdk >= 30 rule)")
        check("classes.dex" in names, "classes.dex present")
        check(not any(n.startswith("lib/") or n.endswith(".so") for n in names), "no native code")
        check(not any(n.startswith("META-INF/") and n.endswith((".SF", ".RSA", ".MF")) for n in names),
              "no JAR-signature files")
        dex = z.read("classes.dex")
        check(dex[:4] == b"dex\n", "dex magic")
        check("assets/allowlist.txt" not in names, "no assets/allowlist.txt in GbGpu either")
    for needle in (b"get-mode", b"set-mode", b"set-app-policy", b"set-app-intel", b"set-app-nvidia", b"get-apps",
                   b"apps-begin", b"apps-add ", b"apps-commit", b"run-selftest", b"list-holders",
                   b"close-holders-and-detach", b"ui-caps", b"gb.sb3.gpu.action.CANCEL_DETACH", b"gb.sb3.gpu.extra.MODE",
                   b"gb_gpu_detach"):
        check(needle in dex, "classes.dex has the string %r" % needle.decode())
    for gone in (b"checked_state", b"set_checked_error", b"nvidia_gpu", b"gb.sb3.gpu.extra.THEN_DISABLE"):
        check(gone not in dex, "classes.dex no longer has %r (v1.1 Settings switch / dialog)" % gone.decode())

    # ---------------- UI-02
    print("== UI-02 manifest")
    rc, xt = run(aapt2, "dump", "xmltree", "--file", "AndroidManifest.xml", apk)
    tree = parse_xmltree(xt)
    man = tree.find("manifest")[0]
    app = man.find("application")[0]
    res = resources(apk, aapt2)

    def label(el):
        v = el.attrs.get("android:label", "")
        return res.get(v[1:], {}).get("value", v) if v.startswith("@0x") else v

    check(app.attrs.get("android:allowBackup") == "false", "allowBackup false")
    check(app.attrs.get("android:debuggable", "false") == "false", "no android:debuggable")
    for a in ("android:persistent", "android:sharedUserId", "android:process"):
        check(a not in app.attrs and a not in man.attrs, "no %s" % a)
    check(label(app) == "NVIDIA GPU", "application label 'NVIDIA GPU' (13.1: descriptive; v1.2 does not change it)")

    comps = {}
    for kind in ("activity", "activity-alias", "service", "receiver", "provider"):
        for e in app.find(kind):
            n = e.attrs.get("android:name", "")
            comps[(kind, n[len("gb.sb3.gpu"):] if n.startswith("gb.sb3.gpu.") else n)] = e
    want = {("service", ".GpuTileService"), ("service", ".DetachTileService"), ("activity", ".SettingsActivity"),
            ("activity", ".HoldersActivity"), ("provider", ".SwitchProvider"), ("receiver", ".StateReceiver"),
            ("receiver", ".ActionReceiver")}
    check(set(comps) == want, "exactly the 13.2 (v1.2) components: %s" % sorted(n for _, n in comps))

    def comp(kind, name):
        return comps.get((kind, name), El("missing", 0))

    t = comp("service", ".GpuTileService")
    check(t.attrs.get("android:exported") == "true", "GpuTileService exported")
    check(t.attrs.get("android:permission") == "android.permission.BIND_QUICK_SETTINGS_TILE",
          "GpuTileService requires BIND_QUICK_SETTINGS_TILE")
    check(actions(t) == ["android.service.quicksettings.action.QS_TILE"], "GpuTileService QS_TILE action")
    md = meta(t)
    check(md.get("android.service.quicksettings.TOGGLEABLE_TILE") == "true", "TOGGLEABLE_TILE=true")
    check(md.get("android.service.quicksettings.ACTIVE_TILE") == "true", "ACTIVE_TILE=true")
    check(label(t) == "Graphics", "tile label 'Graphics' (13.2 v1.2; same component name as v1.1)")
    check("android:icon" in t.attrs, "GPU tile icon")

    t = comp("service", ".DetachTileService")
    check(t.attrs.get("android:exported") == "true", "DetachTileService exported")
    check(t.attrs.get("android:permission") == "android.permission.BIND_QUICK_SETTINGS_TILE",
          "DetachTileService requires BIND_QUICK_SETTINGS_TILE")
    check(actions(t) == ["android.service.quicksettings.action.QS_TILE"], "DetachTileService QS_TILE action")
    check(label(t) == "Detach screen", "Detach tile label 'Detach screen'")

    a = comp("activity", ".SettingsActivity")
    check(a.attrs.get("android:exported") == "true", "SettingsActivity exported (Settings starts it)")
    check("com.android.settings.action.IA_SETTINGS" in actions(a), "SettingsActivity IA_SETTINGS action")
    md = meta(a)
    check(md.get("com.android.settings.category") == "com.android.settings.category.ia.system",
          "category com.android.settings.category.ia.system")
    check(md.get("com.android.settings.title") == "Graphics", "Settings title 'Graphics'")
    check(label(a) == "Graphics", "SettingsActivity label 'Graphics'")
    check("com.android.settings.switch_uri" not in md, "no switch_uri (v1.2: three modes do not fit a switch)")
    su, kh = md.get("com.android.settings.summary_uri", ""), md.get("com.android.settings.keyhint", "")
    sm = re.match(r"^content://([^/]+)(/.*)?$", su)
    # TileUtils.getCompleteUri: a URI without a path gets /<method>/<keyhint> appended (deviation D-UI-1)
    check(sm is not None and sm.group(1) == "gb.sb3.gpu.switches" and sm.group(2) is None,
          "summary_uri content://gb.sb3.gpu.switches without a path")
    check(kh == "graphics", "keyhint graphics -> content://gb.sb3.gpu.switches/getDynamicSummary/graphics")
    check("com.android.settings.icon" in md, "injected entry icon")
    check(actions(a) == ["android.service.quicksettings.action.QS_TILE_PREFERENCES",
                         "com.android.settings.action.IA_SETTINGS"],
          "SettingsActivity: IA_SETTINGS + QS_TILE_PREFERENCES (tile long-press), nothing else")
    launcher = [c for f in app.walk("intent-filter") for c in f.find("category")
                if c.attrs.get("android:name") == "android.intent.category.LAUNCHER"]
    check(not launcher, "no launcher icon (the ia.system category exists on lapis, 13.2 v1.2)")

    a = comp("activity", ".HoldersActivity")
    check(a.attrs.get("android:exported") == "false", "HoldersActivity not exported")
    check(not a.find("intent-filter"), "HoldersActivity has no intent filter")

    p = comp("provider", ".SwitchProvider")
    check(p.attrs.get("android:authorities") == "gb.sb3.gpu.switches", "SwitchProvider authority")
    check(p.attrs.get("android:exported") == "true", "SwitchProvider exported")
    check(p.attrs.get("android:permission") == "android.permission.WRITE_SECURE_SETTINGS",
          "SwitchProvider requires WRITE_SECURE_SETTINGS")
    check("android:grantUriPermissions" not in p.attrs, "SwitchProvider grants no URI permissions")

    r = comp("receiver", ".ActionReceiver")
    check(r.attrs.get("android:exported") == "false", "ActionReceiver not exported (the Cancel action, 12.3)")
    check(not r.find("intent-filter"), "ActionReceiver has no intent filter")
    check("android:permission" not in r.attrs, "ActionReceiver needs no guard (not exported)")

    r = comp("receiver", ".StateReceiver")
    check(r.attrs.get("android:exported") == "true", "StateReceiver exported")
    check(r.attrs.get("android:permission") == "android.permission.DUMP", "StateReceiver requires DUMP")
    check(actions(r) == ["gb.sb3.gpu.action.STATE_CHANGED"], "StateReceiver action gb.sb3.gpu.action.STATE_CHANGED")

    open_exported = [n for (k, n), e in comps.items()
                     if e.attrs.get("android:exported") == "true" and "android:permission" not in e.attrs]
    check(open_exported == [".SettingsActivity"], "only SettingsActivity is exported without a permission (%s)"
          % open_exported)

    print("== UI-02 permissions against the platform's framework manifest")
    levels = protection_levels(jar, aapt2)
    check(len(levels) > 500, "framework permissions read from android.jar (%d)" % len(levels))
    used = sorted(e.attrs.get("android:name", "") for e in man.find("uses-permission")
                  + man.find("uses-permission-sdk-23"))
    check(used == ["android.permission.POST_NOTIFICATIONS", "android.permission.QUERY_ALL_PACKAGES"],
          "uses-permission is exactly POST_NOTIFICATIONS + QUERY_ALL_PACKAGES (%s)" % used)
    lq = levels.get("android.permission.QUERY_ALL_PACKAGES")
    check(lq is not None and lq & 0xF == 0, "QUERY_ALL_PACKAGES is protection level normal (granted at install): %s"
          % (hex(lq) if lq is not None else lq))
    for u in used:
        lv = levels.get(u)
        base, priv = (lv & 0xF, lv & 0x10) if lv is not None else (None, None)
        check(lv is not None and base in (0, 1) and not priv,
              "%s is normal/dangerous and not privileged (protectionLevel %s)" % (u, hex(lv) if lv else lv))
    check(not man.find("permission"), "declares no permissions of its own")
    for g in ("android.permission.BIND_QUICK_SETTINGS_TILE", "android.permission.WRITE_SECURE_SETTINGS",
              "android.permission.DUMP"):
        lv = levels.get(g)
        check(lv is not None and lv & 0xF == 2, "guard %s is signature level (%s)" % (g, hex(lv) if lv else lv))

    if src_manifest:
        print("== UI-1B-10 QUERY_ALL_PACKAGES justification")
        src = open(src_manifest, encoding="utf-8").read()
        m = re.search(r"QUERY_ALL_PACKAGES\s+\(normal.*?Justification:(.*?)-->", src, re.S)
        check(m is not None and "24.3" in m.group(1) and "12.3" in m.group(1),
              "source manifest documents why QUERY_ALL_PACKAGES is needed (catalog 24.3, holder names 12.3/27.4)")
        lx = open(lint_xml, encoding="utf-8").read()
        lm = re.search(r"<!--([^>]*)-->\s*<issue id=\"QueryAllPackagesPermission\" severity=\"ignore\" />", lx)
        check(lm is not None and "24.3" in lm.group(1), "lint.xml suppresses QueryAllPackagesPermission with a reason")
    print("\n%d checks, %d failures" % (CHECKS[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
