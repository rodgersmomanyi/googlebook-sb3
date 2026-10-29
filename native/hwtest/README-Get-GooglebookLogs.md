# Get-GooglebookLogs.ps1: collecting the Googlebook boot logs on Windows

`Get-GooglebookLogs.ps1` copies the logs that every native boot writes to the stick's `EFI-SYSTEM` partition
(`\gblog\boot<N>-t<sec>.txt`, `audio-b<N>-...`, `bootcount`) to `Documents\Googlebook-logs\<timestamp>\`, and makes a
redacted zip that can be sent or posted. It changes nothing on the stick.

| File | What |
|---|---|
| `Get-GooglebookLogs.ps1` | the tool (Windows PowerShell 5.1; ASCII only; embedded C# for the Win32 calls and the redaction engine) |
| `tests\Test-GetGooglebookLogs.ps1` | Pester-free self-tests (mocks only, never a real disk) |
| `tests\results\selftest-<stamp>.txt` | self-test summaries (counts, PASS/FAIL and masked line shapes only) |

(`README.md`, `analyze_gblog.py` and `fixtures\` in this folder belong to the sibling log analyzer. The self-tests
also run its synthetic SB3 fixtures through the redaction when they are present.)

## Section for README-FLASH.md, section 7 (ready to paste)

> ### Collecting the logs with one command
>
> After a boot, switch the Surface off, put the stick into a Windows PC and run this in a PowerShell window:
>
> ```powershell
> powershell -NoProfile -ExecutionPolicy Bypass -File C:\GB-build\native\hwtest\Get-GooglebookLogs.ps1
> ```
>
> The script finds the stick by its partition names (`EFI-SYSTEM`, `super`, `boot_a`, on a USB disk only). It
> refuses internal disks, and if two sticks are plugged in it asks you to choose one with `-DiskNumber <n>`. It copies `\gblog\`
> without changing the stick and puts two things into `Documents\Googlebook-logs\<date-time>\`:
>
> * `gblog\`: the exact, read-only copy. **Keep it private.** It can contain serial numbers, network names and your
>   account address.
> * `Googlebook-logs-<date-time>-share.zip`: **send me this one**. Serial numbers, MAC addresses, Wi-Fi names,
>   e-mail addresses, IMEI-like ids, partition/machine UUIDs, user names and passwords are replaced by placeholders
>   such as `<mac-1>`, and the same value always gets the same placeholder. Microphone test recordings (`.wav`) are left
>   out unless you add `-IncludeAudio`.
>
> Windows normally lets only administrators read an EFI partition. If it refuses, the script says so and offers to
> restart itself as administrator (Windows asks you to confirm). Add `-RedactTerm "your name","your Wi-Fi name"` to
> remove extra words. Skim `share\REDACTION-REPORT.txt` before posting the zip publicly. If your Documents folder is
> synced by OneDrive, the logs go to `%USERPROFILE%\Googlebook-logs` instead, so the private copy stays on the PC.

## Using it

```powershell
# normal use: find the stick, copy, redact, zip
powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-GooglebookLogs.ps1

# only show what the script sees (no admin needed, copies nothing); a good first run on new hardware
powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-GooglebookLogs.ps1 -ListOnly

# extra words to remove; include the audio recordings; pick one of two sticks
... -RedactTerm 'YourName','YourWiFi' -IncludeAudio -DiskNumber 2

