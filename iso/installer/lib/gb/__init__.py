"""Googlebook SB3 installer package (runs only inside the installer initramfs; see DESIGN.md).

Pure modules (no device, mount, ioctl or process code; host unit tests may import them):
const, util, gpt, plan, manifest, efi.
Everything else touches the system and is used only inside the installer environment.
"""
