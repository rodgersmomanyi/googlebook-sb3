> **Repository note.** A wave 4 working document (2026-10-26), copied from the development tree. Paths such
> as `C:\GB-build\...` and `C:\GB-x86\...` are the development layout (`C:\GB-build\native\<x>` =
> `native/<x>`, `C:\GB-x86` = the repository root, `D:\GB-vm-backup`, `D:\gb-w4test` = local backup and
> throwaway-VM folders, `builder:` / `~/gk` = the Linux builder); `evidence/` and the planning session's own
> files are not published, and details of the developer's own files were removed. For users:
> [docs/GATEKEEPER.md](../../../docs/GATEKEEPER.md).

# Software Gatekeeper (lock-screen PIN) for the VM and native trees

Status (2026-10-26, about 17:45): **integrated into both trees (7.9) and REHEARSED ON A THROWAWAY VMWARE VM (9.6):
9.2 steps 1-10 incl. step 8 (W4-GK-1 confirmed on the device, both rounds) and every 9.5 test pass on the final
VM tree `1e939bc0...2934`; the recoveries 8.2-8.6 were performed and kept the user data.** Two pipeline defects found
by the rehearsal were fixed and re-rehearsed (`gb_kmsgdbg`: the service's stderr never reached dmesg on lapis's user
build, 7.3; `gb_zram`: lapis's own `modprobe zram` could win a load race and stop zram, VM_UPGRADE.md 2.2(a)).
Corrections from the device: lapis runs in headless system user mode, so the PIN belongs to **user 10** (uids
100010 / 10, 9.2 note), and the 8.1 table now quotes the measured messages. The user's VM and `lapis.bin` were not
started or written. No native image carries the Gatekeeper yet (HANDOFF.md).

**Reviewed 2026-10-26 (`REVIEW.md`):** sections 3, 4, 5, 6.2, 7.2, 7.5, 7.8, 8, 9 and 10 were corrected. The two
review points that matter most: LockSettingsService counts **every** Gatekeeper failure (errors and `RETRY` too) in
its own persistent limiter (5), and after a reboot with a pending lock-out the first attempt is refused and counted
once more (W4-GK-1, 7.8). Lock-out recovery is no longer "factory reset" first (8).

**Wave 4 code fixes, 2026-10-26 (gb_gatekeeper 0.2.0):** W4-GK-1 (post-reboot timer from boot), W4-GK-2 (internal
failures not counted) and W4-GK-3 (exit if IGatekeeper does not register) are implemented and tested **off-device**
(7.8, 9.1): host tests under ASan/UBSan and on lapis bionic, and the same tests fail on the reviewed pre-W4 sources.
**Independently verified the same day** (`REVIEW.md` section 7): the kept pre-W4 sources rebuild to exactly the
reviewed binary `b62b205c...a46e` (stripped and unstripped), every W4 test fails on them, the 0.2.0 binary
`505b404d...3d51` rebuilds identically from a third empty output directory, LockSettingsService's Android 17 limiter
was re-read against the new timer, and a reboot-loop test (`test_reboot_loop_no_bypass`) shows that rebooting never
shortens a lock-out.
**The user must still not set a real PIN before 9.2 step 8 has shown the W4-GK-1 result on the VM and the recovery
tests of 9.5 have run.** (2026-10-26: both done on the rehearsal VM, PASS, 9.6. What remains before the user's real
PIN: the upgrade of the user's VM itself, VM_UPGRADE.md 2.4-2.8 with V-8 there, and the post-upgrade ESP snapshot
2.9.)

User decision (2026-10-26): **yes** to a software lock-screen PIN, as a convenience lock, not a hardware security
boundary.

| What | Where |
|---|---|
| prototype sources, build script, tests | `C:\GB-build\native\gatekeeper\` (`src\`, `tests\`, `tools\`, `third_party\aosp\`, `build.sh`, `NOTICE`) |
| built binary (x86_64 Android, stripped, 312,400 B, version 0.2.0 = W4-GK-1..3) | `C:\GB-build\native\gatekeeper\out\android\android.hardware.gatekeeper-service.gb`, sha256 `505b404d0606bf80bcb41ff8a66a80c962934e3791d3b8db13645608da143d51` (reproducible: two clean builds, BoringSSL included, into different output directories are byte-identical, stripped and unstripped; `out\repro\`). Superseded: 0.1.0, `b62b205c...a46e` (the reviewed build; its sources are kept in `tests\regress\pre_w4_src\`) |
| build-time acceptance record (VM_UPGRADE.md P5) | `C:\GB-build\native\gatekeeper\out\ACCEPTANCE.txt` |
| rc fragment | `C:\GB-build\native\gatekeeper\rc\gb_gatekeeper.rc` |
| VINTF fragment | `C:\GB-build\native\gatekeeper\vintf\android.hardware.gatekeeper-service.gb.xml` |
| component manifest draft | `C:\GB-build\native\gatekeeper\component\gatekeeper.json` (the reviewed draft, unchanged) |
| integrated component (both trees, 7.9) | `C:\GB-x86\inject\native\components\gatekeeper.json` (= the draft except its comment) |
| builder work dir | `~/gk/` on the builder (sources fetched from android.googlesource.com, extracted images, build tree `~/gk/proto/gatekeeper`) |

Tags: **INFERRED** marks a statement that is reasoned, not measured or read in source. File:line references are to
the AOSP branches and commits in section 12; `lapis` facts come from the lapis images extracted on the builder.

---

## 1. Decision

Build our own small HAL, **`android.hardware.gatekeeper-service.gb`**, out of AOSP pieces, and keep its state on /data:

* AOSP **libgatekeeper** (`system/gatekeeper`, android17-release), compiled in from source, unmodified: the enroll /
  verify / throttling logic, the `password_handle_t` format and the auth-token minting are Google's own.
* the **password signature** of AOSP's nonsecure Gatekeeper (scrypt N=16384 r=8 p=1 over
  `version|user_id|flags|password`), wrapped in **HMAC-SHA256 under a per-device random key K_dev** that is generated
  once and stored in `/data/vendor/gatekeeper` (section 6);
* **persistent failure records** (throttling survives reboots and service restarts; after a reboot the HAL timer
  runs from boot like LockSettingsService's, W4-GK-1 in 7.8, so a reboot no longer adds a throttling tier, section 5);
* an **`ISharedSecret/gatekeeper` participant** with the AOSP derivation, so the auth tokens carry a MAC under the same
  per-boot key as the KeyMint's (section 3);
* **BoringSSL** (AOSP `external/boringssl`) and libc++ linked statically; dynamic dependencies are **only LLNDK
  libraries** (`libc libm libdl liblog libbinder_ndk`), so a lapis OTA that changes `/vendor` libraries cannot break it;
* the stable-AIDL wire protocol written by hand against the libbinder_ndk C API (approach and parcelable framing
  adapted from gbOS86 `gatekeeper/soft_gk.c`, Apache-2.0, credited in `NOTICE`).

### 1.1 Why not the three options as given

| Option | Found / tried | Why not as-is |
|---|---|---|
| (i) graft AOSP's software Gatekeeper from the Android 16 emulator image of our KeyMint | **Present.** `/build/emu36/x86_64/vendor.img` (zip `x86_64-36_r02.zip`, build `BE2A.250530.026.D1` / 13818094, the image `/build/km` and `inject\keymint` come from) has `/apex/com.android.hardware.gatekeeper.nonsecure.apex` (4,612,096 B, sha256 `acc7491b41633ec3d06996acee1144977120e94e4c191741d236c51873ada1e6`). Payload: `bin/hw/android.hardware.gatekeeper-service.nonsecure` + 15 libs, VINTF `IGatekeeper/default` + `ISharedSecret/gatekeeper`, rc `class early_hal`, `user system`. Its `libkeymaster_portable`, `libcppbor`, `libcppcose_rkp`, `lib_android_keymaster_keymint_utils` are byte-identical to our KeyMint graft (sha256 prefixes `5243bf5c`, `fde9d5fb`, `4991d3df`, `7d248b2b`); its `libcrypto.so` (`c1a4e109`) is the APEX variant, not the one we overlay (`inject\keymint\lib64\libcrypto.so` = `/build/km_libcrypto.so`, `5bb2091c`). How the KeyMint was taken: `vendor.img` partition 1 -> `/build/km/{bin,lib64}` (also `inject\km.tgz`) -> `inject\keymint\`, read by `build_esp.py:106-115`. The Android 17 desktop emulator (`C:\GB-x86\emu`, `CLE3.260929.004`) ships the same APEX. | (a) It is a `"vendorBootstrap": true` APEX (`manifest.json`): apexd-bootstrap activates `/vendor/apex` at early-init, **before** our `/vendor` overlay exists (the same reason the strongbox APEX needs a bind mount in gb.rc), so the APEX itself cannot be added. (b) Taken out of the APEX, the binary needs lapis's non-LLNDK vendor C++ libraries (`libbase`, `libutils`, `libcutils`, `libc++`, `libgatekeeper`, `libhardware`, the `-V1-ndk` interface libs), whose ABI is not stable across lapis OTAs. libgatekeeper's ABI already changed between 16 and 17: `virtual uint32_t ComputeRetryTimeout` became `uint64_t` (`system/gatekeeper` `include/gatekeeper/gatekeeper.h:176`, diff of the two branches). A private library directory would rest on `LD_LIBRARY_PATH` in a vendor namespace (INFERRED to work, untested). (c) Its throttling is in memory only (`SoftGateKeeper.h:119-138` `failure_map_`) and it caches the HMAC key once (`SoftGateKeeper.h:68`), so a keystore2 renegotiation is never seen. |
| (ii) build AOSP's software Gatekeeper (`hardware/interfaces/gatekeeper/aidl/software` + `system/gatekeeper`) with the NDK | Sources fetched (android16-release and android17-release; the logic is identical, only `Android.bp` and an error helper differ). | The NDK has no `aidl` compiler, and the generated `android.hardware.gatekeeper-V1-ndk` / `sharedsecret-V1-ndk` libraries on the device expose a C++ ABI tied to the vendor `libc++`; it also needs `libbase` and `libkeymaster_portable`. We take its **logic** instead: libgatekeeper from source, its scrypt parameters, its shared-secret algorithm and its AIDL error mapping. |
| (iii) adapt gbOS86 `soft_gk.c` (SPDX Apache-2.0, 331 lines) | Read in full (clone HEAD `f8a5c1a`, file at `03a6a46`). It proves the hand-written AIDL server works on lapis (gbOS86 HANDOFF.md:69-74, README "Screen-lock password"). | Fixed compile-time keys (`soft_gk.c:88-89`), auth tokens under its own key (`:245`), no throttling, `hardware_backed = 1`, no ISharedSecret, links `/vendor/lib64/libcrypto.so` (not LLNDK; ours is the overlaid Android 16 one). We keep its idea and framing only. |

---

## 2. How Android 17 uses Gatekeeper on a device without Weaver

Sources: `frameworks/base` `services/core/java/com/android/server/locksettings/` (android17-release).

**Weaver absent.** gb.rc binds an empty manifest over lapis's Weaver fragment, so `getWeaverService()` returns null
("Device does not support weaver", `SyntheticPasswordManager.java:678`). Every LSKF protector then takes the
Gatekeeper branch.

**Setting the PIN** (`setLockCredentialWithSpLocked`, `LockSettingsService.java:3428`):

1. `createLskfBasedProtector` (`SyntheticPasswordManager.java:1082-1170`), Gatekeeper branch (`:1119-1158`):
   `gatekeeper.clearSecureUserId(fakeUserId(u))`, then
   `gatekeeper.enroll(fakeUserId(u), null, null, stretchedLskfToGkPassword(stretchedLskf))` with
   `fakeUserId(u) = 100000 + u` (`:2141-2143`). `sid = sidFromPasswordHandle(handle)`; the protector secret is
   `transformUnderSecdiscardable(stretchedLskf, secdis)` (a 16 KiB random file); the handle goes to the protector's
   `pwd` file.
2. `createSyntheticPasswordBlob(..., sid)` -> `SyntheticPasswordCrypto.createBlob` (`SyntheticPasswordCrypto.java:189-207`):
   an AES-GCM key in AndroidKeyStore with `setUserAuthenticationRequired(true)`, `setBoundToSpecificSecureUserId(sid)`,
   `setUserAuthenticationValidityDurationSeconds(15)` (`:62`). **The synthetic password can only be decrypted within
   15 s of a Gatekeeper auth token for that SID.**
3. `newSidForUser` (`SyntheticPasswordManager.java:915-925`): `gatekeeper.enroll(u, null, null, sp.deriveGkPassword())`,
   the "SP handle" whose SID binds apps' user-authentication-bound Keystore keys.

**Unlocking** (`unlockLskfBasedProtector`, `:1534-1690`):

1. `gatekeeper.verifyChallenge(fakeUserId(u), 0, handle, gkPassword)` (`:1605-1606`). gatekeeperd forwards the
   token it gets to keystore2 `IKeystoreAuthorization.addAuthToken` (`system/core` `gatekeeperd/gatekeeperd.cpp:428-456`),
   but only if the AIDL token's `mac` is exactly 32 bytes (`authToken2AidlVec`, `:97`; otherwise the payload is empty
   and nothing reaches keystore2).
2. `unwrapSyntheticPasswordBlob` -> keystore2 picks the newest token for the SID and type, fresh by keystore2's own
   receipt time (`keystore2/src/enforcements.rs:606-640`), and hands it to KeyMint `begin`.
3. `verifyChallenge(gatekeeper, sp, 0, u)` with the SP handle (`:1876-1925`); its result becomes the unlock's
   response, and a failure there is `OTHER_ERROR`, so a broken SP handle fails the unlock too (INFERRED from how the
   callers use `result.response`).
4. `unlockKeystore` (`LockSettingsService.java:1652-1655`: `onDeviceUnlocked(u, SP-derived password)`) and
   `unlockCeStorage` (`:2427-2435`, vold).

**gatekeeperd** (`gatekeeperd.cpp`, runs as `user system`, `class late_start`, lapis `system/etc/init/gatekeeperd.rc`):
if `IGatekeeper/default` is declared it **waits for it forever** in its constructor (`:114-128`); it calls
`deleteAllUsers` on its first enroll after `/data/misc/gatekeeper` was created (`clear_state_if_needed`, `:142-157`);
it never re-fetches the HAL binder. LockSettingsService itself waits for gatekeeperd (`getGateKeeperService`,
`LockSettingsService.java:3141-3160`, `ServiceManager.waitForService`) and does so during every boot, also without a
PIN. So **a declared but absent Gatekeeper hangs boot**; section 4 lists what prevents that.

---

## 3. KeyMint, auth tokens and the shared HMAC key

* **Our KeyMint does not check token MACs.** The grafted pure-software KeyMint 4 (`libpuresoftkeymasterdevice`,
  `system/keymaster` android16) uses `SoftKeymasterEnforcement`, whose `ValidateTokenSignature` returns `true`
  (`include/keymaster/km_openssl/soft_keymaster_enforcement.h:39`). It still checks the SID (`user_id` or
  `authenticator_id` equal to the key's), the authenticator type mask and, for timeout keys, `timestamp + timeout`
  against **CLOCK_BOOTTIME** in ms (`android_keymaster/keymaster_enforcement.cpp:484-536`). So for the credential flow
  the token needs: the right SID, `authenticatorType = PASSWORD`, a CLOCK_BOOTTIME timestamp, and a 32-byte MAC
  (for gatekeeperd). Even a MAC under a wrong key (gbOS86's `auth_key`) would pass today's KeyMint; gb_gatekeeper
  MACs correctly anyway (next point), so its tokens also verify under a KeyMint that checks MACs.
  **That does not make a KeyMint swap safe (review GK-5).** Every existing Keystore key blob belongs to the KeyMint
  implementation that created it, including the auth-bound key that wraps LockSettingsService's synthetic-password
  blob (`SyntheticPasswordCrypto.java:189-207`). Replacing the KeyMint (the Rust nonsecure one, another graft) means
  the right PIN can no longer unwrap the SP, i.e. a lock-out, and app keys are lost with or without a PIN (INFERRED:
  blob formats are implementation-specific). A KeyMint change is its own migration: clear the PIN first, accept
  the loss of app keys, plan it separately.
* **A verifying KeyMint would need the shared key.** keystore2 lists every declared `ISharedSecret` instance
  (`keystore2/src/shared_secret_negotiation.rs:116-151`, `get_declared_instances`), connects in a background thread with
  1 s retries until all answer (`:153-228`) and only logs a mismatch (`:270-279`). The software KeyMint's agreement key
  is 32 zero bytes (`soft_keymaster_enforcement.cpp:40` `kFakeKeyAgreementKey`); AOSP's nonsecure Gatekeeper uses the
  same (`hardware/interfaces/gatekeeper/aidl/software/SharedSecret.cpp:77`). gb_gatekeeper declares and serves
  `ISharedSecret/gatekeeper` with the same CKDF (`src/shared_secret.cpp`), so its tokens also verify under a KeyMint
  that checks (the Rust nonsecure one, or a later graft). H is derived from public nonces and a public constant: it is
  interoperability, not secrecy. Until keystore2 has negotiated, tokens are MAC'd with 32 zero bytes (logged once).
* **UNLOCKED_DEVICE_REQUIRED keys** are keystore2's business since Android 14: super keys unlocked with the SP-derived
  password LockSettingsService passes to `onDeviceUnlocked` (`keystore2/src/super_key.rs:107-123, 274-281`;
  `authorization.rs:162-180`); only the biometric unlock path uses auth tokens (FINGERPRINT type). No Gatekeeper token
  is involved, so nothing else is needed for them.
* **Apps' user-auth-bound keys** get their token from the SP-handle verify (step 3 above): with gb_gatekeeper they work
  (correct SID, type, clock). VM_UPGRADE.md 2.2(c) listed "user-auth-bound Keystore keys may fail" as a gbOS86 limit;
  it does not apply here (the review rewrote 2.2(c) and V-9 accordingly).

---

## 4. Persistence: what a change in Gatekeeper state does (lock-out = data loss)

**Today /data is not encrypted.** `inject\fstab.android-desktop` has no `fileencryption=` on the userdata line, and
vold's `fscrypt_unlock_ce_storage` returns `true` at once when FBE is off (`system/vold` `FsCrypt.cpp:901-903`). So a
lock-out today locks the UI, not the bytes: the files stay readable to root and from the host. If FBE is enabled later
(a /data wipe, out of scope here), a lock-out makes credential-encrypted storage unrecoverable.

| Event | Effect | Mitigation in this design |
|---|---|---|
| K_dev lost or replaced while handles exist | both handles (uid `100000+u` and `u`) stop verifying, no token, no SP: **locked out**. With FBE: CE data lost. (The software KeyMint would accept a forged token, so a root tool holding the right PIN could in principle decrypt the SP blob; INFERRED, not a plan.) | K_dev is created only if both copies are absent, a damaged copy is restored from the other, never regenerated; atomic writes; verify never creates a key (section 6). Recovery: copy the data out, then wipe (8.5). Most "key unavailable" states are not a loss: read 8.1 first. |
| the signature scheme changes in a later gb_gatekeeper | same as above | the scheme is part of the on-disk contract (`gk_key.v1`, "GBGKKEY1"); a change needs a new file version and verify support for the old one. Never change `kScryptN/R/P` or the HMAC step. |
| gb_gatekeeper does not start (declared, not registered) | gatekeeperd waits forever, LockSettingsService waits for gatekeeperd: **boot hang** (section 2) | LLNDK-only, statically linked binary (an OTA cannot break its libraries); registers even if its crypto self-test fails (then refuses enroll/verify); never exits on a storage error; `interface aidl` lines let servicemanager start it on demand. If `IGatekeeper/default` is refused after 5 attempts (review GK-8), 0.2.0 **exits with status 3** (W4-GK-3, 7.8) instead of idling: init restarts it, so a transient refusal heals and a lasting one is a visible crash loop (`... not registered: exiting with status 3` in dmesg) instead of a silent hang; the boot still waits while it fails. ISharedSecret is registered only after IGatekeeper succeeded; its failure alone is logged and IGatekeeper served. No wipe follows a hang: RescueParty is off in both trees. |
| gb_gatekeeper restarts during a boot | gatekeeperd keeps a dead binder; H is gone from memory | `onrestart restart gatekeeperd` (LockSettingsService re-links by itself); tokens fall back to the zero MAC key until reboot, which our KeyMint does not check. An unlock in flight fails and is counted by LockSettingsService (next row). |
| gb_gatekeeper running but refusing every request (self-test failed, key files unreadable: wrong owner/mode/label, EIO; a crash loop) | every unlock attempt, even with the right PIN, is an error, and **LockSettingsService counts each one in its persistent limiter** (section 5): 5 attempts = 1 min, then 5 min, 15 min ... The counter survives the repair | fail-closed: never creates a key on these errors, never counts them in the HAL. Recovery without a wipe: 8.3 (repair) then 8.2 (clear both counters). Don't let the user keep retrying while it is broken. |
| reboot while a lock-out is pending (HAL failure count >= 5 not yet cleared by a success) | up to 0.1.0: libgatekeeper restarted the full timeout at the first attempt after boot and answered `RETRY`, also for the right PIN; LockSettingsService let that attempt through (its timer restarts from boot, it cannot query a Gatekeeper timeout) and counted the `RETRY`: **one more tier per such reboot**. On a VM shut down daily, tiers of 12 h+ need that much uptime | **0.2.0: W4-GK-1 (7.8)**, the HAL timer runs from boot after a new boot (boot_id changed), counters kept, so an attempt LockSettingsService lets through reaches the password check (host-tested; on the VM: 9.2 step 8). A 0.1.0 tree, or if step 8 shows the old result: 8.2. |
| two `IGatekeeper/default` declarations (an OTA renames lapis's Trusty fragment, so gb.rc's empty bind misses it) | libvintf fails the whole device manifest, "Conflicting FqInstance" (`system/libvintf` `HalManifest.cpp:104-165`): no vendor AIDL HAL can register | our ODM fragment declares the HAL with `override="true"`, which removes the vendor's instance first (`HalManifest.cpp:215-235`). |
| a vendor fragment that declares `ISharedSecret/gatekeeper` too (lapis's `vendor_service_contexts:2` already labels that name, so a later lapis may declare it) | same as the row above: `addDirectoryManifests` returns the first error and the whole device manifest fails (`VintfObject.cpp:234-266`): boot failure. No wipe: RescueParty and its factory reset are disabled in both gb.rc (`build_esp.py:917-919, 1556-1558`) | `override="true"` on the sharedsecret HAL too (review GK-6, 7.2). |
| booting any tree without gb_gatekeeper while a PIN is set (an ESP-only rollback to a pre-W4 ESP, the pre-W4 fallback images in `D:\GB-iso\prev\`, an old USB stick on the internal install) | Gatekeeper undeclared: every unlock is an error (and counted, section 5): **locked out**. K_dev, handles and data are intact | clear the PIN first (8.6). Recovery without a wipe: boot a tree with the same gb_gatekeeper again (8.4), then 8.2. |
| lapis OTA (system/vendor) | gb_gatekeeper does not depend on vendor libraries; LLNDK ABIs are stable; handles and K_dev are on /data | wave 4 adds porting checks (section 7.5): `hal_gatekeeper_default` still in the policy, the Trusty paths unchanged. |
| KeyMint key blobs after a patch-level change | keystore2 upgrades the protector key (existing behaviour, unrelated to Gatekeeper) | none needed |
| KeyMint **implementation** replaced (section 3) | the SP blob cannot be unwrapped: with a PIN, the right PIN no longer unlocks; app keys lost either way (INFERRED) | a separate, planned migration with the PIN cleared first |
| ISharedSecret participant broken (gb_gatekeeper errors in `getSharedSecretParameters` / `computeSharedSecret`) | keystore2 retries phase 1 forever and stops phase 2 at the first error (`shared_secret_negotiation.rs:230-275`), so the KeyMint may never get its shared key. Background thread: no boot block; harmless with today's KeyMint (no MAC check) | 9.2 step 3 checks "concluded successfully" |
| factory reset / `wipe_data.py` | /data is formatted: K_dev, failure records, handles and LockSettings state go together | consistent by construction |
| FRP | LockSettingsService copies the LSKF handle to the persistent data block (`synchronizeGatekeeperFrpPassword`, `SyntheticPasswordManager.java:1302`; `config_enableCredentialFactoryResetProtection` true, `core/res/res/values/config.xml:1536`, and **measured true** in lapis R1 `framework-res.apk`). After a factory reset K_dev is gone, so the "previous screen lock" FRP path cannot succeed; the Google-account path is unaffected. No `ro.frp.pst` was found in lapis's vendor or system `build.prop`, and SetupWizard is disabled in our trees, so FRP may never be asked (INFERRED). | documented trade-off of the device key (section 6.3) |

---

## 5. Throttling and its persistence

* AOSP's nonsecure Gatekeeper keeps failure records **in memory** (`SoftGateKeeper.h:119-138`): a reboot or a crash of
  the service resets them.
* libgatekeeper android17 increments the record **before** checking the password (`gatekeeper.cpp:152`); the timeout
  after n failures is 0 for n = 1-4, then 1 min, 5 min, 15 min, 30 min, 90 min, 4 h, 12 h, 36 h, 4 d, 13 d, 41 d,
  123 d, 1 y, 3 y, 9 y for n = 5-19, and from n = 20 on no attempt is allowed (`gatekeeper.cpp:307-366`). A request with a timestamp at or before the stored
  one ("device was rebooted", `:355`) restarts the full timeout and is not counted.
* LockSettingsService has its own **SoftwareRateLimiter** (Android 17): same table (`SoftwareRateLimiter.java:107-128`),
  a persistent counter file per protector (`/data/system_de/<u>/spblob/<protectorId %016x>.failure_counter`,
  `SyntheticPasswordManager.java:160, 2289-2310`, `LockSettingsStorage.java:482, 494`), timeouts restarted at boot
  (`SoftwareRateLimiter.java:276-300`), a missing or damaged counter read as zero. It enforces when
  `config_softwareLskfRateLimiterEnforcing` is true: AOSP default true (`config.xml:1549`), **measured true** in lapis
  R1 `system/framework/framework-res.apk`, not overridden by the vendor RRO (aapt2 36.1.0 dump; product overlays not
  extracted, INFERRED unchanged); `dumpsys lock_settings` prints `Enforcing:`.
* **On a Gatekeeper device LockSettingsService counts every non-match, not only wrong PINs (review GK-1).**
  `reportResultToSoftwareRateLimiter` sends every non-matching response to `reportFailure`
  (`LockSettingsService.java:2723-2747`), which increments the persistent counter "regardless of whether the failure
  is a certainly wrong guess ... Gatekeeper only ever returns generic failures" (`SoftwareRateLimiter.java:438-446`).
  So a wrong PIN, a Gatekeeper `RETRY`, a service error ("password key unavailable", self-test failed) and a
  binder `RemoteException` all count. Duplicate wrong guesses are **not** dropped here: only a `credIncorrect`
  response is remembered for duplicate detection, and only Weaver's `INCORRECT_KEY` produces one
  (`SyntheticPasswordManager.java:783`). Consequence: while gb_gatekeeper refuses for any reason, each attempt with
  the right PIN escalates LockSettingsService's timeouts, and the escalation survives the repair (recovery 8.2).
* **Reboot interaction (review GK-2), fixed in 0.2.0 (W4-GK-1, 7.8).** After a reboot LockSettingsService restarts
  its timeout from boot and cannot ask a Gatekeeper for its remaining time (`SyntheticPasswordManager.java:2361-2370`,
  Weaver only). libgatekeeper restarts the full timeout at the first attempt whose timestamp is at or before the
  stored one (`:355`, second bullet of this section), which after a reboot is almost always the case. Up to 0.1.0 the
  first attempt that LockSettingsService let through after boot was therefore refused with `RETRY` and counted:
  **one more tier per reboot** while the HAL count was >= 5 and not yet cleared by a success, even with the right
  PIN. AOSP's nonsecure HAL does not do this (in-memory records); Trusty devices do. 0.2.0 compares the kernel
  boot_id with the one stored in `/data/vendor/gatekeeper/boot_id` at its first record load; after a new boot it sets
  every record's `last_checked_timestamp` to 0 (counters kept), so the HAL timeout also runs from boot and is never
  longer than LockSettingsService's (same table, a counter at least as high, since LockSettingsService counts errors
  too).
* A **crypto failure** during verify (scrypt's 16 MiB allocation failing) was counted as a wrong guess by the HAL up
  to 0.1.0: libgatekeeper writes the incremented record before it computes the signature (`gatekeeper.cpp:150-157`),
  the adapter then returned an error, and LockSettingsService counts that error too. 0.2.0 (W4-GK-2, 7.8) puts the
  record back as it was before the request, on disk and in memory; LockSettingsService still counts the error (its
  business, GK-1).
* **gb_gatekeeper persists its records** (`/data/vendor/gatekeeper/fr.<uid>`), like Trusty's secure storage does, so
  crashing the HAL or rebooting does not reset the HAL-side limiter (a reboot resets only the timer's start, never a
  counter). A missing or damaged record reads as "no failures" (as LockSettingsService does with its counter); a
  failed write is logged and the in-memory record stays authoritative, so a full disk never blocks an unlock.
  Measured in the host tests (0.2.0, `test_throttling`): failures 1-4 refused, the 5th returns a 60,000 ms timeout, a
  correct PIN 1 s later is refused with 59,000 ms left; a restart of the service in the same boot (3 s after the 5th
  failure) keeps the timer (57,000 ms); a "reboot" (new boot_id, clock back to 50 ms) refuses with **59,950 ms**, the
  time left since boot (0.1.0: the full 60,000 ms), counter still 5, timestamp 0 on disk; a service restart later in
  that boot (30 s) gives 30,000 ms; at 60,001 ms after boot the correct PIN unlocks and clears the record; five more
  wrong guesses and one after the minute give the 6th failure with 300,000 ms. A missing boot_id file, a damaged one
  and an unreadable kernel boot_id each count as a new boot (counters 6 / 7 / 8: 299,000 / 898,000 / 1,795,000 ms
  left 1 / 2 / 5 s after boot, i.e. from boot).
* **Rebooting cannot shorten a lock-out** (`test_reboot_loop_no_bypass`, added by the verification): a new boot
  changes only the timer's origin, never a counter, and an attempt refused with `RETRY` is not counted. Ten reboots
  in a row with the right and a wrong PIN before 60 s from each boot: every attempt refused with the time left since
  that boot, counter 5 throughout; at 59,999 ms after the next boot still refused, at 60,000 ms a wrong PIN reaches
  the check (6th failure, 300,000 ms); ten more reboots and three starts with the `boot_id` file deleted keep counter
  6; the right PIN is accepted only at 300,000 ms after the last boot. Since every boot comes after the last counted
  failure, "the full tier from boot" always ends later in real time than "the full tier from that failure". The same
  holds for LockSettingsService, whose counter is persistent and whose timer also starts at boot
  (`SoftwareRateLimiter.java:168-172, 276-300, 316-321`).
* **What LockSettingsService expects, re-read for 0.2.0 (Android 17 sources, section 12):** at its first use after
  boot it rebuilds each protector's state from the persistent counter with `timeSinceBootOfLastFailure = 0`
  (`SoftwareRateLimiter.java:276-300`, "the timeout resets to its original value ... upon reboot. That matches what
  typical hardware rate-limiter implementations do"), so its lock-out ends at T(n) after boot (`:316-321`); `apply`
  refuses earlier attempts itself (`:240-248`) and lets later ones through to the HAL; it asks a hardware limiter for
  its timeout only for Weaver (`SyntheticPasswordManager.java:2361-2370`); its clock is
  `SystemClock.elapsedRealtime()` (`LockSettingsService.java:679-680`), i.e. CLOCK_BOOTTIME, the HAL's clock. 0.2.0's
  HAL lock-out after a new boot also ends at T(n) after boot, and the HAL sees the attempt no earlier than
  LockSettingsService did, so with the HAL counter at most LockSettingsService's, an attempt it lets through is never
  answered `RETRY`.
* **Residuals of that alignment (verification, LOW, not changed in code):** (1) a restart of **system_server**
  without a kernel reboot (crash, watchdog) also resets LockSettingsService's timer to "from boot", while the HAL,
  correctly, keeps its timer within the boot (same boot_id): an attempt in that gap is answered `RETRY` and counted,
  one tier per such restart while a lock-out is pending. Trusty-backed devices behave the same; making the HAL forget
  its timer within a boot would weaken the HAL limiter against a compromised system_server. (2) LockSettingsService
  verifies the old credential of `setLockCredential` without its limiter (`LockSettingsService.java:2185-2202`), so
  the HAL counter can exceed LockSettingsService's there; after a reboot the HAL then holds out longer and its
  `RETRY` is counted until both agree. Settings and `cmd lock_settings` check the old PIN through the limited path
  first (`LockSettingsShellCommand.java:337-349`), so this needs a privileged caller passing a wrong old PIN directly.
* Note: reaching the 20th counted failure (permanent lock-out in both limiters) still takes over 13 years of
  waiting, because both limiters enforce each tier. But the tiers from 12 h up are a **practical** lock-out on a VM
  that is shut down daily: both timers restart at boot, so a tier needs that much continuous uptime. Errors count
  too, and so did post-reboot `RETRY`s up to 0.1.0 (above). Recovery without a wipe: 8.2.

---

## 6. Key material

### 6.1 What exists

| Material | Where | Lifetime | Who can read it |
|---|---|---|---|
| **K_dev**, 32 random bytes (BoringSSL `RAND_bytes`, i.e. `getrandom`) | `/data/vendor/gatekeeper/gk_key.v1` and `gk_key.v1.bak`, mode 0600, directory 0700, owner `hsm`, SELinux `gatekeeper_vendor_data_file` (lapis `vendor_file_contexts:241`) | created at the first enrollment on this /data, never rewritten, removed only with /data | the service (uid `hsm`), root. Not system_server or gatekeeperd (uid `system`) by DAC; SELinux would also deny them in enforcing mode (here everything is permissive) |
| **H**, the shared HMAC key | memory of the service | one boot (one process) | anyone who sees the nonces (public by design, section 3) |
| failure records | `/data/vendor/gatekeeper/fr.<uid>`, 0600 | until a successful verify, `deleteUser`, `deleteAllUsers` or a factory reset | as K_dev |
| boot_id (0.2.0, W4-GK-1; not secret) | `/data/vendor/gatekeeper/boot_id`, 0600 | rewritten at the first record load of each new boot; kept by `deleteAllUsers` | as K_dev |
| password handles | LockSettingsService's `spblob` directory under `/data/system_de/<u>/` | LockSettings' business | system |

No key is a compile-time constant except the two that interoperability fixes: the shared-secret agreement key (zero,
as in every software KeyMint) and the zero MAC key used before negotiation.

### 6.2 File formats and rules (`src/gk_store.h`)

* `gk_key.v1`: 72 bytes = `"GBGKKEY1"` | K_dev | SHA-256(`"GBGKKEY1"` | K_dev). `fr.<uid>`: 64 bytes = `"GBGKFR01"` | uid |
  failure_counter | secure_user_id | last_checked_timestamp | SHA-256 of the first 32 bytes (unchanged in 0.2.0).
  `boot_id` (0.2.0): 37 bytes, the content of `/proc/sys/kernel/random/boot_id` (36-character UUID + `"\n"`) of the
  boot whose first record load reset the timestamps; anything else (missing, damaged) means "new boot". Written like
  the records (`.tmp`, `fsync`, `rename`, directory `fsync`), and only after every record was rewritten. Reading the
  kernel file is allowed to every domain by lapis's policy (`plat_sepolicy.cil:197` genfscon
  `/sys/kernel/random` -> `proc_random`; `:24677, 24682` `allow domain proc_random` dir/file read).
* K_dev is created only by an enrollment without a current handle and only when **both** copies are absent
  (ENOENT) in a directory that opened and is not on tmpfs/ramfs. Any other state (EACCES, EIO, short file, bad
  checksum on both, one damaged and the other absent, the directory missing) fails closed and is logged ("refusing
  to create a new one ... recovery: factory reset"; read 8 first: most of these are repairable). A damaged or
  missing copy is rewritten from the good one.
* **The "no current handle" part is nominal (review GK-4).** Android 17 enrolls every LSKF protector and the SP
  handle without a current handle (`SyntheticPasswordManager.java:1139, 916-918`); only the rare re-enroll passes
  one (`:1618, 1899`). So after a real loss of both key files, the next enrollment of any user (or an escrow-token
  reset) creates a new K_dev; the host test `test_key_loss_does_not_count` (`tests\test_main.cpp:734-737`) asserts
  exactly that. It costs nothing extra (the old handles were already unverifiable), but a PIN *change* is refused
  only because LockSettingsService first verifies the old PIN, which fails without K_dev, not because of this rule.
* Writes go to `<name>.tmp`, `fsync`, then `rename`, or `link` for a new key (no replace), then `fsync` of the
  directory; the key is read back before it is used.
* The directory is created by init at post-fs-data on the mounted /data (`rc\gb_gatekeeper.rc`); the service never
  creates it, opens it only at the first enroll/verify, and refuses to create a key on tmpfs/ramfs (an unmounted /data).
* verify never creates a key, and a missing key is rejected **before** libgatekeeper runs, so it does not count as a
  wrong guess (test `test_key_loss_does_not_count`).
* `deleteAllUsers` removes failure records only, never K_dev: gatekeeperd calls it at its first enroll after
  `/data/misc/gatekeeper` appears (section 2), when handles made with K_dev may still exist.

### 6.3 Why a device key at all

AOSP's nonsecure Gatekeeper signs with scrypt alone (its "password key" is 32 zero bytes and unused,
`SoftGateKeeper.h:60, 85`). With K_dev:

* a copy of `/data/system_de` (for example a system-uid compromise or a leaked backup) is not enough for an offline
  brute force of a 4-6 digit PIN, which scrypt alone does not prevent;
* nothing secret is compiled into the binary (a requirement of this task; gbOS86 uses fixed keys).

Costs, accepted: handles are not interchangeable with Google's nonsecure Gatekeeper (switching implementation means
setting the PIN again), the FRP screen-lock path (section 4), and the K_dev loss risk above. What it does **not** give:
any protection against root, a host with access to `lapis.bin` or the disk, the COM2 root console, or permissive
SELinux. It is a convenience lock.

---

## 7. Integration design (for wave 4)

### 7.1 Files in both trees (dest relative to `/odm`, i.e. `gbstage/` for the VM and `gbnative/` for native)

| dest | source | mode |
|---|---|---|
| `vup/bin/hw/android.hardware.gatekeeper-service.gb` | `C:\GB-build\native\gatekeeper\out\android\android.hardware.gatekeeper-service.gb` | 0755 |
| `etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml` | `C:\GB-build\native\gatekeeper\vintf\android.hardware.gatekeeper-service.gb.xml` | 0644 |
| gb.rc text | `C:\GB-build\native\gatekeeper\rc\gb_gatekeeper.rc` | (appended) |

`vup/bin/hw/...` appears as `/vendor/bin/hw/android.hardware.gatekeeper-service.gb` through the overlay, like the
KeyMint. The VINTF fragment is an **ODM device-manifest fragment** (`system/libvintf` `VintfObject.cpp:294-305`
`fetchOdmHalFragments`, merged after the vendor fragments, `:309-345`); `/odm` is our tmpfs and is complete before
servicemanager reads VINTF at `on init`. Nothing goes into `se_manifest.xml`.

### 7.2 VINTF fragment

```xml
<manifest version="9.0" type="device">
    <hal format="aidl" override="true">
        <name>android.hardware.gatekeeper</name>
        <version>1</version>
        <fqname>IGatekeeper/default</fqname>
    </hal>
    <hal format="aidl" override="true">
        <name>android.hardware.security.sharedsecret</name>
        <version>1</version>
        <fqname>ISharedSecret/gatekeeper</fqname>
    </hal>
</manifest>
```

Both instances are in the Android 17 framework compatibility matrix (`hardware/interfaces`
`compatibility_matrices/compatibility_matrix.202604.xml:236-243` IGatekeeper/default V1, `:576-585` ISharedSecret
`regex-instance .*`; lapis's own copy `system/etc/vintf/compatibility_matrix.202604.xml:557-563`). libvintf's
`kMetaVersion` is 9.0 (`include/vintf/constants.h:26`), and our `se_manifest.xml` already uses 9.0 on lapis.

`override="true"` on **both** HALs (changed by review GK-6; the prototype's fragment file was updated). Why: libvintf
stops at the first fragment it cannot add and the **whole** device manifest fails (`VintfObject.cpp:234-266`), which
means no vendor AIDL HAL is declared and the boot fails. A second `IGatekeeper/default` (Trusty fragment renamed by an
OTA) or a vendor `ISharedSecret/gatekeeper` (lapis's `vendor_service_contexts:2` already labels that name) would do
that. With `override`, libvintf first removes every earlier device-manifest HAL of that name (`HalManifest.cpp:219-235`;
AIDL versions share one fake major version, so all instances of the name go). Trade-off: a future *live* vendor
`ISharedSecret` instance would be undeclared too. Today none is removed: lapis R1's vendor `manifest.xml`, its
fragments and the masked strongbox APEX fragment declare no ISharedSecret, and our KeyMint's `ISharedSecret/default`
is in the framework manifest (`inject\se_manifest.xml`), which device-manifest overrides do not touch.

### 7.3 Service rc

```
service gb_gatekeeper /vendor/bin/hw/android.hardware.gatekeeper-service.gb
    class early_hal
    user hsm
    group hsm
    seclabel u:r:hal_gatekeeper_default:s0
    interface aidl android.hardware.gatekeeper.IGatekeeper/default
    interface aidl android.hardware.security.sharedsecret.ISharedSecret/gatekeeper
    stdio_to_kmsg
    onrestart restart gatekeeperd

on post-fs-data
    mkdir /data/vendor/gatekeeper 0700 hsm hsm
```

* `class early_hal` (as AOSP's nonsecure Gatekeeper, `android.hardware.gatekeeper-service.nonsecure.rc`): started at
  late-fs (`system/core` `rootdir/init.rc:635-642`), before keystore2 negotiates and long before gatekeeperd
  (`late_start`, `init.rc:1229`). It does not touch /data until the first enroll/verify, which only system_server
  causes, after post-fs-data. The `mkdir` runs after init.rc's `mkdir /data/vendor 0771 root root encryption=Require`
  (`init.rc:685`; init.rc's post-fs-data actions are queued first).
* `user hsm` (`AID_HSM` 1064, `system/core` `libcutils/include/private/android_filesystem_config.h:119`; lapis's Weaver
  rc already runs as `hsm`) instead of AOSP's `user system`: system_server and
  gatekeeperd run as `system`, so with `system` they could read K_dev by DAC. VM_UPGRADE.md 2.2(c) first assumed
  `class hal, user system, group system`; the review rewrote it to this section.
* `mkdir` on an existing directory resets its mode and owner to the ones given (AOSP init README, not re-read here:
  INFERRED), so a wrong owner of the directory is repaired at every boot; a wrong owner of the *files* is not (8.3).
* `interface aidl`: if anything waits for an instance while the service is down, servicemanager has init start it.
  lapis's Trusty Gatekeeper rc declares no interface, so there is no duplicate.
* `stdio_to_kmsg` (rehearsal finding, 2026-10-26): init points stdout/stderr at `/dev/kmsg_debug` and falls back to
  `/dev/null` without it; lapis's user-build init never creates that node, so up to the rehearsal none of the service's
  stderr lines reached dmesg / serial0.log (nor those of the other `stdio_to_kmsg` gb services). Both gb.rc files now
  start with the `gb_kmsgdbg` oneshot (`toybox mknod -m 0600 /dev/kmsg_debug c 1 11`, `exec_start` + `restorecon` first
  thing at early-init; `build_esp.GB_KMSGDBG_*`, `_check_gatekeeper`, verify_image V29). Measured: init opens the node as
  root before it drops to `hsm`, so a root-only 0600 node is enough. Logcat (tag `gb_gatekeeper`) was never affected.
* `onrestart restart gatekeeperd`: needed because gatekeeperd holds the HAL binder from its start. If the HAL crashed
  before post-fs-data, this would start gatekeeperd early; it then exits on its `chdir(/data/misc/gatekeeper)` and init
  retries it until the directory exists (INFERRED harmless: gatekeeperd is not `critical`).

### 7.4 SELinux

No new type, nothing to add to `odm_sepolicy.cil`:

* `hal_gatekeeper_default` and `hal_gatekeeper_default_exec` exist in lapis's policy and are already
  `typepermissive` (`inject\types.txt:884-885`, `inject\odm_sepolicy.cil`); `seclabel` sets the domain, as for
  `gb_keymint`, so the label of the tmpfs file does not matter.
* The policy would even allow the service in enforcing mode for its files and binder names: AOSP
  `vendor/hal_gatekeeper_default.te` (rw `gatekeeper_vendor_data_file` dirs and files; lapis
  `vendor_sepolicy.cil:1912-1918`), `/data/vendor/gatekeeper(/.*)?` -> `gatekeeper_vendor_data_file` (lapis
  `vendor_file_contexts:241`, AOSP `vendor/file_contexts:168`), and
  `android.hardware.security.sharedsecret.ISharedSecret/gatekeeper` -> `hal_gatekeeper_service` (lapis
  `vendor_service_contexts:2`); `android.hardware.gatekeeper.IGatekeeper/default` -> `hal_gatekeeper_service` (lapis
  `system/etc/selinux/plat_service_contexts:117`), which `hal_server_domain(hal_gatekeeper_default, hal_gatekeeper)`
  may add.
* Because every domain is permissive, the service also checks the **caller uid** itself: IGatekeeper only from `system`
  (gatekeeperd) or root, ISharedSecret only from `keystore` (keystore2) or root; meta transactions are open. Without
  that, any app could reset throttling (`deleteAllUsers`) or the shared key.

### 7.5 What stays in gb.rc / build_esp.py (both trees)

* **Keep** `mount none /odm/empty_vintf.xml /vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.trusty.xml bind`
  (VM ~line 909, native ~1552): one declaration only (ours); `override` covers the case that the bind misses.
  VM_UPGRADE.md 2.2(c) first said to remove it "unless `<GK_DIR>` brings its own fragment and keeps that bind": it
  does both, and the review rewrote 2.2(c) to keep the bind.
* **Keep** the GBINIT whiteout of `android.hardware.gatekeeper-service.trusty.rc` and the `SK_STUB` over
  `/vendor/bin/hw/android.hardware.gatekeeper-service.trusty`.
* **Keep** Weaver undeclared and `keystore.module_hash.sent=true`; nothing else changes for the KeyMint.
* Checks to add (`_check_vm` / `verify_image.py`, wave 4's owners; **implemented 2026-10-26, 7.9**): exactly one service with
  `interface aidl android.hardware.gatekeeper.IGatekeeper/default`; the binary's sha256 equals
  `out\android\SHA256SUMS`; the VINTF fragment present **and byte-identical to the reviewed file** (a fragment libvintf
  cannot parse fails the whole device manifest, 7.2); the Trusty fragment bind present; NEEDED of the binary only
  LLNDK libraries; **pairing rule**: the binary, the fragment and the rc stanza are either all three in a tree or none
  of them (a fragment without a running service hangs boot, section 4; a service without the fragment cannot
  register). Porting check after a lapis update: `hal_gatekeeper_default` still in `types.txt`; the Trusty
  fragment and rc paths unchanged; no new vendor declaration of `IGatekeeper` or `ISharedSecret`; `hsm` still
  accepted by init.

### 7.6 Component manifest draft

`C:\GB-build\native\gatekeeper\component\gatekeeper.json` (only keys `_load_manifest` knows: `name`, `source`,
`comment`, `files`, `rc_files`):

```json
{
 "name": "gatekeeper",
 "source": "C:\\GB-build\\native\\gatekeeper",
 "files": [
  {"src": "C:\\GB-build\\native\\gatekeeper\\out\\android\\android.hardware.gatekeeper-service.gb",
   "dest": "vup/bin/hw/android.hardware.gatekeeper-service.gb", "mode": "0o100755"},
  {"src": "C:\\GB-build\\native\\gatekeeper\\vintf\\android.hardware.gatekeeper-service.gb.xml",
   "dest": "etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml", "mode": "0644"}
 ],
 "rc_files": ["C:\\GB-build\\native\\gatekeeper\\rc\\gb_gatekeeper.rc"]
}
```

Components are native-only in `build_esp.py` today; the VM tree needs the same two `gbstage/...` entries in the
`_VM_ENTRIES` list and the rc text appended to the VM `GB_RC`. (Done 2026-10-26, 7.9: this draft, with only its
comment changed, is `C:\GB-x86\inject\native\components\gatekeeper.json`, and build_esp reads it for both trees.)

### 7.7 Wire protocol (for reviewers)

| Instance | Code | Method | Reply |
|---|---|---|---|
| `IGatekeeper/default` (hash `d877e8a1608a00fd9068bcd7e69ea480a7ea17f0`, V1) | 1 | `deleteAllUsers()` | ok |
| | 2 | `deleteUser(int uid)` | ok |
| | 3 | `enroll(int uid, byte[] curHandle, byte[] curPw, byte[] newPw)` | `GatekeeperEnrollResponse{int statusCode, int timeoutMs, long secureUserId, byte[] data}` or service-specific `-1` |
| | 4 | `verify(int uid, long challenge, byte[] handle, byte[] pw)` | `GatekeeperVerifyResponse{int, int, HardwareAuthToken{long challenge, long userId, long authenticatorId, int authenticatorType, Timestamp{long milliSeconds}, byte[] mac}}` or service-specific `-1` |
| `ISharedSecret/gatekeeper` (hash `aea3745b559d2977f0676952e510fb0547a52140`, V1) | 1 | `getSharedSecretParameters()` | `SharedSecretParameters{byte[] seed (empty), byte[] nonce (32)}` |
| | 2 | `computeSharedSecret(SharedSecretParameters[])` | `byte[32]` sharing check, or service-specific KeyMint error |
| both | 0xFFFFFF / 0xFFFFFE | `getInterfaceVersion` / `getInterfaceHash` | 1 / the hash |

Codes and hashes from `hardware/interfaces` AIDL sources and `aidl_api/.../1/.hash`; the encoding (non-null marker 1,
size patched afterwards, nested parcelables framed the same way, `byte[]` as length + data) is the NDK backend's
(`binder_parcel_utils.h:674-686, 1609-1640`; `libs/binder/ndk/parcel.cpp:49-172`). Error mapping as AOSP's
`GateKeeper.cpp`: failure = service-specific `ERROR_GENERAL_FAILURE`, throttling = `statusCode ERROR_RETRY_TIMEOUT`
with `timeoutMs`. libbinder_ndk checks the interface token before our handler runs (`ibinder.cpp:272-283`). Replies
of IGatekeeper are marked sensitive (`AParcel_markSensitive`, as the generated code for `@SensitiveData` does).

### 7.8 Code changes required by the review (wave 4, before the user sets a real PIN)

**Done in gb_gatekeeper 0.2.0 (2026-10-26), off-device.** Full `build.sh` run (host tests under ASan/UBSan, the
Python reference check, the lapis-bionic run, ELF checks), new sha256 in `out\android\SHA256SUMS`
(`505b404d...3d51`), `out\ACCEPTANCE.txt`; reproducible across output directories (9.1). Each new test was also
compiled against the reviewed pre-W4 sources (`tests\regress\pre_w4_src\`, byte-identical copy, `tests\regress_old.sh`)
and **fails there** (`out\regress\summary.txt`: 22 / 14 / 10 / 3 failed checks for `test_throttling`,
`test_reboot_loop_no_bypass`, `test_crypto_failure_not_counted`, `test_registration`). **Verified independently**
(`REVIEW.md` 7): `tests\regress\pre_w4_src` rebuilt with the 0.1.0 recipe gives exactly `b62b205c...a46e` and
`9d3b1efd...21d2` (the reviewed stripped/unstripped hashes), so the regression proof runs against the reviewed code;
the reboot-loop test was added then. Still to do: the device tests of 9.2/9.5.

| Id | Must/should | Change (as implemented) | Test (`tests\test_main.cpp`) | On the pre-W4 code |
|---|---|---|---|---|
| W4-GK-1 | **must** | `GbGateKeeper::CheckBoot` (`src\gb_gatekeeper.cpp`), once per process at the first record load: reads `/proc/sys/kernel/random/boot_id` (must be a 36-character UUID) and compares it with `boot_id` in the state dir (`GkStore::CompareBootId`). Same: nothing changes (a restart within the boot keeps the timestamps, crash-proof throttling as before). Different, file missing or damaged, or the kernel's unreadable: `GkStore::ResetRecordTimestamps` rewrites every valid `fr.<uid>` with `last_checked_timestamp = 0` (counter and SID kept), every record this process loads starts with 0 as well (covers a rewrite that failed), and only if all rewrites succeeded is the new boot_id stored (an unreadable kernel boot_id is never stored). Logged once: `new boot: failure-record timers run from boot (counters kept)`. Effect: the HAL timeout runs from boot, so an attempt LockSettingsService lets through is never refused with `RETRY`. Erring towards "new boot" only shortens the HAL timer; LockSettingsService still enforces its own. | `test_throttling`: new boot_id at 50 ms: `RETRY` 59,950 ms, record counter 5 / timestamp 0, boot_id rewritten; same-boot restart keeps the timer (57,000 ms, later 30,000 ms); at 60,001 ms the right PIN unlocks; escalation needs real wrong guesses (6th = 300,000 ms); missing / damaged boot_id file and unreadable kernel boot_id = new boot. `test_reboot_loop_no_bypass` (verification): 20 reboots and 3 boot_id-less starts during a lock-out never unlock early or change a counter; each tier is served in full from the last boot (section 5). `test_enroll_verify`: the production reader stores the kernel's boot_id byte for byte. `test_replace_and_delete`: `deleteAllUsers` keeps `boot_id`. | 22 + 14 failed checks: e.g. `expected RETRY 59950 ms, got ... timeout=60000`; at 60,001 ms `RETRY` 49 ms instead of unlock |
| W4-GK-2 | should | `GatekeeperService::Enroll`/`Verify` (`src\aidl_service.cpp`) take `SnapshotRecord(uid)` before libgatekeeper runs and `RestoreRecord` (cache and disk) when the request failed internally: `CryptoFailed()` (password key missing, scrypt or HMAC failure) or a libgatekeeper error other than `ERROR_INVALID` (mismatch) / `ERROR_RETRY` (e.g. `ERROR_MEMORY_ALLOCATION_FAILED` from `MintAuthToken`). The answer stays a service-specific `ERROR_GENERAL_FAILURE`, log `verify uid=N -> error (crypto failure E; not counted)` (E = libgatekeeper's error code, or `internal error E`). Residual: a failing *small* allocation inside libgatekeeper's private `CreatePasswordHandle` (58 + n bytes) still looks like a mismatch (INFERRED negligible: lmkd kills first). | `test_crypto_failure_not_counted` (the signature computation fails through `GbGateKeeper::ComputePasswordSignature`'s own failure path): after 4 wrong PINs, two verifies of the right PIN with a crypto failure leave counter 4 and its timestamp on disk, the next right PIN unlocks at once; a PIN change and a new enrollment with a crypto failure leave the record (counter, timestamp, SID) unchanged | 10 failed checks: counter 5 after the first crypto failure, then `RETRY` 59,980 ms for the right PIN; the failed new enrollment had cleared the record under a new SID |
| W4-GK-3 | should | `src\service_main.cpp`: registers `IGatekeeper/default` first (5 attempts, 1 s apart); if refused, logs `... not registered: exiting with status 3 ...` and **exits 3** (init restarts it; no `joinThreadPool`). Only then `ISharedSecret/gatekeeper`: its failure alone is logged and IGatekeeper served (keystore2 retries in the background). "Register even if the self-test failed" kept. | `test_registration` runs the real `service_main.cpp`, linked with a fake service manager (`tests\fake_sm.cpp`: refuses the instances in `$GBGK_FAKE_SM_REFUSE`, blocks in `joinThreadPool`), as a child process: IGatekeeper refused: exit 3 after 5 attempts, never serving, ISharedSecret never offered; ISharedSecret refused: serves IGatekeeper; both fine: serves; `--selftest` 0, bad argument 2. Also on lapis bionic (`gk_android_service_fake` through lapis's `linker64`). | 3 failed checks: kept serving in `joinThreadPool` after the refusal, ISharedSecret registered first, no exit message |

### 7.9 Integration as built (wave 4, 2026-10-26)

Pipeline: `C:\GB-x86` (pre-edit copies in `archive\pre-w4\`, SHA256SUMS.txt inside). No image was built and
`lapis.bin` was not written; the images of wave 3 (`377310ee...` USB, `b1c355b0...` ISO, `D:\gb-export`) do not carry
the Gatekeeper yet. Evidence: `C:\GB-build\native\dgpu\w4\evidence\integration\`.

| What | Where / how |
|---|---|
| one component, both trees | `inject\native\components\gatekeeper.json` (= `component\gatekeeper.json` except its comment; `tests\w2a` TA-1 compares them). Native: the component loader packs the two files and appends `# ---- component gatekeeper ----` + `rc\gb_gatekeeper.rc` to the native gb.rc. VM: `build_esp.py` block "Software Gatekeeper, both trees" reads the same manifest (`_gatekeeper_inputs`), appends the same rc block at the end of the VM `GB_RC` and adds `gbstage/vup/bin/hw/android.hardware.gatekeeper-service.gb` (0755) and `gbstage/etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml` (0644) |
| what ships | binary 0.2.0 `505b404d0606bf80bcb41ff8a66a80c962934e3791d3b8db13645608da143d51` (312,400 B) = `out\android\SHA256SUMS`; fragment `48cd680b721996e57c4b93acce4d842d9f14e0cd3dbd9cda75e1fe3b3216b77b` (override on both HALs); rc `00e754c2...94cb`; the native gb.rc minus this block is byte-identical to the pre-W4 native gb.rc, the VM gb.rc = pre-W4 + the zram insmods (VM_UPGRADE.md 2.2(a)) + this block |
| kept | the empty bind over the Trusty Gatekeeper fragment (both gb.rc, early-init), GBINIT's whiteout of its `.rc`, `SK_STUB` over its binary, the Weaver bind; `keystore.module_hash.sent`; no KeyMint change |
| SELinux | none: `hal_gatekeeper_default`, `hal_gatekeeper_default_exec` and `gatekeeper_vendor_data_file` are lapis types and already `typepermissive` in `inject\odm_sepolicy.cil` (`tests\w4` GK checks it) |
| build-time refusals (`_gatekeeper_inputs`) | binary sha256 != its `SHA256SUMS` line; `out\ACCEPTANCE.txt` version < 0.2.0 (W4-GK-1..3) or without this binary's sha256 line; ELF interpreter not `/system/bin/linker64` or a NEEDED outside `libbinder_ndk libc libdl liblog libm`; fragment sha256 != the reviewed one; rc not LF-only ASCII or missing a 7.3 line (`class early_hal`, `user hsm`, `group hsm`, the seclabel, both `interface aidl` lines, `stdio_to_kmsg`, `onrestart restart gatekeeperd`, the post-fs-data mkdir); manifest keys other than name/source/comment/files/rc_files, other dests, more files or rc files |
| tree checks (`_check_gatekeeper`, from `_check_vm` / `_check_native`) | binary (0755, accepted bytes), fragment (0644, reviewed bytes, the only ODM VINTF fragment), the rc block once, exactly one `interface aidl android.hardware.gatekeeper.IGatekeeper/default` line, the Trusty and Weaver binds once each, the Trusty binary = `SK_STUB`, GBINIT's Trusty `.rc` whiteout. The pairing rule follows: a shipped tree has all three or the build stops |
| image checks (`verify_image.py` V29) | the same rules on any ESP/image, every kind except the test-only `vmtest-nogk` (there: none of the three, masks kept); sources from the component manifest, ELF NEEDED/interpreter parsed from the program headers. V12 (native) also compares the two files and the rc file with the manifest |
| rehearsal variants (`build_usb.py`, output only below `D:\gb-w4test\`) | `--vm-test --vm-test-rehearsal`: exactly `build_esp.INJECT_CPIO`, the tree the user-VM ESP gets, plus `<out>.vmtree.sha256` (gb/inject.gz sha256) for verify_image `--expect-inject-sha256` (V30) at VM_UPGRADE.md 2.4; `--vm-test --vm-test-nogk`: the same tree minus exactly the binary, the fragment (+ its two directories) and the rc block, the pre-W4 stand-in for 9.5's fix-forward / rollback-after-clear (kind `vmtest-nogk`) |
| tests | `tests\w4\test_w4.py` (GK, GKM, ELF, REH, DIFF, CONS groups), `tests\w2b\test_verify_image.py` (V29/V30 mutations, the no-Gatekeeper fixture, today's pre-W4 image fails exactly V12 + V29), `tests\w2a` TA-1/TA-5, `tests\w2b\test_trees_from_build_esp.py`, `tests\w3a\test_w3a.py`: all pass |

Not part of the integration: a native/VM image with it (the next USB/export/ISO rebuild, then ISO T0/T1a/T1b/T3/T4 +
VM-1; the builder's `/` had 16.6 GiB free on 26/10, below build_iso's 25 GiB preflight), the device tests (9.2-9.5),
the repo (`collect.py` still excludes `native/gatekeeper/`; never commit the binary).

---

## 8. Lock-out recovery (a wipe is the last resort)

Rewritten by the review (GK-3). While /data has no file-based encryption, K_dev, the handles and the user's files
survive almost every lock-out; only a forgotten PIN or a truly lost K_dev needs a wipe. 8.2, 8.3, 8.4, 8.5 and 8.6
were **performed on the rehearsal VM on 2026-10-26** (9.6; the user data survived every one except, by design, the
8.5 wipe, after which the copied-out file matched). Root shell: on the VM the COM2 root console (`gbsh.py`, the
user's tool; connect only after boot_completed, VM_UPGRADE.md 2.6); on native there is no root shell on a user
build, so boot a Linux stick and mount the internal `userdata` (plain ext4 while FBE is off). `<u>` below is the
signed-in user: **10** on lapis (headless system user mode, 9.2 note); the LSKF uid in the HAL's log is `100000 + <u>`.

### 8.1 Which case is it

Measured messages (rehearsal, 9.6). The service's stderr is in `dmesg` / `serial0.log` (since `gb_kmsgdbg`, 7.3) and
in `logcat -s gb_gatekeeper`; lapis's logcat keeps only minutes, kmsg the whole boot.

| Symptom (`dmesg \| grep gb_gatekeeper`, logcat `-s gb_gatekeeper gatekeeperd LockSettingsService`, `dumpsys lock_settings`) | Case | Go to |
|---|---|---|
| `verify uid=1000<u> -> retry in N ms`, or the lock screen shows a countdown; the PIN is known | throttling (both limiters) | 8.2 |
| `E open /data/vendor/gatekeeper/gk_key.v1: Permission denied` (one per copy) followed by `E password key unusable (primary damaged, backup damaged): refusing to create a new one; ... (recovery: factory reset)` and `verify uid=... -> error (password key unavailable)`. **The summary line says "damaged" and "factory reset" for unreadable files too: the `open ...:` line before it decides, and this case needs no wipe** (wording to fix in the next gb_gatekeeper build). Also: `crypto self-test FAILED`, `crypto failure ...; not counted`, the service crash-looping (`IGatekeeper/default not registered: exiting with status 3`, init's `Service 'gb_gatekeeper' (pid N) exited with status 3`: the instance is refused by servicemanager, check the VINTF fragment and service_contexts) | gb_gatekeeper broken or its files unreadable | 8.3, then 8.2 |
| gatekeeperd: `Could not find Gatekeeper device, which makes me very sad.` at its start and `has no HAL to talk to` at each unlock attempt; no `gb_gatekeeper` in `getprop \| grep init.svc` | a tree without gb_gatekeeper was booted | 8.4, then 8.2 |
| the PIN is not known, or both key copies are really damaged: `E /data/vendor/gatekeeper/gk_key.v1: checksum mismatch` (or `bad size ... or magic`) for **both** copies, then the same `password key unusable ... refusing to create a new one` line | real lock-out | 8.5 |

### 8.2 Clear the throttling counters (PIN known)

With the device at the lock screen, as root (`<u>` = the signed-in user, 10 on lapis):

```
rm -f /data/vendor/gatekeeper/fr.*                       # gb_gatekeeper's failure records
rm -f /data/system_de/<u>/spblob/*.failure_counter        # LockSettingsService's counter (user <u>)
reboot
```

Both are read as "no failures" when missing (`gk_store.h`; `SyntheticPasswordManager.java:2289-2310`). Both limiters
cache their state in memory, hence the reboot. Then unlock with the PIN. Do **not** touch `gk_key.v1*`, `*.pwd`,
`*.spblob`, `*.secdis` or `handle`.

### 8.3 gb_gatekeeper broken or its files unreadable

* Wrong owner/mode/label: `chown -R hsm:hsm /data/vendor/gatekeeper; chmod 700 /data/vendor/gatekeeper;
  chmod 600 /data/vendor/gatekeeper/*; restorecon -R /data/vendor/gatekeeper; setprop ctl.restart gb_gatekeeper`.
* One key copy damaged: nothing to do, the service repairs it from the other one at the next load
  (`setprop ctl.restart gb_gatekeeper`).
* Binary broken or missing (self-test fails, crash loop, absent after an update): **fix forward**, i.e. boot a tree
  with a working gb_gatekeeper of the same on-disk scheme (`GBGKKEY1`, the same scrypt + HMAC step). VM: the
  post-upgrade ESP snapshot (VM_UPGRADE.md 2.9) with `gb_vmbackup.py restore <BKP> --esp-only --yes`; native: the
  previous image's ESP. K_dev and the handles on /data are unchanged, so the PIN works again.
* Then 8.2 (every attempt during the fault was counted, section 5).

### 8.4 A tree without gb_gatekeeper was booted with a PIN set

Same as the last point of 8.3: boot a tree with gb_gatekeeper again (VM: `<BKP>`, VM_UPGRADE.md 3.1), then 8.2. Then
clear the PIN *before* booting the old tree again, if that is still wanted (8.6).

### 8.5 Forgotten PIN or K_dev really lost: wipe, after copying the data out

1. Copy out what matters while FBE is off. While the VM or device runs at the lock screen, `adb pull /sdcard/...`
   works with our pre-authorised host key (INFERRED); as root, anything under /data can be copied. For the VM, the
   userdata partition of a *copy* of `lapis.bin` can also be mounted on another Linux.
2. Wipe:
   * **VM:** power off, then on the host `python -I wipe_data.py --yes` (repository root; it zeroes the first 16 MiB
     of the `userdata` partition of `lapis.bin`, and the next boot formats /data, `docs/VMWARE.md:116`). Or, if
     nothing after the backup matters, VM_UPGRADE.md 3.3.
   * **Native:** boot the installer or a Linux stick and reformat the `userdata` partition of the install (the
     installer's fresh-install path, `docs/INSTALLER-ISO.md`). INFERRED: there is no native equivalent of
     `wipe_data.py` in the repository today; wave 4 should add one or document the exact installer step.

### 8.6 Avoidable lock-outs

* Clear the PIN (`Settings > Security > Screen lock > None`, or `cmd lock_settings clear --old <PIN>`) before booting
  any tree, rollback or fallback image that has no gb_gatekeeper or a different signature scheme: an ESP-only
  rollback to a pre-W4 ESP (VM_UPGRADE.md 3.1), the pre-W4 images in `D:\GB-iso\prev\`, an old USB stick on the
  internal install.
* Never delete `/data/vendor/gatekeeper` or its `gk_key.v1*` files.
* Never replace the KeyMint implementation with a PIN set (section 3).
* When the lock screen refuses the right PIN with an error (not a countdown), stop trying and find the cause (8.1):
  every attempt is counted (section 5).

---

## 9. Test plan

### 9.1 Done (off-device, reproducible: `sh build.sh` on the builder with `REF_LIBBINDER`, `LAPIS_RT`, `LAPIS_SYSLIB` set)

Result in `C:\GB-build\native\gatekeeper\out\ACCEPTANCE.txt`, logs in `out\build.log`, `out\host\`, `out\android\`,
`out\regress\`, `out\repro\`. Acceptance run (also `REGRESS_OLD=tests/regress/pre_w4_src` and
`COMPARE_WITH=<second build>/android/SHA256SUMS`): gb_gatekeeper **0.2.0** (W4-GK-1..3), built 2026-10-26T04:49:56Z,
binary sha256 `505b404d...3d51`. (0.1.0, the reviewed build `b62b205c...a46e`: 102 host checks passed.)

| Test | Result |
|---|---|
| host unit tests, ASan + UBSan (`tests\test_main.cpp`: key lifecycle incl. damaged/missing copies and tmpfs refusal, failure records, enroll/verify/HAT fields, wrong PIN, tampered and short handles, persistence across a simulated reboot, the kernel boot_id stored by the production reader, throttling table, **reboot during a lock-out with a new boot_id, same-boot restarts, missing/damaged/unreadable boot_id, 20 reboots and 3 boot_id-less starts during a lock-out never shorten it or change a counter (W4-GK-1)**, **crypto failure during verify, PIN change and new enrollment not counted (W4-GK-2)**, key loss not counted, a PIN change never creates a key, PIN change keeps the SID, deleteUser/deleteAllUsers keep K_dev and boot_id, caller uid checks, meta transactions, truncated requests, self-test-failed mode, shared-secret negotiation incl. null element and a newer parcelable field, **the real `service_main.cpp` as a child process with a fake service manager: IGatekeeper refused -> exit 3, ISharedSecret refused -> serves, both -> serves, `--selftest`, bad argument (W4-GK-3)**) | **172 passed, 0 failed** (`out\host\test.log`; 152 before the verification added `test_reboot_loop_no_bypass`) |
| **regression proof** (`REGRESS_OLD=tests/regress/pre_w4_src`, `tests\regress_old.sh`): the same tests compiled against the reviewed pre-W4 sources (13 files, `out\regress\inputs.sha256`; proven to be the reviewed code by rebuilding them to the reviewed binary hashes `b62b205c...a46e` / `9d3b1efd...21d2`, `out\verify\`) | **fail as required: 121 passed, 51 failed**; `test_throttling` 22, `test_reboot_loop_no_bypass` 14, `test_crypto_failure_not_counted` 10, `test_registration` 3, plus the two new boot_id file checks (`out\regress\summary.txt`, `test.log`) |
| independent Python recomputation (`tests\ref_check.py`, `python3 -I`: hashlib.scrypt + hmac + cryptography's AES-CMAC): handle signature and layout, token MACs (zero key and H), H and the sharing check against the soft-KeyMint algorithm, a one-block CKDF | **PASS (5 checks)**, host and Android vectors |
| the same unit tests **built for Android with the NDK and run on lapis's own bionic** (`linker64` + `libc` from lapis's `com.android.runtime` APEX, lapis `/system/lib64`, R1 images on the builder); `test_registration` runs `gk_android_service_fake` (the real `service_main.cpp` + fake service manager) through the same lapis `linker64` | **172 passed, 0 failed**, ref check PASS (`out\android\test\`) |
| the service binary on lapis's bionic: `--version` (`gb_gatekeeper 0.2.0`), `--selftest` (SHA-256, HMAC, scrypt, AES-256-CMAC KATs). The binary is `BIND_NOW`, so this also resolved every import against lapis's libraries, `libbinder_ndk.so` included | **PASS** |
| ELF: NEEDED only `libbinder_ndk libc libdl liblog libm`; BIND_NOW; PIE; no TEXTREL; interpreter `/system/bin/linker64`; verneed `LIBBINDER_NDK`, `LIBBINDER_NDK30`, `LIBBINDER_NDK31`; all 22 binder imports exported with those versions by lapis's `libbinder_ndk.so`; the only new import against 0.1.0 is `read@LIBC` (the boot_id read) | **PASS** |
| `src\abi_check.cpp`: our hand-written binder prototypes compile next to the NDK and AOSP platform headers (a mismatch is a compile error) | **PASS** |
| reproducibility: two clean builds (BoringSSL included) into `out` and `out_repro` on the builder (`COMPARE_WITH`); BoringSSL's DWARF build directory is mapped (`-fdebug-prefix-map`), since it used to change lld's build-id with the output path. The verification added a third clean build into an empty `~/gk/proto/gatekeeper/vfy_w4/out_clean` (tree removed afterwards; record in `out\verify\out_clean.*`) | **byte-identical**, stripped and unstripped (`out\repro\`, `out\verify\`) |

Not covered off-device: real binder IPC (no binder device on the builder), servicemanager's real answer to an
undeclared instance (the fake answers `EX_ILLEGAL_ARGUMENT`; W4-GK-3 exits on any refusal), init's restart of the
exited service, the real kernel boot_id across a real reboot (the tests inject boot_ids; the production reader is
tested against the builder kernel's file), gatekeeperd/keystore2/LockSettingsService, SELinux. The lapis libraries
used are `CL3B.260622.271.R1`; the VM runs a later build (R4); only LLNDK ABIs matter, which Treble keeps stable
(INFERRED for R4 until 9.2 step 2).

### 9.2 VM (after the backup in VM_UPGRADE.md step 1; part of its rehearsal 2.3 and V-7/V-8)

Run as root on the COM2 console or `adb shell`. Pass criteria in brackets. On the throwaway rehearsal VM (VM_UPGRADE.md
2.3: `build_usb.py --vm-test --vm-test-rehearsal`, below `D:\gb-w4test\`) "root" is that VM's own console or adb;
the user VM's console (`gbsh.py`) and adb address belong to the VM-upgrade phase only.

**Measured on the rehearsal (2026-10-26, 9.6), read the steps below with these corrections:**

* **lapis runs in headless system user mode** (`ro.fw.mu.headless_system_user=true`): user 0 is the headless system
  user behind the login screen ("User" / "Use as guest" / "Add user"); the signed-in profile is **user 10** (on the
  user's VM too: "`am switch-user 10` logs into the user's profile", project memory 24/10). So the PIN belongs to user
  10: every `cmd lock_settings ...` call needs `--user 10` (check with `am get-current-user` after signing in), the
  Gatekeeper uids are **`100010`** (LSKF protector, `fakeUserId(10)`) and **`10`** (SP handle) instead of 100000 / 0,
  the records are `fr.100010` / `fr.10`, and LockSettingsService's counter is `/data/system_de/10/spblob/*.failure_counter`.
  After a reboot user 10 is `RUNNING_LOCKED`; picking "User" on the login screen asks for the PIN.
* The lock screen says "Click or swipe to unlock"; a click (`input tap`) shows the PIN bouncer, `input text <pin>` +
  `input keyevent 66` enters it. The bouncer is a secure window (`screencap` is black); read the state with `dumpsys
  window` (`isKeyguardShowing`) and `dumpsys lock_settings` (`numFailures`, `lockoutEndTime`).
* `dmesg | grep gb_gatekeeper` (step 1) only works since the rehearsal fix `gb_kmsgdbg` (7.3): init's `stdio_to_kmsg`
  writes to `/dev/kmsg_debug`, which lapis's user-build init never creates. lapis's logd keeps only 256 KiB per buffer,
  so boot-time logcat lines (keystore2's negotiation, "Waited one second") are gone within about a minute of
  boot_completed: step 3 was observed with `logcat -G 16M; setprop ctl.restart keystore2` (keystore2 renegotiates), and
  "no wait for IGatekeeper" from servicemanager's kmsg lines (no "trying to start it as a lazy AIDL service" for
  gatekeeperd).
* The HAL logs a wrong PIN as `verify uid=100010 -> error 1` (libgatekeeper's ERROR_INVALID); the 5th one as
  `retry in 60000 ms`.

1. Boot. `dmesg | grep gb_gatekeeper` [`gb_gatekeeper 0.2.0 starting`, then `registered ...IGatekeeper/default`,
   `registered ...ISharedSecret/gatekeeper` (0.2.0 registers IGatekeeper first), no `E ` lines, no `exiting with
   status 3`]; `getprop init.svc.gb_gatekeeper` [running]; `service list | grep -i -E 'gatekeeper|sharedsecret'`
   [IGatekeeper/default once, ISharedSecret/gatekeeper, ISharedSecret/default, `android.service.gatekeeper.IGateKeeperService`];
   `ls -ldZ /data/vendor/gatekeeper` [`drwx------ hsm hsm u:object_r:gatekeeper_vendor_data_file`, empty];
   boot completes in the usual time [no "Waited one second" for IGatekeeper in logcat].
2. `/vendor/bin/hw/android.hardware.gatekeeper-service.gb --selftest` [PASS, on the R4 libraries].
3. `logcat -d -s keystore2 | grep -i -E 'shared secret|negotiation'` ["concluded successfully", no "NOT PROVISIONED
   CORRECTLY"]; `logcat -d -s gb_gatekeeper` ["shared HMAC key computed with 2 participants"].
4. `cmd lock_settings set-pin 1357` [ok; `ls /data/vendor/gatekeeper` shows `gk_key.v1`, `gk_key.v1.bak`, `boot_id`
   (equal to `cat /proc/sys/kernel/random/boot_id`) and `fr.*` records];
   `cmd lock_settings verify --old 1357` [ok]; `cmd lock_settings verify --old 1358` [fails];
   `dumpsys lock_settings | grep -A6 SoftwareRateLimiter` [Enforcing: expected `true` (lapis R1 framework-res), note
   it, and `numFailures=1` for the protector]; `ls /data/system_de/0/spblob/` [a `*.failure_counter` file].
5. Lock the screen, unlock with 1357 [unlocks; both counters back to 0]. Then enter wrong PINs; repeating the same
   wrong PIN is fine, LockSettingsService does **not** drop duplicates on a Gatekeeper device (section 5) [after
   the 5th failure a 1 min countdown; `logcat -s gb_gatekeeper` shows `verify uid=100000 -> retry in 60000 ms`;
   `dumpsys lock_settings` `numFailures=5`]. Wait the minute, unlock with 1357 [both counters 0].
6. Auth-bound keys: open an app that uses biometric/credential-bound Keystore keys, or run
   `cmd lock_settings verify --old 1357` and then any app action that needs "screen lock confirmed"; a simpler proof is
   that unlocking works at all (the SP blob key is itself auth-bound, section 2) [unlock works = token accepted].
7. Reboot [lock screen asks for the PIN; 1357 unlocks; `logcat -s gb_gatekeeper` shows `verify uid=100000 -> ok` and
   `verify uid=0 -> ok`; no new key created].
8. **Throttling across reboot: the W4-GK-1 check** (binary 0.2.0: `--version`, sha256 as `out\android\SHA256SUMS`).
   Five wrong PINs [a 1 min countdown, `numFailures=5`, logcat `verify uid=100000 -> retry in 60000 ms`]; before the
   minute is over, `xxd /data/vendor/gatekeeper/fr.100000` [bytes 12-15 = `05 00 00 00`, bytes 24-31 = a non-zero
   timestamp] and `cat /data/vendor/gatekeeper/boot_id`; then reboot at once. Expected after boot:
   * LockSettingsService holds input for **1 min from boot** (its timer restarts at boot); the HAL is not asked;
   * when that countdown ends, 1357 unlocks **at the first attempt** [`logcat -s gb_gatekeeper` shows `new boot:
     failure-record timers run from boot (counters kept)` and then `verify uid=100000 -> ok` and `verify uid=0 -> ok`,
     no `retry in` line after boot; `dumpsys lock_settings` `numFailures=0` afterwards; `boot_id` in the state dir
     now equals `/proc/sys/kernel/random/boot_id` and differs from the one noted before the reboot; `fr.100000`
     cleared (counter 0)].

   **Fail** = the 0.1.0 behaviour (review GK-2): the first attempt after the minute is refused by the HAL
   (`verify uid=100000 -> retry in N ms`: a fresh full 60000 when the five failures were made later after their boot
   than this attempt is after this one, as with a quick reboot; otherwise what was left of their minute, counted
   on the new boot's clock) and LockSettingsService counts it: a **5 min**
   countdown, `numFailures=6`. Then stop: no real PIN; record `--version`, the binary's sha256 and the log; clear
   both counters (8.2). Optional second round on the throwaway VM: five wrong PINs, wait the minute, a sixth
   (`numFailures=6`, a 5 min countdown), reboot at once: expected a 5 min countdown from boot, then 1357 unlocks at
   the first attempt, i.e. no tier added by the reboot.
9. `kill $(pidof android.hardware.gatekeeper-service.gb)` [init restarts it and, through `onrestart`, gatekeeperd;
   `logcat` shows both starting; the PIN still unlocks; **no** `new boot:` line after the restart: a restart within
   the boot keeps the HAL timers]. (`stop` + `start` does not run `onrestart`, so gatekeeperd would keep the dead
   binder: always restart the HAL by kill or `setprop ctl.restart gb_gatekeeper`.)
10. `cmd lock_settings set-pin --old 1357 2468`, reboot, unlock with 2468 [ok]; then
    `cmd lock_settings clear --old 2468` [ok]; reboot [no lock screen].
11. Only then may the user set a real PIN in Settings: with gb_gatekeeper 0.2.0 or later in the tree (W4-GK-1, 7.8)
    **and** step 8 passed (or the user has accepted section 5's escalation in writing), and, on the VM, after the
    post-upgrade ESP snapshot (VM_UPGRADE.md 2.9).

### 9.3 Native (Surface Book 3)

Same as 9.2 steps 1-10 on a USB boot first, then the internal install (`gb_bootlog` captures dmesg at 40/90/180 s; add
`gb_gatekeeper` and `keystore2` lines to it if there is no shell).

### 9.4 OTA

After the first lapis OTA with a PIN set: the slot switch boots, gb_slotsync keeps our tree, the PIN unlocks, `--selftest`
passes, `dmesg` shows no `Conflicting FqInstance`. Before the OTA, record the sha256 of `gk_key.v1`; after it, the same.

### 9.5 Negative tests worth one run (VM, throwaway data)

* Remove the service from the tree but keep the VINTF fragment: expect the boot hang of section 4 (confirms why the
  fragment and the service must always ship together); restore from backup. (2026-10-26: the pipeline refuses to
  build such a tree, 7.9. Making one means hand-editing the cpio inside a copy of `w4-tree.img`, for the throwaway
  disk only; or skip the test: it only confirms the documented failure mode.)
* Damage one byte of `/data/vendor/gatekeeper/gk_key.v1` (the backup stays intact), then
  `setprop ctl.restart gb_gatekeeper` (K_dev is cached after its first load): the next unlock works and logcat shows "key primary
  copy damaged, restoring it from the backup"; `cmp gk_key.v1 gk_key.v1.bak` is then silent. Same with the backup
  damaged instead ("rewriting it from the primary copy"). Damaging both must make verify fail with "refusing to create
  a new one" and must not create a new key (the host test covers this; on the device it is a lock-out, so only on
  throwaway data, then `wipe_data.py`).
* Added by the review (they prove the no-wipe recoveries of section 8; throwaway VM only):
  * **Error counting and 8.2:** `chmod 000 /data/vendor/gatekeeper/gk_key.v1 /data/vendor/gatekeeper/gk_key.v1.bak;
    setprop ctl.restart gb_gatekeeper`; enter the right PIN five times [each refused with an error, logcat
    `password key unavailable`, no `retry` from the HAL; after the 5th, LockSettingsService shows a 1 min countdown
    although the PIN is right, `numFailures=5`]. Repair as 8.3
    (`chmod 600`, restart), then 8.2 (`rm` both counters, reboot) [the right PIN unlocks at once, `numFailures=0`].
  * **Reboot during a lock-out (GK-2, W4-GK-1):** step 8 of 9.2 with the build at hand [0.2.0: no escalation, the
    right PIN unlocks at the first attempt after LockSettingsService's countdown from boot]; if it fails, 8.2
    [unlocks].
  * **Registration refused (W4-GK-3; no PIN set):** keep the service and its rc stanza but leave our VINTF fragment
    out of the tree (or bind `/odm/empty_vintf.xml` over it), so `IGatekeeper/default` is undeclared and
    servicemanager refuses it (INFERRED: it refuses undeclared VINTF-stability instances) [dmesg: five `addService
    ... failed` lines, then `IGatekeeper/default not registered: exiting with status 3`, and init restarting the
    service again and again (a visible crash loop, not a silent idle); boot completes, because an undeclared
    IGatekeeper is not waited for (gatekeeperd "Could not find Gatekeeper device", as with an old tree)]; restore
    the fragment.
  * **Fix-forward after an old tree (8.4):** with a PIN set, write the pre-W4 ESP (ESP-only rollback) and boot
    [locked out: errors, gatekeeperd "Could not find Gatekeeper device"]; write the W4 ESP back and boot, run 8.2
    [the PIN unlocks; `gk_key.v1` sha256 unchanged].
  * **Rollback after a clear (8.6):** clear the PIN, ESP-only rollback to the pre-W4 ESP, boot [no lock screen, boot
    completes, user data present].
  * (2026-10-26) The "pre-W4 ESP" of these two tests is `build_usb.py --vm-test --vm-test-nogk --esp-only
    D:\gb-w4test\esp\old-tree.img --super-guid <the rehearsal disk's>` (the W4 VM tree minus exactly the Gatekeeper);
    the "W4 ESP" the same with `--vm-test-rehearsal` (VM_UPGRADE.md 2.3). The "Registration refused" test above also
    breaks the pairing rule and, like the first bullet, can only be made by hand.

### 9.6 Device rehearsal results (2026-10-26, throwaway VMware VM, VM_UPGRADE.md 2.3)

Throwaway VM `D:\gb-w4test\vm\gbw4.vmx` (4 GB RAM, 2 vCPU, the user VM's CPUID masks, its own console pipe
`\\.\pipe\gbw4_con`, NAT adb at its own address; never the user's VM, pipe or adb address), disk from
`build_usb.py --vm-test --vm-test-rehearsal --sparse --userdata-gib 8`, ESP switches with the pipeline's
`w4-tree.img` / `old-tree.img` (`--vm-test-nogk`) and two hand-made negative ESPs (`tools\mk_negative_esp.py`; they fail
exactly verify_image V29 as they must). lapis CL3B.260622.276.R4 (`ro.build.fingerprint
ASUS/lapis/lapis:17/CL3B.260622.276.R4/16658739:user/release-keys`), k15 `6.18.21-android17-5-g3c2c2f588d3f-ab16302715`,
gb_gatekeeper 0.2.0 `505b404d...3d51`. Evidence: `C:\GB-build\native\dgpu\w4\evidence\rehearsal\` (tools\, run 1 =
`boot1..boot17\`, run 2 = `run2\` with `SUMMARY.txt`, `EVAL.txt`, one file per step, `artifacts\serial0_run2_all_boots.log.gz`).

* **Run 1** (VM tree `beb72fd6...0e19`, then `dec6ee8f...` after fix 1): every 9.2 step and 9.5 test passed, and
  found the two pipeline defects: (1) no `gb_gatekeeper:` line in dmesg (stdio_to_kmsg without `/dev/kmsg_debug`,
  7.3; fixed by `gb_kmsgdbg`, verified on the next boot); (2) in 1 of 17 boots no zram (lapis's vendor `modprobe zram`
  raced our insmod: `Device or resource busy`, mmd_setup SIGABRT; fixed by `gb_zram`, VM_UPGRADE.md 2.2(a)).
* **Run 2** (the final VM tree `1e939bc047f6d4092aeb61b7705235ecada75c1c44c742fbc6ffcf94a5842934  gb/inject.gz`, a
  freshly built disk, 17 boots, `tools\run_rehearsal.py`): **65 PASS, 3 FAIL, the 3 resolved as below = all pass.**
  Two FAILs were the evaluator's (step 5's unlock checks asked for a `dumpsys user` line those steps do not print;
  re-evaluated from the saved outputs: PASS, `run2\EVAL.txt`), one was the first version of the zram-race test (`rmmod
  zram` refused while mmd held zram0); its fixed version passed on the same VM (`run2_zramrace\`).

| Step / test | Result (run 2 unless noted) |
|---|---|
| 1 | dmesg `gb_gatekeeper 0.2.0 starting` (21.3 s), `registered ...IGatekeeper/default`, then `...ISharedSecret/gatekeeper`, `shared HMAC key computed with 2 participants`; no `E`, no exit 3; `init.svc.gb_gatekeeper` running; service list IGatekeeper/default once + ISharedSecret/gatekeeper + /default + IGateKeeperService; gatekeeperd found the HAL with no lazy-start wait; `/data/vendor/gatekeeper` `drwx------ hsm hsm gatekeeper_vendor_data_file`, empty; boot_completed at 74-84 s uptime in the 14 normal boots of run 2 (fresh /data: 193 s and 184 s), as run 1's boots before the fixes (73-82 s; one at 115 s while the host ran the test suites) |
| 2 | `--version` 0.2.0, `--selftest` PASS on the R4 libraries, sha256 `505b404d...3d51` |
| 3 | keystore2 `Shared secret negotiation concluded successfully` (observed after `logcat -G 16M; setprop ctl.restart keystore2`; system_server unaffected), no NOT PROVISIONED |
| 4 | `cmd lock_settings set-pin --user 10 1357`: `Pin set`; `gk_key.v1` = `.bak` (72 B, 0600 hsm), `boot_id` == kernel boot_id, `fr.100010`, `fr.10`; verify 1357 ok, 1358 `didn't match`; SoftwareRateLimiter `Enforcing: true`, `numFailures=1`; `/data/system_de/10/spblob/<id>.failure_counter` |
| 5 | lock screen, 1357 unlocks (counters 0); 5 wrong: `lockoutEndTime` +1 min, `numFailures=5`, HAL `verify uid=100010 -> retry in 60000 ms`; after the minute 1357 unlocks, both counters 0 |
| 6 | every unlock unwraps the auth-bound SP key; no `KEY_USER_NOT_AUTHENTICATED` / `Failed to unwrap` (V-9) |
| 7 | reboot: user 10 `RUNNING_LOCKED`, PIN prompt after picking "User"; 1357 unlocks; `verify uid=100010 -> ok`, `uid=10 -> ok`; same K_dev, no key created |
| **8 (W4-GK-1)** | 5 wrong PINs at 400-419 s uptime (HAL record counter 5, timestamp 418,570 ms), reboot at once; first attempt after the reboot at 94.5 s uptime: `new boot: failure-record timers run from boot (counters kept)`, `verify uid=100010 -> ok`, no `retry in`, counters 0, `boot_id` updated. 0.1.0 would have answered RETRY 60,000 (stored timestamp > now). **PASS: the 0.2.0 behaviour.** Second round: 6th failure at 418.4 s (`retry in 300000 ms`, `numFailures=6`), reboot at once; LockSettingsService then showed `lockoutEndTime=PT5M` from boot and refused an attempt at 87.7 s without asking the HAL; at 305.9 s 1357 unlocked at the first attempt (`new boot`, ok): **no tier added by a reboot** (run 1: same results, 104.4 s / 306.3 s) |
| 9 | `kill`: init restarted gb_gatekeeper and (onrestart) gatekeeperd; the PIN still unlocks; no `new boot` line after the restart; `W no shared HMAC key negotiated yet: signing auth tokens with the zero key` (as designed, 4) |
| 10 | `set-pin --old 1357 2468`, reboot, 2468 unlocks; `clear --old 2468` (`CredentialType: NONE`); reboot: no PIN prompt |
| 9.5 one copy damaged | primary: `key primary copy damaged, restoring it from the backup`; backup: `key backup damaged or different, rewriting it from the primary copy`; unlock works, `cmp` silent both times |
| 9.5 both damaged | `checksum mismatch` x2, `password key unusable ... refusing to create a new one`; no new key file; locked out (then 8.5) |
| 9.5 error counting + 8.3 + 8.2 | `chmod 000` both keys: the RIGHT PIN refused 5 times (`open ...: Permission denied`, `verify ... -> error (password key unavailable)`), HAL record unchanged, LockSettingsService `numFailures=5` + 1 min; 8.3 (chown/chmod/restorecon/restart) + 8.2 (rm both counters, reboot): 1357 unlocks at once, `numFailures=0`, data intact |
| 9.5 reboot during a lock-out | = step 8 |
| 9.5 registration refused (W4-GK-3) | hand-made tree without the fragment: `addService(...IGatekeeper/default) failed: exception -3` x5, `not registered: exiting with status 3`, init restarts it (14 exits in the first 100 s: a visible crash loop), gatekeeperd `Could not find Gatekeeper device, which makes me very sad.`; boot completes |
| 9.5 fragment without service | hand-made tree: no boot_completed in 300 s, gatekeeperd `Waited one second for ...IGatekeeper/default` / 515 lazy-start attempts, system_server restarted by its watchdog again and again (22 zygote kills): the documented boot hang; hard power-off, W4 ESP back: normal boot, data intact |
| 8.4 fix-forward | PIN set, old (no-Gatekeeper) ESP: the right PIN fails and is counted, gatekeeperd `has no HAL to talk to`; W4 ESP back + 8.2: 1357 unlocks, `gk_key.v1` sha256 unchanged, data intact |
| 8.6 rollback after a clear | PIN cleared, old ESP: boots to the desktop without a PIN prompt, data intact |
| 8.5 | at the lock-out above: the user's file copied out (root copy, `adb pull`), sha256 equal on the host; userdata wiped (first 16 MiB); fresh /data (`Enabling ext4 verity` once), no credential, no PIN prompt |
| other | zram swap on zram0 from mmd and mmd_setup exit 0 in all 16 completed boots of run 2 (`gb_zram: zram0 present (early-init insmod)` in each; the 17th boot is the deliberate hang); fs-verity on a new /data file (FS_VERITY_FL, digest, writes refused) and `verity` in tune2fs -l; VNNI summary readable, irqon == emulated; slot `_b`, gb_slotsync running, no HOLD; 0 `permissive=0` denials (the Gatekeeper's own denials are `permissive=1`: tmpfs labels of the binary/fragment, as gb_keymint); host after all boots: vbmeta_b (15,0,1), vbmeta_a (0,0,0), GPT valid, boot/vbmeta/super identical to lapis.bin, ESP == built image except `loader/random-seed` |

---

## 10. Open items and INFERRED points

* Integrated into both pipeline trees on 2026-10-26 (7.9), tested on the host and **rehearsed on a throwaway VM
  (9.6, all pass)**. Next: the user's VM per VM_UPGRADE.md 2.4-2.9 (its preview must match the rehearsed tree
  `1e939bc0...2934` by V30), then the native images and the ISO tests (9.3 on the Surface Book 3).
* Measured on the VM (9.6): binder registration (IGatekeeper first, then ISharedSecret), gatekeeperd / keystore2 /
  LockSettingsService interplay, the state directory's label, `user hsm` accepted by init on R4, `--selftest` on the R4
  libraries. Not measured: the native tree on the Surface Book 3 (9.3), an OTA with a PIN set (9.4).
* (Rehearsal) The HAL's summary line for an unreadable key (`password key unusable (primary damaged, backup
  damaged): refusing to create a new one; ... (recovery: factory reset)`) is the same for EACCES and for real damage;
  only the preceding `open ...: Permission denied` vs `checksum mismatch` line tells 8.3 from 8.5 (8.1 documents it).
  Not changed in code (it would need a new build, acceptance and review); suggested for the next gb_gatekeeper build:
  report "unreadable (errno)" separately from "damaged" and point at GATEKEEPER.md 8.1 instead of "factory reset".
* (Rehearsal) lapis's Trusty Gatekeeper rc is still parsed (GBINIT's whiteout lands after init's first parse), so init
  logs once per boot `Could not start service 'vendor.gatekeeper_default' ... has incorrect label` for the SK_STUB
  binary. It never runs (no crash loop, no declaration); the same line is in wave 3's VM boots. Harmless, left as is.
* (Rehearsal) lapis's logd keeps 256 KiB per buffer although `ro.logd.size.main=8M` is set (not investigated);
  boot-time logcat lines are lost within about a minute. The Gatekeeper's own lines are in kmsg since `gb_kmsgdbg`.
* lapis's `config_softwareLskfRateLimiterEnforcing`: **measured true** in R1's system framework-res, not overridden by
  the vendor RRO; product overlays not extracted (INFERRED unchanged). A persistent data block (`ro.frp.pst`) was not
  found in lapis's build.prop files (INFERRED inactive).
* Review items for wave 4: W4-GK-1 (must, before a real PIN), W4-GK-2 and W4-GK-3 (should) are **implemented in
  0.2.0 and tested off-device** (7.8, 9.1), each test proven to fail on the pre-W4 code; **device-confirmed on the
  rehearsal VM (9.6): W4-GK-1 by 9.2 step 8 (both rounds), W4-GK-3 by 9.5 "Registration refused"; the recovery tests
  of 9.5 pass.** W4-GK-2 (an internal crypto failure) has no device trigger and stays host-tested only.
* Residuals of the fixes: W4-GK-1 treats an unreadable kernel boot_id or a missing/damaged `boot_id` file as a new
  boot, which only shortens the HAL timer (LockSettingsService still enforces its own); W4-GK-2 cannot tell a failed
  *small* allocation inside libgatekeeper's private `CreatePasswordHandle` from a mismatch (INFERRED negligible);
  W4-GK-3 makes a lasting refusal a crash loop, it does not remove the boot wait while IGatekeeper is declared but
  absent. servicemanager's refusal of an undeclared instance was measured on the rehearsal: `exception -3`
  (EX_ILLEGAL_ARGUMENT, the code the fake service manager of the host test uses), servicemanager logging `Could not
  find android.hardware.gatekeeper.IGatekeeper/default in the VINTF manifest`.
  Found by the verification (LOW, documented in section 5, no code change): a system_server restart within a boot
  resets LockSettingsService's timer but not the HAL's, so one attempt in that gap is counted once more; and
  `setLockCredential`'s unlimited old-credential check can put the HAL counter above LockSettingsService's.
* The R4 libraries were not inspected (R1 images on the builder); only LLNDK symbols are used.
* No native `wipe_data` equivalent exists (section 8).
* If FBE is ever enabled: the design holds (K_dev on DE storage of /data, created after post-fs-data), but then a
  lock-out loses CE data; that needs its own decision and a /data wipe.
* A gb_gatekeeper crash in the middle of a boot leaves H at the zero fallback until reboot: harmless with the current
  KeyMint, a token-MAC failure with a verifying one.

---

## 11. Build and reproduce

On the builder (Ubuntu 24.04, NDK r27c at `/build/ndk`, everything at `nice -n 19 ionice -c3`):

```
cd ~/gk/proto/gatekeeper          # a copy of C:\GB-build\native\gatekeeper
REF_LIBBINDER=~/gk/lapis_sys/system/lib64/libbinder_ndk.so \
LAPIS_RT=~/gk/rt/payload LAPIS_SYSLIB=~/gk/lapis_sys/system/lib64 sh build.sh
```

The 0.2.0 acceptance run did that twice from empty output directories: first with
`OUT=~/gk/proto/gatekeeper/out_repro`, then into `out` with also `REGRESS_OLD=tests/regress/pre_w4_src` (the W4 tests
must fail on the reviewed pre-W4 sources, 9.1) and `COMPARE_WITH=~/gk/proto/gatekeeper/out_repro/android/SHA256SUMS`
(the build fails unless both binaries are byte-identical). The builder copy's sources (all 51 files, including the
reviewed VINTF fragment `48cd680b...` and `NOTICE`) are byte-identical to `C:\GB-build\native\gatekeeper`.
The verification (same day) re-ran it into an empty `vfy_w4/out_clean` and then into an emptied `out`, both with
`REGRESS_OLD` and `COMPARE_WITH=.../out_repro/android/SHA256SUMS` (same binary hashes as before: the tests changed,
not `src\`), and rebuilt the 0.1.0 binary from `tests\regress\pre_w4_src` (`out\verify\vfy_old.sh`: BoringSSL with
its DWARF directory mapped to the 0.1.0 build's `out/bssl-android`, the same compile and link lines, version
`0.1.0`), which gives the reviewed hashes exactly.

`~/gk/lapis_sys` = `fsck.erofs --extract` of `/build/system_a.img` (lapis R1 system); `~/gk/rt/payload` = the extracted
`apex_payload.img` of its `system/apex/com.android.runtime.apex`. BoringSSL is cloned from
`android.googlesource.com/platform/external/boringssl` and pinned to `0cd1f6a94b670e6b61af95a06e71e690ae8e3262`. No
prebuilt third-party binary is used; the emulator and lapis images are only read for analysis and for the off-device
run.

---

## 12. Sources (all fetched from android.googlesource.com, source only)

| Repository | Branch | Commit | Files read |
|---|---|---|---|
| platform/frameworks/base | android17-release | `94b4c163b7dfe5ce3607f7bb8456f9573f7de57d` | locksettings/{LockSettingsService, SyntheticPasswordManager, SyntheticPasswordCrypto, SoftwareRateLimiter, LockSettingsShellCommand}.java, core/res/res/values/config.xml |
| platform/system/core | android17-release | `545d2487e38192a2ce25040897ced877cf6b4f53` | gatekeeperd/*, rootdir/init.rc, libcutils/include/private/android_filesystem_config.h |
| platform/system/gatekeeper | android17-release (+ android16-release for the diff) | `5b5e75b5bda9fccbc3132e9624cb25286babdaac` | all (copied to `third_party\aosp\system_gatekeeper`) |
| platform/hardware/interfaces | android17-release / android16-release | `0162af698935100a590b7359581ac8b1b80693e5` / `5688f7eb1e117ed26e642a695de300b7683acb87` | gatekeeper/aidl/software/*, gatekeeper and sharedsecret AIDL + `.hash`, keymint HardwareAuthToken, compatibility_matrix.202604.xml |
| platform/hardware/libhardware | android17-release | `97ee3e33ec5ba48c0eca8341ed377ac4497a0678` | include_all/hardware/hw_auth_token.h |
| platform/system/security | android17-release | `cbb2ef1617a94d6cdf2dc2ccb7a545575dbb20cb` | keystore2/src/{shared_secret_negotiation, enforcements, super_key, authorization, maintenance}.rs |
| platform/system/keymaster | android16-release (our KeyMint's) | `1d26bd0ab27ccf3ed1af84e6514026ea4167bf3f` | km_openssl/{soft_keymaster_enforcement, ckdf, hmac}.cpp, android_keymaster/keymaster_enforcement.cpp, headers |
| platform/system/vold | android17-release | `6d20a0078c2bcaa2499dc5d032f337bfe1182d9e` | FsCrypt.cpp |
| platform/system/sepolicy | android17-release | `e066568e98d86db31a9346d30977f3632fa7073c` | vendor/hal_gatekeeper_default.te, vendor/file_contexts, public/hal_gatekeeper.te |
| platform/system/libvintf | android17-release | `df89ad736e14ac375f61d50fa543ae7cd746a140` | VintfObject.cpp, HalManifest.cpp |
| platform/frameworks/native | android17-release | `ae266dcb706d083868578cfedce381ef44488a07` | libs/binder/ndk/{parcel, ibinder}.cpp, include_platform/android/{binder_manager, binder_process, binder_stability}.h |
| platform/external/boringssl | android17-release | `0cd1f6a94b670e6b61af95a06e71e690ae8e3262` | built (libcrypto) |

Images read (not modified): `/build/emu36/x86_64/vendor.img` (Android 16 emulator), `/build/vendor_a.img` and
`/build/system_a.img` (lapis `CL3B.260622.271.R1`, build 16452207), `C:\GB-x86\emu\vendor` (Android 17 desktop
emulator, listing only). gbOS86 clone `<a local clone of github.com/linuxkid473/gbos-x86>`
(HEAD `f8a5c1a`): `gatekeeper/soft_gk.c`, `gatekeeper/build.sh`, `HANDOFF.md`, `usb/hook.sh`.