# logs that are already reachable as a folder (a drive letter you assigned, an older copy)
... -SourcePath S:\            # or S:\gblog, or \\?\Volume{GUID}\
```

If `Documents` is synced by OneDrive (common on Windows 11), the default output folder is `%USERPROFILE%\Googlebook-logs`
instead, so the private copy and `redaction-map.csv` are not uploaded; the script says so. An explicit `-OutputRoot` inside
OneDrive is kept but the script warns.

`-ExecutionPolicy Bypass` applies to that one PowerShell process only and changes nothing permanently. Other
switches: `-OutputRoot <folder>` (instead of `Documents\Googlebook-logs`), `-NoShare` (private copy only),
`-NoElevationPrompt` (never offer the administrator restart). Run it from a PowerShell window, not with
"Run with PowerShell", so the summary stays on screen.

## Administrator rights: when and why

| Step | Admin? |
|---|---|
| list disks, bus types and **GPT partition names** (`IOCTL_DISK_GET_DRIVE_LAYOUT_EX` etc. on a handle opened with no access rights) | no |
| list volumes and map them to disk + offset (`FindFirstVolume`, `IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS`) | no |
| read files on `EFI-SYSTEM` through `\\?\Volume{GUID}\` (no drive letter) | **usually yes**: Windows restricts EFI system partitions to administrators. The script tries without admin first. On "access denied" it stops before creating anything, then offers to restart itself elevated (UAC prompt) or prints the exact command. |
| fallback, only when the volume path cannot be read: a temporary drive letter | yes (`SetVolumeMountPoint`, or `Add-PartitionAccessPath` when Windows lists no volume). The letter is checked to point at the selected partition and is always removed in a `finally`, also on errors and Ctrl+C, including a Ctrl+C that lands while the letter is being assigned. If removal fails, the script prints `mountvol X: /D`. Closing the PowerShell window during the copy kills the process before any `finally` runs; the next run then says that EFI-SYSTEM already has a letter and prints the `mountvol` command. |

The disk serial number is never read (the storage descriptor's serial field is skipped). The disk list shows only
the bus type, the removable flag, the vendor/product strings and the size. Files on the stick are opened with
`GENERIC_READ` only; nothing is written, renamed or deleted there. Windows itself may update a FAT "last access" date.

## What you get

```
Documents\Googlebook-logs\20261026-153012\
  gblog\                                     exact copy, files marked read-only        PRIVATE
  SHA256SUMS.txt                             hash of every file, equal to the bytes read from the stick
  COPY-INFO.txt                              disk/partition/volume and the method used  PRIVATE
  redaction-map.csv                          placeholder -> original value              PRIVATE
  share\gblog\...                            redacted text files
  share\REDACTION-REPORT.txt                 counts per category, file list, final check (no values)
  Googlebook-logs-20261026-153012-share.zip  = share\, verified entry by entry           SEND THIS
