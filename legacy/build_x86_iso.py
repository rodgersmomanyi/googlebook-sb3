"""Build an x86-64 UEFI bring-up ISO for the lapis (Intel) Googlebook image.
systemd-boot (amd64) + Google GKI bzImage + reconstructed GKI initrd (vendor+generic ramdisk + bootconfig)."""
import io, os, re, struct, sys
import pycdlib

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOT = os.path.join(HERE, "boot")
OUT_ISO = os.path.join(HERE, "googlebook-x86.iso")
ESP_MB = 128

SDBOOT  = open(os.path.join(HERE, "debs", "systemd-bootx64.efi"), "rb").read()
KERNEL  = open(os.path.join(BOOT, "kernel.bin"), "rb").read()
GENERIC = open(os.path.join(BOOT, "generic_ramdisk.lz4"), "rb").read()
VENDOR  = open(os.path.join(BOOT, "vendor_ramdisk.lz4"), "rb").read()
VBC     = open(os.path.join(BOOT, "vendor_bootconfig.txt"), "rb").read()

# ---- initrd: vendor ramdisk first, generic ramdisk second (generic /init wins), then bootconfig ----
def make_initrd():
    blob = bytearray()
    blob += VENDOR
    blob += GENERIC
    while len(blob) % 4: blob.append(0)          # 4-byte align before bootconfig
    content = VBC                                 # androidboot.hardware=android-desktop ...
    size = len(content); csum = sum(content) & 0xffffffff
    blob += content + struct.pack("<II", size, csum) + b"#BOOTCONFIG\n"
    return bytes(blob)

INITRD = make_initrd()

# ---- kernel command line ----
VENDOR_CMDLINE = ("snd_intel_sdw_acpi.sdw_link_mask=0x5 kvm-intel.pkvm=1 intel_iommu=igfx_off "
 "xe.mem_monitor_enabled=1 console=null 8250.nr_uarts=4 console_msg_format=syslog log_buf_len=1M "
 "cgroup_disable=pressure root=/dev/ram0 firmware_class.path=/vendor/firmware reserve_mem=20M:2M:trace "
 "trace_instance=boot_mapped^traceoff@trace drm.trace=0x106 sched_proxy_exec=1 module_blacklist=cros_ec_debugfs "
 "proc_mem.force_override=ptrace binder.impl=rust no_console_suspend softlockup_panic=1 nmi_watchdog=panic "
 "pm.dpm_watchdog_enabled=1 cpufreq.default_governor=performance fred=on spi-intel.writeable=1 "
 "spi-intel.ignore_protection_status=1 ia32_emulation=false vsyscall=none trace_instance=backup=boot_mapped "
 "xe.enable_psr=1 snd_intel_dspcfg.dsp_driver=3 snd_sof_intel_hda_common.sof_use_tplg_nhlt=1 "
 "snd_sof_pci.ipc_type=1 snd_intel_sdw_acpi.sdw_link_mask=0xF xe.enable_panel_replay=0 bootconfig")

DROP = ("console=null", "softlockup_panic=1", "nmi_watchdog=panic", "pm.dpm_watchdog_enabled=1",
        "drm.trace=", "reserve_mem=", "trace_instance=", "module_blacklist=", "bootconfig",
        "kvm-intel.pkvm=", "fred=")
keep = [t for t in VENDOR_CMDLINE.split() if not t.startswith(DROP)]

ADD = ("console=ttyS0,115200 console=tty0 earlycon=uart8250,io,0x3f8,115200 ignore_loglevel "
       "printk.devkmsg=on loglevel=8 panic=0 "
       "androidboot.hardware=android-desktop androidboot.slot_suffix=_a androidboot.force_normal_boot=1 "
       "androidboot.verifiedbootstate=orange androidboot.vbmeta.device_state=unlocked "
       "androidboot.selinux=permissive androidboot.boot_source=internal "
       "androidboot.boot_devices=0000:05:00.0 bootconfig")
CMDLINE = " ".join(keep) + " " + ADD

LOADER_CONF = "default gb\ntimeout 5\nconsole-mode max\neditor yes\n"
GB_ENTRY = f"title   Googlebook lapis x86\nlinux   /vmlinuz\ninitrd  /initrd.img\noptions {CMDLINE}\n"

