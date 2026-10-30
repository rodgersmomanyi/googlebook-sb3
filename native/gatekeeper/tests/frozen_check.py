# SPDX-License-Identifier: Apache-2.0
"""Independent check of the frozen gb_gatekeeper vectors (safety audit SA-6).

    python3 -I frozen_check.py frozen_v1.json test_frozen_v1.cpp

Uses only hashlib (scrypt, sha256) and hmac; none of the C code under test. For every vector of frozen_v1.json:
  key_file   72 bytes: "GBGKKEY1" | K_dev[32] | SHA-256("GBGKKEY1" | K_dev)          (gk_store.h)
  handle     password_handle_t v2, 58 bytes, flags 1, not hardware backed, user_id == secure_user_id
  signature  == HMAC-SHA256(K_dev, scrypt(handle[0:17] | password, salt (8 B LE), N=16384, r=8, p=1, 32))
             (gb_gatekeeper.cpp ComputePasswordSignature) and != the signature of a wrong password
and the constants the C++ test (test_frozen_v1.cpp) verifies through the production code are exactly these bytes.
Prints "GK_FROZEN_REF: PASS (<n> vectors)"; exit 1 on any mismatch.
"""
import hashlib
import hmac
import json
import re
import struct
import sys


def sig(key, prefix17, pw, salt):
    st = hashlib.scrypt(prefix17 + pw, salt=struct.pack("<Q", salt), n=16384, r=8, p=1, dklen=32,
                        maxmem=64 * 1024 * 1024)
    return hmac.new(key, st, hashlib.sha256).digest()


def main(js, cpp):
    doc = json.load(open(js, encoding="utf-8"))
    assert doc["format"] == "gb-gatekeeper-frozen/1", doc["format"]
    src = open(cpp, encoding="utf-8").read()
    body = src[src.index("const Vector kVectors[] = {"):src.index("const int32_t kUid")]
    cvec = re.findall(r'\{"([^"]+)",\s*"([0-9a-f]+)",\s*"([0-9a-f]+)",\s*"([0-9a-f]+)",\s*"([0-9a-f]+)"\}', body)
    assert len(cvec) == len(doc["vectors"]) >= 1, (len(cvec), len(doc["vectors"]))
    assert re.search(r"const int32_t kUid = (\d+);", src).group(1) == str(doc["vectors"][0]["uid"])
    for v, c in zip(doc["vectors"], cvec):
        assert c == (v["name"], v["key_file"], v["password"], v["handle"], v["secure_user_id"]), \
            f"{v['name']}: test_frozen_v1.cpp differs from {js}"
        kf, pw, h = bytes.fromhex(v["key_file"]), bytes.fromhex(v["password"]), bytes.fromhex(v["handle"])
        assert len(kf) == 72 and kf[:8] == b"GBGKKEY1" and hashlib.sha256(kf[:40]).digest() == kf[40:], \
            f"{v['name']}: key file format"
        assert len(h) == 58, f"{v['name']}: handle length"
        ver, uid, flags, salt = struct.unpack_from("<BQQQ", h, 0)
        assert ver == 2 and flags == 1 and h[57] == 0 and f"{uid:016x}" == v["secure_user_id"], v["name"]
        assert sig(kf[8:40], h[:17], pw, salt) == h[25:57], f"{v['name']}: signature does not verify"
        assert sig(kf[8:40], h[:17], pw + b"5", salt) != h[25:57], f"{v['name']}: a wrong password verifies"
        print(f"{v['name']}: key file, handle layout and signature OK (sid {uid:016x})")
    print(f"GK_FROZEN_REF: PASS ({len(doc['vectors'])} vectors)")


if __name__ == "__main__":
    try:
        main(sys.argv[1], sys.argv[2])
    except (AssertionError, KeyError, ValueError, OSError) as e:
        print(f"GK_FROZEN_REF: FAIL ({e})")
        sys.exit(1)
