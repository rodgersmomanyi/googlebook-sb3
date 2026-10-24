#!/usr/bin/env python3
"""Offline re-implementation of the checks the (patched) intel-ipu6 driver applies to the
IPU4P firmware at probe: ipu6_cpd_validate_cpd_file() -> __ipu6_validate_cpd_file() and the
package-directory build in ipu6_cpd_parse_module_data(), with the IPU4P parameters
(cpd_metadata_cmpnt_size = sizeof(struct ipu6se_cpd_metadata_cmpnt) = 68).

usage: python -I cpd_check.py <ipu4p_fw.bin>
"""
import hashlib
import struct
import sys

CPD_HDR_MARK = 0x44504324
MAX_MANIFEST_SIZE = 2048 * 4
MAX_METADATA_SIZE = 64 * 1024
MAX_COMPONENT_ID = 127
MAX_COMPONENT_VERSION = 0xFFFF
EXTN_TYPE_IUNIT = 0x10
IMG_TYPE_MAIN_FIRMWARE = 2
CMPNT_SIZE_IPU4P = 12 + 32 + 4 + 4 + 16      # ipu6se_cpd_metadata_cmpnt
ENT = struct.Struct("<12sII4s")              # ipu6_cpd_ent
MODHDR = struct.Struct("<IIIIII11s7s2s")     # ipu6_cpd_module_data_hdr


class Bad(Exception):
    pass


def cpd_hdr(buf, off):
    mark, cnt, hver, ever, hlen = struct.unpack_from("<IIBBB", buf, off)
    return mark, cnt, hver, ever, hlen


def entries(buf, off):
    _, cnt, _, _, hlen = cpd_hdr(buf, off)
    return [ENT.unpack_from(buf, off + hlen + i * ENT.size) for i in range(cnt)]


def validate_cpd(buf, off, cpd_size, data_size, what):
    _, cnt, _, _, hlen = cpd_hdr(buf, off)
    if cpd_size < hlen:
        raise Bad(f"{what}: Invalid CPD moduledata size")
    if (cpd_size - hlen) // ENT.size < cnt:
        raise Bad(f"{what}: Invalid CPD header")
    for i, (name, eoff, elen, _) in enumerate(entries(buf, off)):
        if data_size < eoff or data_size - eoff < elen:
            raise Bad(f"{what}: Invalid CPD entry ({i})")


def main(path):
    buf = open(path, "rb").read()
    print(f"file: {path}\nsize: {len(buf)}\nsha256: {hashlib.sha256(buf).hexdigest()}")
    validate_cpd(buf, 0, len(buf), len(buf), "file")
    mark, cnt, hver, ever, hlen = cpd_hdr(buf, 0)
    if mark != CPD_HDR_MARK:
        raise Bad("Invalid CPD header (marker)")
    print(f"$CPD: entries={cnt} hdr_ver={hver} ent_ver={ever} hdr_len={hlen:#x} "
          f"partition={buf[12:16].decode(errors='replace')!r}")
    ents = entries(buf, 0)
    for name, eoff, elen, _ in ents:
        print(f"  entry {name.rstrip(b'\0').decode():12s} off={eoff:#09x} len={elen:#09x}")
    man, met, mod = ents[0], ents[1], ents[2]
    if man[2] > MAX_MANIFEST_SIZE:
        raise Bad("Invalid CPD manifest size")
    # metadata
    moff, mlen = met[1], met[2]
    if mlen < 28 or mlen > MAX_METADATA_SIZE:
        raise Bad("Invalid CPD metadata")
    extn_type, extn_len, img_type = struct.unpack_from("<III", buf, moff)
    if extn_type != EXTN_TYPE_IUNIT or img_type != IMG_TYPE_MAIN_FIRMWARE:
        raise Bad(f"Invalid CPD metadata descriptor img_type ({img_type})")
    if (mlen - 28) % CMPNT_SIZE_IPU4P:
        raise Bad("Invalid CPD metadata size (component size mismatch)")
    ncmp = (mlen - 28) // CMPNT_SIZE_IPU4P
    cmpnts = []
    for i in range(ncmp):
        cid, csize, cver = struct.unpack_from("<III", buf, moff + 28 + i * CMPNT_SIZE_IPU4P)
        cmpnts.append((cid, csize, cver))
    print(f"metadata: extn_type={extn_type:#x} img_type={img_type} components={ncmp} "
          f"(component size {CMPNT_SIZE_IPU4P})")
    # moduledata
    doff, dlen = mod[1], mod[2]
    if dlen < MODHDR.size:
        raise Bad("Invalid CPD moduledata size")
    (mhlen, endian, pkg_date, sdk_date, cc_date, plat, sysver, archver, _) = \
        MODHDR.unpack_from(buf, doff)
    if dlen < mhlen:
        raise Bad("Invalid CPD moduledata size")
    print(f"moduledata: hdr_len={mhlen:#x} fw_pkg_date={pkg_date:#x} hive_sdk={sdk_date:#x} "
          f"compiler={cc_date:#x} platform={plat} sys_ver={sysver.rstrip(b'\0')!r} "
          f"fw_arch={archver.rstrip(b'\0')!r}")
    validate_cpd(buf, doff + mhlen, dlen - mhlen, dlen, "moduledata")
    dmark, dcnt, _, _, _ = cpd_hdr(buf, doff + mhlen)
    if dmark != CPD_HDR_MARK:
        print("  (note: nested directory marker is not $CPD; driver does not check it)")
    # package directory (ipu6_cpd_parse_module_data)
    for i, (name, eoff, elen, _) in enumerate(entries(buf, doff + mhlen)):
        if i >= ncmp:
            raise Bad("Component index out of range")
        cid, _, cver = cmpnts[i]
        if cid < 0 or cid > MAX_COMPONENT_ID:
            raise Bad("Invalid CPD component id")
        if cver > MAX_COMPONENT_VERSION:
            raise Bad("Invalid CPD component version")
        print(f"  pkg_dir[{i}] {name.rstrip(b'\0').decode():12s} id={cid:3d} ver={cver:#06x} "
              f"len={elen:#x}")
    print("RESULT: PASS - accepted by the IPU4P CPD checks of the patched intel-ipu6 driver")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Bad as e:
        print("RESULT: FAIL -", e)
        sys.exit(1)