# =============== FAT32 writer (from tools/build_iso.py) ===============
class Node:
    def __init__(self, name, data=None):
        self.name, self.data, self.children = name, data, ({} if data is None else None)
        self.cluster = 0
    @property
    def is_dir(self): return self.children is not None

def build_tree(files):
    root = Node('')
    for path, data in files.items():
        parts = path.split('/'); cur = root
        for d in parts[:-1]: cur = cur.children.setdefault(d, Node(d))
        cur.children[parts[-1]] = Node(parts[-1], data)
    return root

def lfn_checksum(sn):
    s = 0
    for c in sn: s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s

def short_alias(name, used):
    base, _, ext = name.rpartition('.') if '.' in name else (name, '', '')
    clean = lambda s: re.sub(r"[^A-Z0-9_$~!#%&\-{}()@'`^]", '', s.upper())
    b, e = clean(base), clean(ext)[:3]
    if re.fullmatch(r'[A-Z0-9_\-]{1,8}', b) and name == name.upper() and len(ext) <= 3 and b + ('.' + e if e else '') == name:
        return name.upper(), False
    n = 1
    while True:
        tail = '~%d' % n; cand = (b[:8 - len(tail)] + tail)
        full = cand + ('.' + e if e else '')
        if full not in used: return full, True
        n += 1

