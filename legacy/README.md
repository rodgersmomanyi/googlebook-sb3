# legacy

`build_x86_iso.py`: the first bring-up tool, a UEFI ISO with systemd-boot, Google's own GKI kernel and the lapis
ramdisks, before we had our own kernel and the overlay tree. Kept for reference: it records lapis's original vendor
kernel command line. Needs `pycdlib`. Superseded by `build_esp.py`.
