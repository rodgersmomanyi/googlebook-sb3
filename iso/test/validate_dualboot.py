r"""Dual-boot and uninstall validation (DESIGN.md 7.2 T3, T3n, T4, T4b, T4n). Read-only on the disk images.

Imports C:\GB-build\native\dualboot\tools\gpt_validate.py by path for GPT parsing and the per-MiB digests.
EFI variables are compared as raw efivarfs bytes (4 attribute bytes + data) pulled from the guest; load options are
parsed here (4.2 "EFI load options").
"""
import hashlib
import json
import os
import struct
import subprocess
import sys
import uuid

import common as C
import mkdisks
import validate_wholedisk as W

ACTIVE = 0x00000001


# ------------------------------------------------------------------------------------------------ EFI variables
def var_data(raw):
    return raw[4:] if raw is not None and len(raw) >= 4 else None


def boot_order(raw):
    d = var_data(raw)
    if d is None:
        return None
    return [struct.unpack_from("<H", d, i)[0] for i in range(0, len(d) - 1, 2)]


def fmt_order(order):
    return ",".join("%04X" % n for n in order) if order else "-"


def parse_load_option(raw):
    """EFI_LOAD_OPTION from an efivarfs file: dict attributes, description, hd (or None), path, nodes."""
    d = var_data(raw)
    attrs, fpl = struct.unpack_from("<IH", d, 0)
    i = 6
    while i + 1 < len(d) and d[i:i + 2] != b"\0\0":
        i += 2
    desc = d[6:i].decode("utf-16-le", errors="replace")
    fp = d[i + 2:i + 2 + fpl]
    nodes, j = [], 0
    while j + 4 <= len(fp):
        t, st, ln = fp[j], fp[j + 1], struct.unpack_from("<H", fp, j + 2)[0]
        if ln < 4:
            break
        nodes.append((t, st, fp[j + 4:j + ln]))
        j += ln
        if t == 0x7F and st == 0xFF:
            break
    hd, path, before_hd = None, "", 0
    for t, st, body in nodes:
        if t == 4 and st == 1 and hd is None and len(body) >= 38:
            part, start, size = struct.unpack_from("<IQQ", body, 0)
            sig, mbrt, sigt = body[20:36], body[36], body[37]
            hd = dict(part=part, start=start, size=size, mbr_type=mbrt, sig_type=sigt,
                      guid=str(uuid.UUID(bytes_le=bytes(sig))) if sigt == 2 else sig.hex())
        elif t == 4 and st == 4:
            path += body.decode("utf-16-le", errors="replace").rstrip("\0")
        elif hd is None and not (t == 0x7F):
            before_hd += 1
    return dict(attributes=attrs, description=desc, hd=hd, path=path, nodes=[(t, st) for t, st, _ in nodes],
                nodes_before_hd=before_hd)


def numbered(vars_):
    """{n: raw} of the Boot#### variables."""
    out = {}
    for k, v in vars_.items():
        if len(k) == 8 and k.startswith("Boot"):
            try:
                out[int(k[4:], 16)] = v
            except ValueError:
                pass
    return out


def descriptions(vars_):
    out = {}
    for n, raw in numbered(vars_).items():
        try:
            out[n] = parse_load_option(raw)["description"]
        except Exception:
            out[n] = None
    return out


def find_by_description(vars_, needle):
    return sorted(n for n, d in descriptions(vars_).items() if d and needle in d)


def vars_summary(vars_):
    return {"BootOrder": fmt_order(boot_order(vars_.get("BootOrder"))),
            "BootNext": fmt_order(boot_order(vars_.get("BootNext"))) if vars_.get("BootNext") else None,
            "entries": {"Boot%04X" % n: {"label": descriptions(vars_).get(n),
                                         "sha256": hashlib.sha256(raw).hexdigest()}
                        for n, raw in sorted(numbered(vars_).items())}}


