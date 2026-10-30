# Lock-screen PIN: the software Gatekeeper

Since wave 4 (2026-10) both trees, the VMware VM and the native Surface Book 3, carry **gb_gatekeeper**
(`android.hardware.gatekeeper-service.gb`, version 0.2.0). This is our own software implementation of Android's
Gatekeeper HAL. With it you can set a PIN, pattern or password in Settings and unlock with it.

It is a **convenience lock, not a hardware security boundary**. The Surface Book 3 and the VM have no Trusty/GSC
secure world. That means there is no hardware-backed key, no Weaver and no file-based encryption of /data. Read
[What it does not protect](#what-it-does-not-protect) before relying on it.

| | VMware VM | Native Surface Book 3 |
|---|---|---|
| Status | **works**. Tested in a throwaway VM before it shipped: set, change and clear a PIN, wrong-PIN throttling, unlock after a reboot during a lock-out, a service crash, damaged key files and each recovery path below. User data survived every recovery except the deliberate wipe. Then a long-running VM was upgraded and the PIN used on it | same binary, VINTF fragment and rc. **Untested on hardware** |
| Kill switch | none (clear the PIN before booting an image without it, [below](#before-you-go-back-to-an-older-image)) | same |

Sources, build and tests: [native/gatekeeper](../native/gatekeeper/). The complete design, the integration record,
the test plan and its results are in [native/dgpu/w4/GATEKEEPER.md](../native/dgpu/w4/GATEKEEPER.md) (a working
document; its section numbers are cited below as "design 8.2" and so on).

## How it works

* **AOSP's own logic.** The service compiles AOSP **libgatekeeper** (`system/gatekeeper`, android17-release) in
  unmodified. That library provides the enroll and verify logic, the password-handle format, the auth-token minting
  and the throttling table. Our code is the glue around it.
* **Password signature.** Each password signature uses **scrypt (N=16384, r=8, p=1)** over
  `version | user id | flags | password`, with the same parameters as AOSP's nonsecure Gatekeeper. The scrypt result
  is then signed with **HMAC-SHA256 under a per-device random key, K_dev**.
* **The per-device key.** K_dev is 32 random bytes, created at the first enrollment on this /data and stored twice:
  `/data/vendor/gatekeeper/gk_key.v1` and `gk_key.v1.bak`. Both files are mode 0600, owner `hsm`, in a 0700
  directory with SELinux label `gatekeeper_vendor_data_file`. The key is never regenerated while a copy is readable,
  and a damaged copy is restored from the other one. No key is compiled into the binary.
* **Persistent throttling.** The service stores its failure records on /data (`fr.<uid>`), so a reboot or a service
  restart does not reset them. After a reboot the timers run from boot, the way Android's own LockSettingsService
  limiter works. The counters are kept, so **rebooting never shortens a lock-out** and never adds a tier. The
  waiting time after *n* failed attempts:

  | Failed attempts | 1-4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20+ |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | Wait | none | 1 min | 5 min | 15 min | 30 min | 90 min | 4 h | 12 h | 36 h | 4 d | 13 d | 41 d | 123 d | 1 y | 3 y | 9 y | no more attempts |

  Android 17's LockSettingsService also keeps its own persistent limiter with the same table. It counts **every**
  failed unlock, including errors from a broken Gatekeeper, even when the PIN was right.
* **Auth tokens.** The service takes part in the `ISharedSecret` negotiation with KeyMint (instance
  `ISharedSecret/gatekeeper`), so its auth tokens carry a MAC under the per-boot shared HMAC key.
* **Robust against updates.** BoringSSL and libc++ are linked statically. The only dynamic dependencies are LLNDK
  libraries (`libbinder_ndk libc libdl liblog libm`), so a lapis OTA that changes vendor libraries cannot break it.
* **Wiring.** The service runs as `user hsm` in `class early_hal` and is registered before keystore2 and gatekeeperd
  need it. Its `interface aidl` lines let servicemanager start it on demand. If it restarts, init also restarts
  gatekeeperd (`onrestart`), because gatekeeperd never fetches a new HAL binder.
* **Declared once.** lapis's Trusty Gatekeeper and its Weaver stay undeclared, as before. Our ODM VINTF fragment
  declares `IGatekeeper/default` and `ISharedSecret/gatekeeper` with `override="true"`, so a later lapis fragment
  cannot produce two declarations of the same HAL.
* **Checks.** `build_esp.py` refuses to build a tree when:
  * the binary does not match its `SHA256SUMS` and `ACCEPTANCE.txt` records;
  * the binary needs a non-LLNDK library;
  * the fragment is not the reviewed one;
  * the binary, the fragment and the rc block are not all present together.

  `verify_image.py` check **V29** applies the same rules to a built image or ESP.

## Setting the PIN

Use **Settings > Security > Screen lock** on your own profile.

**lapis runs in headless system user mode.** User 0 is a system user without a screen; it is what shows the login
screen ("User", "Use as guest", "Add user"). Your profile is a separate user, **user 10** on a VM set up as in
[VMWARE.md](VMWARE.md). To check, run `am get-current-user` after signing in. This has three consequences:

* The PIN belongs to that user. A shell command needs the user id:
  `cmd lock_settings set-pin --user 10 <PIN>`, `cmd lock_settings clear --old <PIN> --user 10`.
* After a reboot the profile starts locked. Pick your user on the login screen, then enter the PIN.
* The Gatekeeper logs the ids `100010` (the PIN protector, `100000 + user`) and `10` (the synthetic-password handle),
  not `100000` / `0`. Its records are `fr.100010` / `fr.10`, and LockSettingsService's counter is
  `/data/system_de/10/spblob/*.failure_counter`.

The service's own messages go to the kernel log: `dmesg | grep gb_gatekeeper` (on the VM also `serial0.log`). For a
quick health check, run `/vendor/bin/hw/android.hardware.gatekeeper-service.gb --selftest` as root.

## What it protects

* Someone at the lock screen without the PIN cannot get into your profile through the UI, and guessing is throttled
  (table above).
* A copy of Android's credential files alone (for example `/data/system_de` from a leaked backup or a system-uid
  compromise) is not enough for an offline PIN search, because each guess also needs K_dev.
* Apps' user-authentication-bound keys and the synthetic-password blob are released only after a successful
  Gatekeeper verification. Note that our software KeyMint does not check auth-token MACs (see below).

## What it does not protect

* **No hardware-backed key.** K_dev is a file on /data. Root, or anyone who can read the disk, has K_dev and the PIN
  handles together. That includes `lapis.bin` on the Windows host, the Surface's NVMe, a Linux boot stick and a copy
  of the disk. With both, an offline search of a 4-6 digit PIN is practical: scrypt slows each guess, but the PIN
  space is small.
* **/data is not encrypted.** Our fstab has no file-based encryption, so the PIN locks the UI, not the bytes. Every
  file on /data is readable from the host, from the disk or as root, without the PIN.
* **Root access is not gated by the PIN**: the VM's root serial console (`gbsh.py`) and anything running as root.
  ADB authorization is separate from the screen lock, so a host whose ADB key is authorized keeps adb access on a
  locked device.
* **SELinux does not separate them either.** The global mode is enforcing, but every type of this build is
  permissive through our `odm_sepolicy.cil` ([ARCHITECTURE.md](ARCHITECTURE.md)), so nothing beyond Unix
  permissions keeps other processes away from the key files.
* **The boot menu is open.** systemd-boot has `editor yes`: anyone at the machine can press `e`, add `init=` or
  `rdinit=` to the kernel command line and get a root shell without the PIN. The PIN does not protect against
  physical access.
* **No attestation and no StrongBox.** KeyMint is the AOSP pure-software KeyMint, which does not verify auth-token
  MACs. Google apps that need hardware key attestation keep refusing as before.
* **Factory reset protection (FRP).** The "previous screen lock" path cannot succeed after a factory reset, because
  K_dev is gone with /data. The Google-account path is unaffected.

Do not keep sensitive data on this system.

## If the PIN is refused

Find out which case it is first. Stop retrying when the lock screen shows an **error** rather than a countdown:
every attempt is counted, even with the right PIN (design 8.1).

| You see | Case | What to do |
|---|---|---|
| a countdown; you know the PIN | normal throttling | wait. On a VM that is shut down daily, the tiers from 12 h up need that much uptime. As root you can reset both limiters: `rm -f /data/vendor/gatekeeper/fr.* /data/system_de/<user>/spblob/*.failure_counter`, then reboot and unlock (design 8.2) |
| an error with the right PIN; `dmesg` shows `open /data/vendor/gatekeeper/gk_key.v1: Permission denied`, `crypto self-test FAILED` or a crash loop (`exiting with status 3`) | the service is broken or its files are unreadable. The summary line then says "damaged ... factory reset" too: **trust the `open ...:` line before it, this case needs no wipe** | fix the owner and mode (`chown -R hsm:hsm /data/vendor/gatekeeper; chmod 700` the directory, `chmod 600` the files, `restorecon -R`, `setprop ctl.restart gb_gatekeeper`), or boot a build with a working gb_gatekeeper. Then reset the counters as above (design 8.3) |
| gatekeeperd logs `Could not find Gatekeeper device` | a build without gb_gatekeeper was booted | boot a build with it again, then reset the counters (design 8.4) |
| `checksum mismatch` for **both** key copies, or the PIN is forgotten | real lock-out | copy your data out first: /data is not encrypted, so root or the disk gives you every file. Then wipe /data: VM off, `python -I wipe_data.py --yes` on the host; native: reformat `userdata` from the installer (design 8.5) |

Never delete `/data/vendor/gatekeeper` or the `gk_key.v1*` files.

## Before you go back to an older image

Clear the PIN (Settings > Security > Screen lock > None) before you boot any build without gb_gatekeeper. That
covers:

* a VM ESP from before wave 4;
* a native USB image, dual-boot export or installer ISO built before the Gatekeeper was added (wave 5);
* an old USB stick on an internal install.

Without the HAL every unlock fails, and each failure is counted. Also clear it before replacing the KeyMint, and
before any future gb_gatekeeper with a different signature scheme.

## Upgrading an existing VM

The VM gets the Gatekeeper with the rest of the VM tree: `build_esp.py` writes a new ESP into `lapis.bin`
(`GB_WRITE_LAPIS=1`, VM powered off). **Back up `lapis.bin` first.** The wave 4 procedure covers the backup, a
rehearsal on a throwaway VM, the checks after the first boot and the rollback paths. Once a PIN exists, roll back
only to an ESP that has gb_gatekeeper, or clear the PIN first. The procedure is in
[native/dgpu/w4/VM_UPGRADE.md](../native/dgpu/w4/VM_UPGRADE.md), with its tools in `native/dgpu/w4/tools/`.

Both write paths now check this themselves (wave 6): `build_esp.py` (`GB_WRITE_LAPIS=1`) and
`gb_vmbackup.py restore --esp-only` compare the "PIN path" of the ESP they would write with the ESP now on the
disk (the Gatekeeper binary and rc service, every VINTF fragment the image adds, the KeyMint service and its
libraries, the Trusty and Weaver masks, the fstab and the SELinux types the PIN path runs in) and refuse any
difference. A reviewed change needs `GB_PIN_MIGRATION=1` (build) or `--pin-migration` (rollback);
`verify_image.py --expect-pin-path <ESP>` shows the same comparison. `gb_vmbackup.py restore --esp-only --dry-run`
runs every check without writing.

## Limits and open points

* A later gb_gatekeeper must still verify the PINs enrolled with this one. `tests/frozen_v1.json` holds two key
  files and handles that version 0.2.0 enrolled; `tests/test_frozen_v1.cpp` verifies them through the service code
  in every build (host and Android) and `ACCEPTANCE.txt` must say `GK_FROZEN_V1: PASS`, or `build_esp.py` refuses
  the binary (wave 6; the 0.2.0 binary itself predates the test and is accepted by its pinned sha256). Eight
  deliberate scheme changes (scrypt N or r, key-file magic or name, HMAC hash, key length, salt, key checksum)
  all fail that test.
* The native tree is untested on hardware. An OTA with a PIN set has not been tested yet: the design keeps the
  handles and K_dev on /data and depends only on LLNDK, so it is expected to work.
* If file-based encryption is ever enabled (it needs a /data wipe), the design still holds. A lock-out would then
  lose credential-encrypted data.
* For an unreadable key file the summary log line says "damaged ... recovery: factory reset", the same as for real
  damage. Only the `open ...:` line before it tells the two apart. A later build should word it differently.

## Credits

AOSP: libgatekeeper, `hw_auth_token.h` and the libbinder_ndk platform headers, copied unmodified into
`native/gatekeeper/third_party/aosp/`. The nonsecure Gatekeeper and KeyMint served as design references (scrypt
parameters, shared-secret derivation). **gbOS86** ([gbos-x86](https://github.com/linuxkid473/gbos-x86)): its
Apache-2.0 `gatekeeper/soft_gk.c` showed that a hand-written stable-AIDL server over the libbinder_ndk C API works on
lapis. Its parcelable framing and meta-transaction codes are adapted in `src/aidl_service.cpp` and `src/binder_api.h`
and credited in [NOTICE](../native/gatekeeper/NOTICE). Its fixed keys, self-signed tokens and missing throttling were
not taken over.