```

## Redaction

Text is processed byte for byte (ISO-8859-1 view), so everything that is not replaced stays identical, including
UTF-8 text and line endings. There are three passes:

1. **In context** (29 line rules, each run only on lines that contain its trigger words). The rules cover:
   * `getprop` keys: serialno, mac/bt address, ssid, part/boot uuid, android_id, device_name, hostname.
   * Serial numbers: `SerialNumber:`, `serial_number=`, `POWER_SUPPLY_SERIAL_NUMBER=`, input-device `Uniq=` and
     `UniqueId`, input descriptors.
   * Wi-Fi SSIDs: `SSID: "..."`, `ssid=`, `SSID '...'`, config keys, `"name":BSSID`, NetworkInfo `extra`.
   * Accounts and names: `Account {name=...}`, `accountName=`/`userName=`, `UserInfo{0:<name>:...}`,
     `bluetooth_name="..."` and similar.
   * Secrets: passwords, psk, tokens, `Bearer`, `ya29.`.
   * Coordinates.
   * IMEI/MEID/IMSI/ICCID: by key, and Luhn-valid 15-digit numbers with a real reporting-body prefix.
   * Android ids and `android-<16 hex>` host names.
   * E-mail addresses, also on domains that start with a digit (`163.com`, `3m.com`) and URL-encoded as `%40` or
     `%2540`. HAL versions (`...@4.0-impl.so`), `@2x` asset names and Java `Object@hash.member` are not e-mails.
   * MAC addresses (unicast only).
   * UUIDs, except public constants: GPT types, ACPI/EFI GUIDs, the Bluetooth SIG base, AOSP audio effects, DRM schemes.
   * Global, unique-local and EUI-64 IPv6 addresses.
2. **Everywhere**: every value found in pass 1 is also replaced in all other files and spellings (MAC/UUID without
   separators, any case). Plain words, short numbers and package/host names are only replaced in context, so a
   value like `default` cannot wipe out unrelated text. The exception is a Wi-Fi name of 6 or more letters: Android
   also logs it bare (`Adding/Updating network <ssid>`), so it is replaced as a whole word everywhere. `-RedactTerm`
   words are replaced everywhere.
3. **Final check**: all rules and all known values are searched again. If anything is left, the zip is **not**
   made (exit code 3), and the report lists the file and line (never the value).

Kept on purpose:
* Android's placeholder MAC `02:00:00:00:00:00`, multicast and broadcast addresses, `<unknown ssid>`.
* PCI addresses used as USB root-hub serials.
* The kernel release that the VM's `test_power` battery reports as its "serial".
* Kernel banner addresses such as `dm-devel@lists.linux.dev`.
* HAL names such as `android.hardware.audio@7.1`, and WorkManager ids such as `...systemjobscheduler@com.google...`.

Not handled, so say this when you ask someone to post logs:
* private IPv4 addresses;
* Bluetooth device names outside `*_name=` fields;
* free text that apps log;
* any other personal word (use `-RedactTerm` for these).

A display's EDID-derived `uniqueId` is replaced (treated like a serial), and so is a password field even when Android
already wrote it as redacted.

Speed: about 0.4 to 0.8 s per MB end to end (copy, three passes, zip). 8 boots of 5 snapshots (about 90 MB) take
roughly a minute.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | unexpected error (message printed) |
| 2 | no stick; refused (internal disk, the Windows disk, two `EFI-SYSTEM`); ambiguous; or `\gblog\` missing or empty |
| 3 | the final redaction check failed: the share copy is kept for inspection, no zip |
| 4 | the copy could not be made or verified |
| 5 | administrator rights needed (restarted elevated, or the command was printed) |

## If the automatic way does not work

Assign a letter yourself in an administrator prompt, run with `-SourcePath`, then remove the letter again:

```
diskpart
  list disk
  select disk <n>          (the USB stick: check the size!)
  list partition
  select partition 1       (EFI-SYSTEM, about 512 MB)
  assign letter=S
  exit
powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-GooglebookLogs.ps1 -SourcePath S:\
diskpart  ->  select disk <n>  ->  select partition 1  ->  remove letter=S
```

While a drive letter is assigned, Windows may create a `System Volume Information` folder on that partition. That is
harmless for Googlebook, but it is why the script prefers the letter-less volume path.

## Self-tests

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Test-GetGooglebookLogs.ps1            # about 1 minute
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Test-GetGooglebookLogs.ps1 -KeepWork  # keep work\selftest-*
```

