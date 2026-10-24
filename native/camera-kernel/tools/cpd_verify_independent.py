"""Independent re-implementation of ipu6-cpd.c (v7.3-rc4 + IPU4P series) checks for IPU4P."""
import struct, sys

d = open(sys.argv[1], 'rb').read()
CMPNT = 12 + 32 + 4 + 4 + 16   # ipu6se_cpd_metadata_cmpnt (IPU4P uses ipu6se size)
EXTN = 4 + 4 + 4 + 16          # ipu6_cpd_metadata_extn
ENT = 12 + 4 + 4 + 4           # ipu6_cpd_ent
errs = []

def hdr(buf, off=0):
    mark, cnt, hv, ev, hl = struct.unpack_from('<IIBBB', buf, off)
    return mark, cnt, hv, ev, hl

def ents(buf, off, hl, cnt):
    out = []
    for i in range(cnt):
        nm, o, l, _ = struct.unpack_from('<12sII4s', buf, off + hl + i * ENT)
        out.append((nm.rstrip(b'\0').decode(), o, l))
    return out

def validate_cpd(buf, off, cpd_size, data_size, tag):
    mark, cnt, hv, ev, hl = hdr(buf, off)
    if cpd_size < hl:
        errs.append(tag + ': cpd_size < hdr_len')
    if (cpd_size - hl) // ENT < cnt:
        errs.append(tag + ': header ent_cnt too big')
    for i, (nm, o, l) in enumerate(ents(buf, off, hl, cnt)):
        if data_size < o or data_size - o < l:
            errs.append('%s: entry %d (%s) out of range' % (tag, i, nm))
    return mark, cnt, hl

mark, cnt, hl = validate_cpd(d, 0, len(d), len(d), 'outer')
print('outer mark=%#x ($CPD=%#x) ent_cnt=%d hdr_len=%d' % (mark, 0x44504324, cnt, hl))
if mark != 0x44504324:
    errs.append('bad CPD mark')
E = ents(d, 0, hl, cnt)
print('entries', E)
man, met, mod = E[0], E[1], E[2]
if man[2] > 2048 * 4:
    errs.append('manifest too large')
# metadata
mo, ml = met[1], met[2]
extn_type, elen, img_type = struct.unpack_from('<III', d, mo)
print('metadata extn_type=%#x len=%#x img_type=%d meta_size=%d' % (extn_type, elen, img_type, ml))
if ml < EXTN or ml > 65536:
    errs.append('metadata size')
if extn_type != 0x10 or img_type != 2:
    errs.append('metadata extn/img type')
if (ml - EXTN) % CMPNT:
    errs.append('metadata not multiple of cmpnt size %d' % CMPNT)
ncmp = (ml - EXTN) // CMPNT
print('components (68-byte) =', ncmp, ' (84-byte IPU6 size would give %.2f)' % ((ml - EXTN) / 84))
cmp = []
for i in range(ncmp):
    cid, csz, cver = struct.unpack_from('<III', d, mo + EXTN + i * CMPNT)
    cmp.append((cid, csz, cver))
print('component id/size/ver:', cmp)
# moduledata
xo, xl = mod[1], mod[2]
mh_len, endian, pkgdate = struct.unpack_from('<III', d, xo)
sysver = d[xo + 24: xo + 24 + 11]
arch = d[xo + 35: xo + 35 + 7]
print('moduledata hdr_len=%#x fw_pkg_date=%#x sys_ver=%r fw_arch=%r' % (mh_len, pkgdate, sysver, arch))
if xl < 44 or xl < mh_len:
    errs.append('moduledata size')
imark, icnt, ihl = validate_cpd(d, xo + mh_len, xl - mh_len, xl, 'inner')
IE = ents(d, xo + mh_len, ihl, icnt)
print('inner mark=%#x ent_cnt=%d hdr_len=%d' % (imark, icnt, ihl))
for e in IE:
    print('   ', e)
if icnt + 1 > 16:
    errs.append('pkg_dir overflow')
for i in range(icnt):
    if i > 127 or i >= ncmp:
        errs.append('component index %d out of range' % i)
        continue
    cid, _, cver = cmp[i]
    if cid < 0 or cid > 127:
        errs.append('bad id')
    if cver > 0xffff:
        errs.append('bad ver')
print('RESULT:', 'PASS' if not errs else 'FAIL ' + '; '.join(errs))