def check_efi_install(res, before, after, esp_guid, esp_first, esp_sectors, part_number, efi_markers):
    """T3 criterion 8."""
    ob, oa = boot_order(before.get("BootOrder")), boot_order(after.get("BootOrder"))
    nb, na = numbered(before), numbered(after)
    new = sorted(set(na) - set(nb))
    res.evidence["efi_before"] = vars_summary(before)
    res.evidence["efi_after"] = vars_summary(after)
    if not res.check(len(new) == 1, "exactly one new Boot#### variable (%s)" % ["%04X" % n for n in new]):
        return None
    n = new[0]
    res.check(oa == (ob or []) + [n], "BootOrder after %s == before %s + [%04X]" % (fmt_order(oa), fmt_order(ob), n))
    wbm = find_by_description(before, "Windows Boot Manager")
    res.check(bool(oa) and bool(wbm) and oa[0] == wbm[0],
              "first BootOrder entry is still the fake Windows Boot Manager (%s)" % ["%04X" % w for w in wbm])
    lo = parse_load_option(na[n])
    hd = lo["hd"] or {}
    res.evidence["new_entry"] = dict(lo, number="%04X" % n)
    res.check(lo["description"] == C.BOOT_LABEL, "new entry labelled %r" % lo["description"])
    res.check(lo["attributes"] == ACTIVE, "new entry Attributes == LOAD_OPTION_ACTIVE (0x%x)" % lo["attributes"])
    res.check(hd.get("part") == part_number and hd.get("sig_type") == 2 and hd.get("mbr_type") == 2
              and hd.get("guid") == esp_guid and hd.get("start") == esp_first and hd.get("size") == esp_sectors,
              "HD(%s,GPT,%s,%s,%s) == HD(%d,GPT,%s,%d,%d)" % (hd.get("part"), hd.get("guid"), hd.get("start"),
                                                          hd.get("size"), part_number, esp_guid, esp_first,
                                                          esp_sectors))
    res.check(lo["path"].lower() == C.LOADER.lower(), "File(%s) == %s (case-insensitive)" % (lo["path"], C.LOADER))
    if lo["nodes_before_hd"]:
        res.info("new entry has %d device-path nodes before HD (accepted, A5)" % lo["nodes_before_hd"])
    same = all(nb[k] == na.get(k) for k in nb)
    res.check(same, "every pre-existing Boot#### byte-identical")
    res.check(before.get("BootNext") == after.get("BootNext"), "BootNext unchanged")
    aft = [kv for t, kv in efi_markers if kv.get("phase") == "after"]
    bef = [kv for t, kv in efi_markers if kv.get("phase") == "before"]
    res.check(bool(aft) and aft[-1].get("bootorder") == fmt_order(oa) and aft[-1].get("new") == "%04X" % n,
              "@@GB EFI phase=after agrees (%s)" % (aft[-1] if aft else None))
    res.check(bool(bef) and bef[-1].get("bootorder") == fmt_order(ob), "@@GB EFI phase=before agrees")
    return n


def check_efi_uninstall(res, before, after, gb_number, g2_before_order=None, efi_markers=()):
    """T4 / T4b EFI criteria: BootOrder == before minus the Googlebook number, it is gone, the rest byte-identical."""
    ob, oa = boot_order(before.get("BootOrder")), boot_order(after.get("BootOrder"))
    nb, na = numbered(before), numbered(after)
    res.evidence["efi_before"] = vars_summary(before)
    res.evidence["efi_after"] = vars_summary(after)
    res.check(oa == [x for x in (ob or []) if x != gb_number], "BootOrder after %s == before %s minus %04X"
              % (fmt_order(oa), fmt_order(ob), gb_number))
    if g2_before_order is not None:
        res.check(oa == g2_before_order, "BootOrder == the order before the install (%s)" % fmt_order(g2_before_order))
    res.check(gb_number not in na, "Boot%04X (Googlebook OS) is gone" % gb_number)
    res.check(all(na.get(k) == v for k, v in nb.items() if k != gb_number) and set(na) == set(nb) - {gb_number},
              "all other Boot#### variables byte-identical")
    if efi_markers:
        aft = [kv for t, kv in efi_markers if kv.get("phase") == "after"]
        res.check(bool(aft) and aft[-1].get("bootorder") == fmt_order(oa), "@@GB EFI phase=after agrees")