The test sets `GB_LOGS_BLOCK_REAL_DISKS=1`, which makes every function that would touch a real disk, volume or mount
point throw; the tests replace those functions with mocks. Everything is written to `work\selftest-<stamp>\` and
deleted at the end. Covered:

* **A** parse, ASCII, the C# compiles, the real-disk guard.
* **B** the IOCTL buffer parsers against synthetic `winioctl.h` layouts: GPT names/offsets/types, MBR, truncation,
  storage descriptor without reading the serial, geometry, volume extents.
* **C** stick selection: USB vs internal NVMe; internal dual-boot disk refused; two sticks + `-DiskNumber`; missing
  `super`/`boot_a`; two `EFI-SYSTEM`; a Windows-To-Go disk; MBR/unreadable disks; case-sensitive names; SD, SATA, VHD
  and unknown bus types.
* **D** volume path first. Access denied without admin: stops, nothing mounted. As admin, a temporary letter: the
  highest free letter, checked to point at the partition, removed also when the copy throws, a failed removal
  reported. No volume: a partition access path. No `gblog`.
* **E** timestamp folders never reused; `-SourcePath` forms including `\\?\`; a verified read-only copy (source
  hash, time and attributes unchanged); the copy never overwrites; file kinds.
* **F** redaction with invented values (documentation MACs/IPv6, example.com, the test IMEI). Checks:
  * nothing is left, and every line is changed only through placeholders;
  * a file of look-alikes stays byte-identical;
  * placeholders are consistent across files and spellings, and `-RedactTerm` never matches inside a placeholder;
  * bytes are preserved (UTF-8, 0xFF, CRLF);
  * audio and binary files are handled as described above;
  * the final check catches a value that was put back;
  * the report has no values, and the private map has them.
* **G** real and realistic inputs, each run fully through `-SourcePath`:
  * the VMware native-smoke `gblog` examples (`C:\GB-build\native\dgpu\1b\evidence\w2\vm1`, `...\w2fix\vm1`,
    `...\w3\evidence\build\vm1`, `vm1b`);
  * the VM's verify logcat and serial log (`D:\GB-vm-backup\w4-upgrade-20261026-1741\verify`);
  * the sibling analyzer's synthetic SB3 fixtures.

  Checks:
  * the private copy equals the input (SHA256SUMS), the input is untouched, and the copies are read-only;
  * the zip contents are right, and the line count is the same;
  * every changed line carries a placeholder, and the triage summary block is unchanged;
  * look-alikes are kept;
  * the Google account in the VM logcat is gone (checked by value, never printed), and so are the fixtures'
    invented serials, account and Uniq ids.

  Changed lines are shown only as masked shapes.

  Wave-5 summary lines (2026-10-29; a one-off integration check, not a self-test case): the VM-1 snapshots with the
  summary that the wave-5 `GB_BOOTLOG_SH` (`cd94022f`) prints for the same logs (`kernel log:`, `deferred probes`,
  `buttons:`, `posture:`, `posture override:`, `audio dsp_driver=`, `i915 white veil: ... hwc: ...`, `i915
  PSR/underrun lines:`, `wifi: ... link:`, `bluetooth:`, `camera:`, `linker:`) went through `-SourcePath`. The
  redaction changed 0 summary lines in all three snapshots, and the analyzer gives the same 39 statuses on the share
  copy as on the private copy. The regenerated analyzer fixtures (now with gb_hw's `buttons`/`posture` lines and two
  `gpio-keys` devices) pass group G unchanged (114 passed, 1 skipped). Review 2026-10-30: the fixtures now carry every
  one of those summary lines (`make_fixtures.py` writes the whole `cd94022f` summary); group G still passes (114
  passed, 1 skipped, `summary block: 0` changed lines in every fixture), and `-SourcePath` on `sb3_pass`, `sb3_fail`
  and `sb3_detached` changes 0 of 332 summary lines (0 of 72 wave-5 lines), gives the same 39 statuses on the share
  copy, and no invented secret reaches the share copy's triage.
* **H** end-to-end with a mocked stick (a `\\?\` path to a fake volume): exit codes 0/2/3/5, the temporary-letter
  branch, `-ListOnly`, an empty `gblog`, output inside the source refused, quoting of the elevated relaunch command.
* **I** the real entry point in a child `powershell.exe -File` (exit codes).
* **K** review regressions (2026-10-29): crafted leaks (e-mail on `163.com`/`3m.com`, `%2540`, a value at a CRLF line
  end, a letters-only SSID logged bare) are gone and their look-alikes stay byte-identical; a letter whose mount call
  was interrupted (a mock error, and a real pipeline stop, which is what Ctrl+C does) is removed, a letter that
  belongs to another volume never is; a letter already on EFI-SYSTEM is pointed out with the `mountvol` command.
* **J** attaching a FAT32 image: **skipped**. Windows needs administrator rights to attach a VHD, so the IOCTL and
  volume-path code has not run against a real FAT32 volume here. The first real run on the stick is that test (use
  `-ListOnly` first).
