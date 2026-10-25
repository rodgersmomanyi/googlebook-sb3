#!/usr/bin/env python3
"""GD-1B-12 (manifest part): gfxdriver.json and out/gfxdriver.json equal the JSON block of 1b\\TASKS_1B.md 3.3 byte for
byte (block content + one trailing LF), parse as JSON, and out/ ships no gb_gfxdriver.* (retired in v1.2, T3.9).

usage: python -I check_manifest.py <component dir> <TASKS_1B.md>
Exit 0 = PASS.
"""
import json
import os
import re
import sys


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    comp, tasks = sys.argv[1], sys.argv[2]
    with open(tasks, "rb") as f:
        t = f.read().decode("utf-8")
    m = re.search(r"### 3\.3 New `gfxdriver\.json`[^\n]*\n\n```json\n(.*?)\n```", t, re.S)
    if not m:
        print("FAIL cannot find the 3.3 JSON block in %s" % tasks)
        return 1
    want = (m.group(1) + "\n").encode("utf-8")
    bad = 0
    for rel in ("gfxdriver.json", os.path.join("out", "gfxdriver.json")):
        p = os.path.join(comp, rel)
        with open(p, "rb") as f:
            got = f.read()
        same = got == want
        print("  %s %s equals TASKS_1B 3.3 byte for byte (%d bytes)" % ("ok  " if same else "FAIL", rel, len(got)))
        bad += 0 if same else 1
        try:
            j = json.loads(got.decode("utf-8"))
            good = (j["build_prop"] == ["ro.gfx.driver.0=gb.sb3.gfxdriver"] and j["rc_files"] == [] and
                    j["rc_text"] == "" and [x["dest"] for x in j["files"]] == ["app/GbGfxDriver/GbGfxDriver.apk"])
        except (ValueError, KeyError, TypeError) as e:
            print("  FAIL %s: %s" % (rel, e))
            good = False
        print("  %s %s: APK only, no rc, build_prop ro.gfx.driver.0=gb.sb3.gfxdriver" % ("ok  " if good else "FAIL", rel))
        bad += 0 if good else 1
    left = sorted(n for n in os.listdir(os.path.join(comp, "out")) if n.startswith("gb_gfxdriver"))
    print("  %s out/ has no gb_gfxdriver.* %s" % ("ok  " if not left else "FAIL", left if left else ""))
    bad += 1 if left else 0
    print("RESULT: %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
