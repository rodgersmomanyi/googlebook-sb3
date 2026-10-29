# SPDX-License-Identifier: Apache-2.0
"""Independent re-computation of the values gk_host_tests wrote to VECTORS.json.

    python3 -I ref_check.py VECTORS.json

Uses only hashlib (scrypt, sha256), hmac and the `cryptography` package (AES-CMAC); none of the
C code under test. Checks:
  handle      password_handle_t layout (version 2, packed, 58 bytes) and
              signature = HMAC-SHA256(K_dev, scrypt(version|user_id|flags|password, salt,
                                                    N=16384, r=8, p=1, 32))
              plus the inner scrypt step being exactly AOSP SoftGateKeeper's ComputePasswordSignature
  token_*     auth token MAC = HMAC-SHA256(key, hw_auth_token_t[0:37]) with the libgatekeeper
              byte order (version 0, LE challenge/user_id/authenticator_id, BE type and timestamp)
  shared      H = CKDF(zero key, "KeymasterSharedMac", seed1|nonce1|seed2|nonce2...) (NIST SP
              800-108 counter mode, AES-256-CMAC) and sharingCheck = HMAC(H, "Keymaster HMAC
              Verification"), the same computation as system/keymaster soft_keymaster_enforcement
  ckdf16      the one-block CKDF case
"""
import hashlib
import hmac
import json
import struct
import sys

from cryptography.hazmat.primitives import cmac
from cryptography.hazmat.primitives.ciphers import algorithms


def aes_cmac(key, data):
    c = cmac.CMAC(algorithms.AES(key))
    c.update(data)
    return c.finalize()


def ckdf(key, label, context, out_len):
    out = b""
    blocks = (out_len + 15) // 16
    for i in range(1, blocks + 1):
        msg = struct.pack(">I", i) + label + b"\x00" + context + struct.pack(">I", out_len * 8)
        out += aes_cmac(key, msg)
    return out[:out_len]


def check_handle(v):
    key = bytes.fromhex(v["key"])
    pw = bytes.fromhex(v["password"])
    h = bytes.fromhex(v["handle"])
    assert len(h) == 58, len(h)
    version, user_id, flags, salt = struct.unpack_from("<BQQQ", h, 0)
    sig = h[25:57]
    hw = h[57]
    assert version == 2 and flags == 1 and hw == 0, (version, flags, hw)
    to_sign = h[0:17] + pw
    # AOSP SoftGateKeeper::ComputePasswordSignature: crypto_scrypt(password=to_sign,
    # salt=&salt (8 bytes, host order), N=16384, r=8, p=1, 32 bytes)
    stretched = hashlib.scrypt(to_sign, salt=struct.pack("<Q", salt), n=16384, r=8, p=1,
                               dklen=32, maxmem=64 * 1024 * 1024)
    want = hmac.new(key, stretched, hashlib.sha256).digest()
    assert sig == want, "handle signature mismatch"
    assert user_id != 0
    return "handle OK (sid %016x)" % user_id


def check_token(name, v):
    key = bytes.fromhex(v["key"])
    tok = struct.pack("<BQQQ", 0, v["challenge"] & (2**64 - 1), v["user_id"] & (2**64 - 1),
                      v["authenticator_id"] & (2**64 - 1))
    tok += struct.pack(">IQ", v["type"], v["timestamp"])
    assert len(tok) == 37
    want = hmac.new(key, tok, hashlib.sha256).digest()
    assert bytes.fromhex(v["mac"]) == want, name + " MAC mismatch"
    assert v["type"] == 1, "authenticator type must be PASSWORD"
    return name + " OK"


def check_shared(v):
    ctx = b"".join(bytes.fromhex(p["seed"]) + bytes.fromhex(p["nonce"]) for p in v["params"])
    h = ckdf(b"\x00" * 32, b"KeymasterSharedMac", ctx, 32)
    assert h == bytes.fromhex(v["h"]), "H mismatch"
    check = hmac.new(h, b"Keymaster HMAC Verification", hashlib.sha256).digest()
    assert check == bytes.fromhex(v["check"]), "sharing check mismatch"
    return "shared secret OK (%d participants)" % len(v["params"])


def check_ckdf16(v, shared):
    ctx = b"".join(bytes.fromhex(p["seed"]) + bytes.fromhex(p["nonce"]) for p in shared["params"])
    out = ckdf(bytes.fromhex(v["key"]), v["label"].encode(), ctx, 16)
    assert out == bytes.fromhex(v["out"]), "ckdf16 mismatch"
    return "ckdf16 OK"


def main():
    with open(sys.argv[1], encoding="utf-8") as fh:
        vec = json.load(fh)
    # the CMAC implementation itself: NIST SP 800-38B D.3 example 2
    assert aes_cmac(bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4"),
                    bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")).hex() == \
        "28a7023f452e8f82bd4bf28d8c37c35c"
    out = [check_handle(vec["handle"]),
           check_token("token_zero", vec["token_zero"]),
           check_shared(vec["shared"]),
           check_token("token_h", vec["token_h"]),
           check_ckdf16(vec["ckdf16"], vec["shared"])]
    for line in out:
        print(line)
    print("GK_REF_CHECK: PASS (%d checks)" % len(out))


if __name__ == "__main__":
    main()
