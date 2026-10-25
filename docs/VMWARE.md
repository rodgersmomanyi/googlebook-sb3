# Googlebook OS in VMware Workstation

Tested on the Surface Book 3 itself (Windows 11, VMware Workstation 17.x, 4 vCPU / 12 GB guest). The VM uses the
host GPU through VMware's SVGA3D: VMware renders on whichever host GPU Windows gives it (the GTX 1650 in the base
when attached, else the Iris Plus).

## 1. Workspace

Clone this repository (or copy it) to a folder with ~80 GB free; that folder is the workspace (`GB_HOME`). All
commands below run from it. Use `python -I -B` so no `__pycache__` lands next to the images.

## 2. The disk image

```powershell
# the recovery image you obtained (docs/INPUTS.md) -> raw disk
tar -xf <your lapis recovery zip>          # -> android-desktop_signed_recovery_image.bin (19 GB)
Rename-Item android-desktop_signed_recovery_image.bin lapis.bin
python -I -B vm\make_vmdk.py     # lapis.vmdk: flat descriptor for lapis.bin
python -I -B grow_esp.py         # ESP 192 MiB, taken from the unused ChromeOS KERN-A partition (GPT backed up first)
python -I -B grow_userdata.py    # +28 GB, userdata grows over ota_recovery_b; updates lapis.vmdk's extent
```

`grow_esp.py` hard-codes the ESP offset of this recovery image (142606336); check it against your image's GPT first.
Both scripts save the original GPT to `lapis.bin.gptbak`.

## 3. The VM

```powershell
Copy-Item vm\lapis-boot.vmx.template lapis-boot.vmx
```

Open `lapis-boot.vmx` in VMware Workstation (File > Open). Settings that matter:

| Setting | Why |
|---|---|
| `firmware = "efi"`, `uefi.secureBoot.enabled = "FALSE"` | systemd-boot + our unsigned kernel |
| `nvme0:0.fileName = "lapis.vmdk"`, `nvme0:0.mode = "independent-persistent"`, `nvme0.pciSlotNumber = "2208"` | the disk; /data persists; the slot gives the PCI path `pci0000:00/0000:00:15.2` that `build_esp.py` passes as `androidboot.boot_devices` |
| `cpuid.7.1.eax`, `cpuid.7.ecx` masks | hide AVX-VNNI and GFNI from the guest |
| `svga.graphicsMemoryKB = "2097152"`, `mks.enable3d = "TRUE"`, `svga.guestBackedPrimaryAware` | 3D acceleration (2 GB of graphics memory) |
| `mouse.vusb.enable = "FALSE"`, `mouse.vusb.useBasicMouse = "FALSE"` | absolute pointer for `gb_absmouse`, together with vmmouse in psmouse |
| `ethernet0.virtualDev = "vmxnet3"`, NAT | network (the guest's eth0 is DHCP'd by Android's EthernetTracker) |
| `sound.present = "TRUE"`, `sound.virtualDev = "hdaudio"`, `sound.minProgressUsec = "2000"`, `sound.maxPeriodSizeMS = "10"`, `sound.bufferIdealMS = "60"` | sound through lapis's HDA drivers (`gb_vmaudio.sh`); the last three shrink VMware's host audio buffering for lower latency |
| `chipset.useAcpiBattery = "TRUE"` | host battery/AC as ACPI devices (only the first battery; see the battery bridge) |
| `serial0` -> `serial0.log` | kernel + Android log (the diagnostics write to kmsg) |
| `serial1` -> named pipe `\\.\pipe\lapis_con` (server) | root shell for `gbsh.py` |
| `priority.grabbed = "high"`, `mks.svgaThread.pollUS = "250"` | smoother frame pacing |
| `vhv.enable = "TRUE"` | exposes VT-x to the guest; the kernel only turns VMX on if a VM is created (`kvm.enable_virt_at_load=0`) |

Change settings with `vmx_set.ps1 'key=value' ...` while the Workstation window is closed (it powers the guest off
cleanly first); Workstation rewrites the .vmx from its own copy otherwise.

## 4. Kernel and other inputs

1. Build the kernel on the builder and stage it: [kernel/README.md](../kernel/README.md), then
   `python -I -B stage_kernel.py <tag>` (fetches `bzImage` -> `boot/kernel.bin` and the VM + native modules,
   checks every module's dependencies against the load order and that all vermagics match).
2. Build the guest helpers: `NDK=/build/ndk guest/build.sh` on the builder, copy `inject/{vmtools,mouse,slotsync}`
   back.
3. Graphics: build Mesa svga (`graphics/mesa/build_mesa.sh` + patch), create `inject/mesa/ENABLE_SKIAGL`, patch your
   minigbm (`graphics/minigbm/patch_minigbm.py <dir with the two stock libs>`), build SwiftShader
   (`graphics/swiftshader/`).
4. Everything else in [INPUTS.md](INPUTS.md): busybox, systemd-boot, the KeyMint graft, `gen_edid.py`, tinymix and
   `volume_curves.xml` for VM sound.

## 5. Build the ESP and boot

```powershell
$env:GB_WRITE_LAPIS = "1"      # build_esp.py refuses to write lapis.bin without it (boot_live/cycle inherit it)
python -I -B build_esp.py      # builds the ESP (systemd-boot, kernel, initrd for the booted slot) and writes it
                               # into lapis.bin's EFI-SYSTEM partition - only with the VM powered off: it refuses
                               # while VMware has the VM open (a lapis*.lck folder next to lapis.bin)
.\boot_live.ps1                # clean power-off if running, rebuild, start the VM, summarize serial0.log
```

`build_esp.py` prints what it included (`GPU (Mesa svga / SkiaGL): ON`, `absolute mouse: ON`, `network (vmxnet3)`,
`host battery`, `VM sound`, `slot follower` ...). First boot formats /data and runs dexopt; expect several minutes.
`wait_boot.sh` waits for `sys.boot_completed` over the console.

Never write the ESP while the VM runs. An OTA installed in the guest changes which slot boots; `build_esp.py`
follows it automatically (it reads the slot state from lapis.bin, see [OTA.md](OTA.md)).

## 6. Host helpers

* **Battery**: build `host/gb_hostbatt.exe` (`csc -nologo -target:winexe -out:gb_hostbatt.exe gb_hostbatt.cs`) and
  run it at logon (Task Scheduler, "At log on", your user, `gb_hostbatt.exe <workspace>\lapis-boot.vmx`). It reads
  Windows' combined battery level, AC and charging state every 10 s and writes it to the VM (on change and every
  2 min); `gb_vmtools` mirrors it into the guest's battery, so the status bar follows the laptop live. Without it
  the guest shows only the clipboard battery.
* **Console**: `python -I gbsh.py "getprop sys.boot_completed"`, `gbsh.py -f script.sh`, `gbsh.py push|pull`;
  `gbnet.py push|pull` is the fast path over the VM network. `gbsh.py --reset` sends ^C.
* **scrcpy (optional)**: put your ADB public key (`%USERPROFILE%\.android\adbkey.pub`) in `inject/adb/adb_keys`
  (never commit it), rebuild; after boot the guest listens for ADB on its NAT address (`gb_adb.sh`), and
  `host\Googlebook-scrcpy.cmd` opens it with screen, keyboard/mouse, clipboard and audio (unpack scrcpy 5.0.1 for
  Windows as `host\scrcpy-win64-v5.0.1`). While scrcpy runs, the guest's sound plays in it instead of through
  VMware's sound card. `Googlebook-scrcpy.cmd hid` / `hidall` use a virtual HID keyboard (and mouse) inside Android.
  The script uses the guest address `192.168.115.133`: change `IP` in it to your VM's NAT address.
* **Network watchdog**: built together with network ADB, `gb_netwatch.sh` checks eth0 every 10 s and cycles the link
  when it has carrier but no IPv4 address for 30 s (Android's Ethernet stack sometimes stops re-provisioning it after
  a VMware NAT hiccup; the guest then keeps sending unanswered DHCP requests). It logs `gb_netwatch:` to kmsg.
* **Frame pacing**: `tools/frametime.cs` measures the VM window's on-screen update cadence (DXGI desktop duplication).

## Limitations in the VM

* No Bluetooth (features marked unavailable), no sensors, no camera, no touch.
* Software KeyMint, no Gatekeeper/Weaver: no screen-lock credential, no hardware-backed keys or attestation.
* The display never sleeps (`gb_showhome` keeps it awake: a sleep/resume used to wedge SurfaceFlinger) and the
  kernel holds a wakelock; Android suspend is disabled.
* Vulkan apps run on SwiftShader (CPU); GLES/Skia runs on the host GPU.
* After boot, two of the four vCPUs are taken offline (`persist.gb.cpus`, default 2): fewer vCPUs gave lower frame
  latency on a busy 4-thread host. `setprop persist.gb.cpus 0` keeps all online.

## Troubleshooting

* `serial0.log` has everything; useful markers: `GB_MODULES`, `GB_EDID`, `GB_UI`, `GB_P<n>` (boot progress loop),
  `gb_slotsync:`, `gb_vmaudio:`, `gb_netwatch:`.
* `wipe_data.py --yes` (VM off) zeroes the start of userdata; the next boot formats a fresh /data.
* `cycle.ps1` is the quick reboot-and-grep loop used during bring-up.
