r"""The OTA-robust form of inject\odm_sepolicy.cil (DESIGN.md 4.4): every `(typepermissive T)` line becomes
`(optional gb_ota_T (typepermissive T))`, so a type that a later Google build removes or renames only disables its own
optional block instead of failing the whole policy compile (secilc: unknown type -> init LOG(FATAL) -> reboot loop).
Same function as build_esp.ota_safe_cil (kept identical; tests compare them).
  python -I -B cil_transform.py IN.cil OUT.cil"""
import re
import sys

TP = re.compile(rb"\(typepermissive ([A-Za-z0-9_-]+)\)")


def ota_safe_cil(raw):
    out, names = [], set()
    for ln in raw.split(b"\n"):
        ln = ln.rstrip(b"\r")
        if not ln.strip():
            continue
        m = TP.fullmatch(ln)
        assert m, f"odm_sepolicy.cil: only (typepermissive T) lines are expected, got {ln[:80]!r}"
        t = m.group(1)
        assert t not in names, f"odm_sepolicy.cil: {t.decode()} twice"
        names.add(t)
        out.append(b"(optional gb_ota_" + t + b" (typepermissive " + t + b"))\n")
    assert out, "odm_sepolicy.cil: empty"
    return b"".join(out)


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    open(sys.argv[2], "wb").write(ota_safe_cil(data))
