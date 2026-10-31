#!/bin/bash
# Generate kernel patch 0113 (k16: Realtek card-reader drivers as modules) in a scratch git repository that holds only
# the two files it touches, read from the k15 branch with `git show` (so /build/gki/common is never written).
#   arch/x86/configs/gki_defconfig: the four options at the places savedefconfig puts them (tools/cfg_check.sh proves
#     savedefconfig(new file) == new file, so Kleaf's "savedefconfig does not match" check passes)
#   BUILD.bazel: the four .ko appended to kernel_x86_64's module_implicit_outs and modules_superset (the k13 lists)
# Output: /var/tmp/k16/fp/out/0113-*.patch and /var/tmp/k16/fp/gki_defconfig.k16 (for cfg_check.sh).
# Usage (builder): bash gen_0113.sh
set -eu
W=/var/tmp/k16/fp
SRC=/build/gki/common
export GIT_AUTHOR_NAME="GB-build k16" GIT_AUTHOR_EMAIL=k16@builder.invalid
export GIT_COMMITTER_NAME="GB-build k16" GIT_COMMITTER_EMAIL=k16@builder.invalid
export GIT_AUTHOR_DATE="Fri, 30 Oct 2026 20:00:00 +0000" GIT_COMMITTER_DATE="Fri, 30 Oct 2026 20:00:00 +0000"
rm -rf "$W"; mkdir -p "$W/repo/arch/x86/configs" "$W/out"
cd "$W/repo"
git init -q .
git -C "$SRC" show k15-nouveau:arch/x86/configs/gki_defconfig > arch/x86/configs/gki_defconfig
git -C "$SRC" show k15-nouveau:BUILD.bazel > BUILD.bazel
git add -A; git commit -q -m "k15-nouveau files"
# --- gki_defconfig: savedefconfig order (cfg_check.sh "canonical placement")
python3 -I - <<'EOF'
p = "arch/x86/configs/gki_defconfig"
t = open(p).read()
a, b = "CONFIG_VCPU_STALL_DETECTOR=m\n", "CONFIG_MMC_SDHCI_PLTFM=y\n"
assert t.count(a) == 1 and t.count(b) == 1 and "RTSX" not in t and "MMC_REALTEK" not in t
t = t.replace(a, a + "CONFIG_MISC_RTSX_PCI=m\nCONFIG_MISC_RTSX_USB=m\n")
t = t.replace(b, b + "CONFIG_MMC_REALTEK_PCI=m\nCONFIG_MMC_REALTEK_USB=m\n")
open(p, "w").write(t)
q = "BUILD.bazel"
s = open(q).read()
old = '"drivers/platform/x86/mxm-wmi.ko"]'
new = ('"drivers/platform/x86/mxm-wmi.ko", "drivers/misc/cardreader/rtsx_pci.ko", "drivers/misc/cardreader/rtsx_usb.ko", '
       '"drivers/mmc/host/rtsx_pci_sdmmc.ko", "drivers/mmc/host/rtsx_usb_sdmmc.ko"]')
lines = s.split("\n")
hit = [i for i, l in enumerate(lines) if old in l]
assert len(hit) == 2 and lines[hit[0]].lstrip().startswith("module_implicit_outs = ") and \
    lines[hit[1]].lstrip().startswith("modules_superset = ") and hit[1] == hit[0] + 1, hit
assert "rtsx" not in s
for i in hit:
    assert lines[i].count(old) == 1
    lines[i] = lines[i].replace(old, new)
open(q, "w").write("\n".join(lines))
print("edited: defconfig +4 lines, BUILD.bazel 2 lines")
EOF
cp arch/x86/configs/gki_defconfig "$W/gki_defconfig.k16"
git add -A
git commit -q -F - <<'EOF'
gki_defconfig, BUILD.bazel: build the Realtek card-reader drivers as modules

The Surface Book 3 has an SD card reader in its keyboard base. Its type
is still unknown: a USB mass-storage-class reader already works with the
built-in usb-storage/uas, but a Realtek vendor-class USB reader
(RTS5129/RTS5139/RTS5179/RTS5170, 0bda:0129/0139/0140) needs rtsx_usb
plus rtsx_usb_sdmmc, and a Realtek PCIe reader needs rtsx_pci plus
rtsx_pci_sdmmc. lapis's vendor_dlkm has none of them.

Build all four as modules (MISC_RTSX_PCI, MISC_RTSX_USB,
MMC_REALTEK_PCI, MMC_REALTEK_USB =m; MISC_RTSX follows as m, MFD_CORE
stays m) and list them in kernel_x86_64's module outputs. They are
loaded by modalias from the native tree, never preloaded. Memory Stick
(MEMSTICK) and USB_STORAGE_REALTEK stay off: the latter would make the
built-in usb-storage skip Realtek mass-storage readers.

The KMI symbol list is unchanged. Because the GKI build keeps the
exports its own modules need (TRIM_UNUSED_KSYMS), vmlinux additionally
exports the core symbols these drivers import and the trimmed k15
vmlinux did not export (the out-of-tree pre-build check found seven:
devm_mmc_alloc_host, mmc_add_host, mmc_remove_host, usb_sg_init,
usb_sg_wait, usb_sg_cancel, pci_intx). Every existing export keeps its
CRC.
EOF
git format-patch -q --zero-commit --abbrev=12 --start-number 113 --subject-prefix="PATCH 113/113" -1 -o "$W/out" HEAD
ls -la "$W/out"
sha256sum "$W"/out/*.patch
