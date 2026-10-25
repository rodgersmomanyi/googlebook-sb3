"""The \\gbinstall\\ state files (4.5). Builds the documents; writing happens in the flows. Device side only for
reading the logs."""
import json

from . import const
from .util import sha256, sha256sums_text, utc_iso

README = """Googlebook OS - installed next to Windows by the Googlebook SB3 installer ISO (build {build_id}).

This folder (\\gbinstall\\ on the Googlebook EFI-SYSTEM partition) holds the install state in the format of the
Windows dual-boot scripts (C:\\GB-build\\native\\dualboot).

Import it on Windows (elevated PowerShell, reads this partition without a drive letter):
  .\\Import-GooglebookIsoInstall.ps1 -EspId {esp_guid}            (dry run)
  .\\Import-GooglebookIsoInstall.ps1 -EspId {esp_guid} -Apply
Fallback: boot the installer, tools w (copies this folder to a USB stick), then
  .\\Import-GooglebookIsoInstall.ps1 -From <stick>:\\gbsb3-logs\\<...>\\gbinstall -Apply
Import before using uninstall_dualboot.ps1 or install_dualboot.ps1 -UpdateEspOnly / -RepairBootEntry.
install_dualboot.ps1 -UpdateEspOnly rewrites this partition and drops this folder: import first.

Pre-install partition table: gpt_pre_install_primary.bin / gpt_pre_install_backup.bin
(gbdisk restore-gpt --from <folder> --tag pre_install; see README_DUALBOOT, section Recovery).
"""


def install_state(disk_guid, region, entries_ords, sess_build, manifest_sha, esp_guid, super_guid, boot_num,
                  efi_partno, kname, phase):
    an = region["anchor"]
    return {"format": "gb-dualboot-state/1", "mode": "iso", "target": None, "disk_number": None,
            "disk_id": disk_guid, "disk_serial": None, "run_dir": "gbinstall", "export_dir": None,
            "manifest": "manifest_installed.json", "phase": phase, "time": utc_iso(),
            "anchor": {"number": entries_ords[an["slot"]], "id": an["guid"], "offset": an["offset"],
                       "original_length": an["length"], "new_length": an["length"]},
            "plan": "plan.json", "pre_shrink_layout": "layout_pre_install.json",
            "esp_id": esp_guid, "super_id": super_guid, "entry_id": None, "entry_name": const.BOOT_LABEL,
            "iso": {"build_id": sess_build, "payload_manifest_sha256": manifest_sha,
                    "efi_boot_number": ("%04X" % boot_num) if boot_num is not None else None,
                    "efi_partition_number": efi_partno, "disk_kname": kname, "gpt_written": True}}


def files_for_esp(docs, logs_text, build_id, esp_guid):
    """docs: dict name -> bytes|obj. Returns dict name -> bytes incl. install.log, README.txt and SHA256SUMS."""
    out = {}
    for name, v in docs.items():
        if isinstance(v, (bytes, bytearray)):
            out[name] = bytes(v)
        elif isinstance(v, str):
            out[name] = v.encode("utf-8")
        else:
            out[name] = json.dumps(v, separators=(",", ":")).encode("utf-8")
    out["install.log"] = logs_text.encode("utf-8", "replace")
    out["README.txt"] = README.format(build_id=build_id, esp_guid=esp_guid).encode("ascii")
    out["SHA256SUMS"] = sha256sums_text(sorted(out.items())).encode("ascii")
    return out


def verify_sums(files):
    """True if SHA256SUMS lists exactly the other files with their hashes."""
    from .util import parse_sha256sums
    if "SHA256SUMS" not in files:
        return False
    try:
        sums = parse_sha256sums(files["SHA256SUMS"].decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return False
    others = {k: v for k, v in files.items() if k != "SHA256SUMS"}
    return sorted(sums) == sorted(others) and all(sums[k] == sha256(v) for k, v in others.items())