def check_efi_unchanged(res, before, after, label="EFI variables"):
    res.check(before == after, "%s byte-identical (%d variables)" % (label, len(before)))


# ------------------------------------------------------------------------------------------------ GPT / contents
def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def attrs_int(v):
    return int(v, 16) if isinstance(v, str) else int(v)


def check_t3_disk(res, raw, pre_json, expected_plan, export_manifest, payload_manifest, plan_from_esp):
    """T3 criteria 2, 3, 4, 5 and 7 (host side, read-only)."""
    gv = mkdisks.load_gpt_validate()
    snap = load(pre_json)
    exp = load(expected_plan)
    exm = load(export_manifest)
    man = payload_manifest
    info, probs = mkdisks.read_gpt(raw)
    res.check(info is not None and not probs, "2: GPT structurally valid (%s)" % (probs or "no problems"))
    if info is None:
        return None
    h1, s1 = info["h1"], snap["h1"]
    res.check(info["mbr_sha"] == snap["mbr_sha"], "2: protective MBR byte-identical")
    for k in ("disk", "first", "last", "n", "esz", "elba"):
        res.check(h1[k] == s1[k], "2: header %s unchanged (%s)" % (k, h1[k]))
    cur = {e["slot"]: e for e in info["entries"]}
    for e in snap["entries"]:
        res.check(cur.get(e["slot"], {}).get("raw") == e["raw"], "3: pre-existing slot %d '%s' byte-identical"
                  % (e["slot"], e["name"]))
    nslots = len(snap["entries"])
    plan_by_id = {p["id"]: p for p in exp["partitions"]}
    new = []
    for i, p in enumerate(exm["partitions"]):
        e = cur.get(nslots + i)
        ok = e is not None and (e["name"], e["type"], e["id"], e["attributes"]) == (
            p["name"], p["type"], p["guid"], attrs_int(p["attributes"]))
        pp = plan_by_id.get(p["guid"])
        ok2 = e is not None and pp is not None and (e["offset"], e["length"]) == (pp["offset"], pp["length"])
        res.check(ok and ok2, "3: slot %d = %s name/type/GUID/attributes of the export manifest, offset/length == "
                              "expected_plan (%s)" % (nslots + i, p["name"],
                                                     (e["offset"], e["length"]) if e else None))
        if e:
            new.append(e)
    extra = [e for s, e in cur.items() if s >= nslots + len(exm["partitions"])]
    res.check(not extra, "3: no other new entries")
    if len(new) == len(exm["partitions"]):
        start = min(e["offset"] for e in new)
        end = max(e["offset"] + e["length"] for e in new)
        ud = [e for e in new if e["name"] == "userdata"][0]
        rec = [e for e in snap["entries"] if e["type"] == C.TYPE_WINRE]
        res.check(start == 3343908864, "3: start == D: end == 3,343,908,864 (%d)" % start)
        res.check(ud["length"] == 10411 * C.MiB, "3: userdata 10,411 MiB (%d MiB)" % (ud["length"] // C.MiB))
        if rec:
            res.check(rec[0]["offset"] - end == C.GiB, "3: exactly 1 GiB unallocated before Recovery (%d)"
                      % (rec[0]["offset"] - end))
    # 4: plan.json from the ESP
    if plan_from_esp and os.path.isfile(plan_from_esp):
        pl = load(plan_from_esp)
        key = lambda p: (p["name"], p["type"], p["id"], p["offset"], p["length"], attrs_int(p["attributes"]))
        res.check([key(p) for p in pl["partitions"]] == [key(p) for p in exp["partitions"]],
                  "4: plan.json partitions == expected_plan.json")
        res.check(pl.get("region") == exp.get("region"), "4: plan.json region == expected (%s)" % pl.get("region"))
        res.check(pl.get("anchor", {}).get("number") == 4 == exp.get("anchor", {}).get("number"),
                  "4: anchor.number == 4")
        rc, out = mkdisks.gbdisk("verify", "--image", raw, "--plan", plan_from_esp, "--layout-only")
        res.evidence["gbdisk_verify"] = out[-3000:]
        res.check(rc == 0, "4: gbdisk verify --layout-only exit 0 (%d)" % rc)
    else:
        res.check(False, "4: plan.json from the ESP present")
    # 5: contents
    objs = man["objects"]
    with open(raw, "rb") as f:
        for dp, e in zip(man["dual_boot"]["partitions"], new):
            if dp["name"] == "EFI-SYSTEM":
                continue
            if dp["object"]:
                o = objs[dp["object"]]
                res.check(W.sha256_range(f, e["offset"], o["raw_size"]) == o["sha256"],
                          "5: %s sha256 == object %s" % (dp["name"], dp["object"]))
                if e["length"] > o["raw_size"]:
                    res.check(W.zero_range(f, e["offset"] + o["raw_size"], e["length"] - o["raw_size"]),
                              "5: %s tail zero" % dp["name"])
            else:
                ok = all(W.zero_range(f, e["offset"] + a, n) for a, n in W.zero_ranges_405(e["length"]))
                res.check(ok, "5: %s blank, zero per 4.0.5" % dp["name"])
    # 7: every MiB outside the new partitions byte-identical
    now = gv.digests(raw)
    units = snap["units"]
    region = [(e["offset"], e["offset"] + e["length"]) for e in new]
    changed = [u for u, s, e in gv.unit_ranges(info["size"]) if now[u] != units[u]
               and not any(s >= a and e <= b for a, b in region)]
    res.check(len(now) == len(units) and not changed, "7: every MiB outside the 18 new partitions byte-identical "
                                                      "to pre.json %s" % (changed[:10] or ""))
    esp = [e for e in new if e["name"] == "EFI-SYSTEM"]
    return esp[0] if esp else None


def check_install_state(res, state_path, expected_plan, export_manifest, payload_manifest, build_id, efi_number):
    """T3 criterion 9."""
    st = load(state_path)
    exp = load(expected_plan)
    db = payload_manifest["dual_boot"]
    anchor_id = exp["anchor"]["id"]
    want = {"format": "gb-dualboot-state/1", "mode": "iso", "phase": "complete", "esp_id": db["esp_guid"],
            "super_id": db["super_guid"], "entry_name": C.BOOT_LABEL}
    for k, v in want.items():
        res.check(st.get(k) == v, "9: install_state %s == %r (%r)" % (k, v, st.get(k)))
    a = st.get("anchor") or {}
    res.check(a.get("number") == 4 and a.get("id") == anchor_id, "9: anchor number 4, id = D:'s GUID %s" % anchor_id)
    res.check(a.get("original_length") == 2147483648 == a.get("new_length"),
              "9: original_length == new_length == 2,147,483,648 (%s, %s)" % (a.get("original_length"),
                                                                              a.get("new_length")))
    iso = st.get("iso") or {}
    res.check(iso.get("efi_partition_number") == 6, "9: iso.efi_partition_number == 6 (%s)"
              % iso.get("efi_partition_number"))
    res.check(iso.get("build_id") == build_id, "9: iso.build_id == %s" % build_id)
    if efi_number is not None:
        res.check(str(iso.get("efi_boot_number", "")).upper() == "%04X" % efi_number,
                  "9: iso.efi_boot_number == %04X (%s)" % (efi_number, iso.get("efi_boot_number")))
    res.check(st.get("disk_id") == load_snapshot_disk(expected_plan), "9: disk_id == the disk GUID")
    return st


def load_snapshot_disk(expected_plan):
    return load(expected_plan).get("disk_id")


def gpt_validate_cli(res, label, *args):
    rc, out = mkdisks.gpt_validate(*args)
    res.evidence[label] = out[-4000:]
    ok = rc == 0 and "RESULT: PASS" in out
    res.check(ok, "%s: gpt_validate.py %s RESULT PASS (exit %d)" % (label, " ".join(a for a in args[:1]), rc))
    return ok