class Fat32:
    DATE = ((2026 - 1980) << 9) | (10 << 5) | 11
    def __init__(self, total_bytes, label='GBOOK_ESP', spc=2):
        self.spc, self.label = spc, label.ljust(11)[:11]
        self.total = total_bytes // 512; self.res = 32; self.nf = 2
        fz = 0
        for _ in range(50):
            clusters = (self.total - self.res - self.nf * fz) // spc
            nz = ((clusters + 2) * 4 + 511) // 512
            if nz == fz: break
            fz = nz
        self.fatsz, self.clusters = fz, clusters
        assert clusters >= 65525, 'not enough clusters for FAT32'
        self.csize = spc * 512
        self.fat = [0] * (clusters + 2); self.fat[0] = 0x0FFFFFF8; self.fat[1] = 0x0FFFFFFF
        self.data = bytearray(clusters * self.csize); self.nxt = 2
    def alloc(self, nbytes):
        n = max(1, (nbytes + self.csize - 1) // self.csize); first = self.nxt
        assert first + n <= self.clusters + 2, 'FAT image full'
        for i in range(n): self.fat[first + i] = first + i + 1
        self.fat[first + n - 1] = 0x0FFFFFFF; self.nxt += n
        return first
    def put(self, cl, data): o = (cl - 2) * self.csize; self.data[o:o + len(data)] = data
    def dir_entries(self, node, parent_cluster):
        ents = []
        if node.cluster != 2:
            for nm, cl in ((b'.          ', node.cluster), (b'..         ', parent_cluster if parent_cluster != 2 else 0)):
                ents.append(self.sfn(nm, 0x10, cl, 0))
        used = set()
        for name in sorted(node.children, key=lambda s: s.lower()):
            ch = node.children[name]; alias, need_lfn = short_alias(name, used); used.add(alias)
            b, _, e = alias.partition('.'); sn = (b.ljust(8) + e.ljust(3)).encode('ascii')
            if need_lfn:
                u = name.encode('utf-16le') + b'\0\0'; u += b'\xff\xff' * ((-len(u) // 2) % 13)
                cs = lfn_checksum(sn); chunks = [u[i:i + 26] for i in range(0, len(u), 26)]
                for i, c in reversed(list(enumerate(chunks, 1))):
                    seq = i | (0x40 if i == len(chunks) else 0)
                    ents.append(bytes([seq]) + c[0:10] + b'\x0f\x00' + bytes([cs]) + c[10:22] + b'\x00\x00' + c[22:26])
            ents.append(self.sfn(sn, 0x10 if ch.is_dir else 0x20, ch.cluster, 0 if ch.is_dir else len(ch.data)))
        return b''.join(ents)
    def sfn(self, sn, attr, cl, size):
        return (sn + bytes([attr, 0, 0]) + struct.pack('<HHHHHHHI', 0, self.DATE, self.DATE, cl >> 16, 0, self.DATE, cl & 0xFFFF, size))
    def layout(self, node, parent=0):
        n_ent = 0 if node.cluster == 2 else 2
        used = set()
        for name in node.children:
            _, need = short_alias(name, used); used.add(short_alias(name, used)[0])
            n_ent += 1 + ((len(name) + 1 + 12) // 13 if need else 0)
        node.dirbytes = max(32, n_ent * 32)
    def write_tree(self, root):
        root.cluster = self.alloc(0); assert root.cluster == 2
        order = [(root, 0)]; i = 0
        while i < len(order):
            node, par = order[i]; i += 1
            self.layout(node)
            if node is not root: node.cluster = self.alloc(node.dirbytes)
            elif node.dirbytes > self.csize: raise SystemExit('root dir too big')
            for nm, ch in node.children.items():
                if ch.is_dir: order.append((ch, node.cluster))
                else: ch.cluster = self.alloc(len(ch.data)); self.put(ch.cluster, ch.data)
        for node, par in order:
            raw = self.dir_entries(node, par)
            self.put(node.cluster, raw.ljust(max(len(raw), 0)))
    def image(self):
        bs = bytearray(512)
        bs[0:3] = b'\xeb\x58\x90'; bs[3:11] = b'GBOOKISO'
        struct.pack_into('<HBHBHHBHHHII', bs, 11, 512, self.spc, self.res, self.nf, 0, 0, 0xF8, 0, 63, 255, 0, self.total)
        struct.pack_into('<IHHIHH', bs, 36, self.fatsz, 0, 0, 2, 1, 6)
        bs[64] = 0x80; bs[66] = 0x29; struct.pack_into('<I', bs, 67, 0x20261011); bs[71:82] = self.label.encode(); bs[82:90] = b'FAT32   '
        bs[510:512] = b'\x55\xaa'
        fsi = bytearray(512); struct.pack_into('<I', fsi, 0, 0x41615252); struct.pack_into('<I', fsi, 484, 0x61417272)
        struct.pack_into('<II', fsi, 488, self.clusters - (self.nxt - 2), self.nxt); struct.pack_into('<I', fsi, 508, 0xAA550000)
        fatb = struct.pack('<%dI' % len(self.fat), *self.fat)
        fatb += b'\0' * (self.fatsz * 512 - len(fatb))
        res = bytearray(self.res * 512); res[0:512] = bs; res[512:1024] = fsi; res[6 * 512:7 * 512] = bs; res[7 * 512:8 * 512] = fsi
        out = bytes(res) + fatb * self.nf + bytes(self.data)
        return out.ljust(self.total * 512, b'\0')[:self.total * 512]

# =============== assemble ESP + ISO ===============
FILES = {
    'EFI/BOOT/BOOTX64.EFI': SDBOOT,
    'loader/loader.conf': LOADER_CONF.encode(),
    'loader/entries/gb.conf': GB_ENTRY.encode(),
    'vmlinuz': KERNEL,
    'initrd.img': INITRD,
}
fat = Fat32(ESP_MB * 2**20)
fat.write_tree(build_tree(FILES))
ESP = fat.image()
print(f"initrd={len(INITRD):,}B  kernel={len(KERNEL):,}B  ESP used={(fat.nxt-2)*fat.csize/2**20:.1f} MiB of {ESP_MB} MiB", flush=True)
print("CMDLINE:", CMDLINE, flush=True)

iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='GBOOK_X86', sys_ident='X86_64')
iso.add_fp(io.BytesIO(ESP), len(ESP), '/EFIBOOT.IMG;1', rr_name='efiboot.img', joliet_path='/efiboot.img')
iso.add_eltorito('/EFIBOOT.IMG;1', bootcatfile='/BOOT.CAT;1', rr_bootcatname='boot.cat', joliet_bootcatfile='/boot.cat',
                 boot_load_size=1, platform_id=0xEF, media_name='noemul', efi=False)
iso.write(OUT_ISO)
iso.close()
print(f"ISO: {OUT_ISO}  {os.path.getsize(OUT_ISO):,} B", flush=True)
