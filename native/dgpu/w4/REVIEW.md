> **Repository note.** A wave 4 working document (2026-10-26), copied from the development tree. Paths such
> as `C:\GB-build\...` and `C:\GB-x86\...` are the development layout (`C:\GB-build\native\<x>` =
> `native/<x>`, `C:\GB-x86` = the repository root, `D:\GB-vm-backup`, `D:\gb-w4test` = local backup and
> throwaway-VM folders, `builder:` / `~/gk` = the Linux builder); `evidence/` and the planning session's own
> files are not published, and details of the developer's own files were removed. For users:
> [docs/GATEKEEPER.md](../../../docs/GATEKEEPER.md).

# W4 review -- software Gatekeeper (GATEKEEPER.md + prototype) and the VM backup/upgrade plan (VM_UPGRADE.md)

Date: 2026-10-26, about 05:30-06:30 local (UTC+2). Adversarial review for data loss and lock-out: FBE / CE key binding,
key and handle persistence, throttling, OTA, a crash of the gatekeeper service, SELinux at boot, first boot vs
existing /data, backup completeness, restore correctness, disk budgets.

What this review touched: it wrote this file, edited `GATEKEEPER.md` and `VM_UPGRADE.md` (corrections listed in
section 5) and one data file of the prototype, `C:\GB-build\native\gatekeeper\vintf\android.hardware.gatekeeper-service.gb.xml`
(override on the sharedsecret HAL too, GK-6). No source file, binary, build output or acceptance record of the
prototype was changed (binary sha256 still `b62b205c...a46e`, re-hashed). Nothing under `C:\GB-x86` was written; the
user's VM was not started; `lapis.bin` was not opened. Builder: read-only use of the `~/gk` sources and lapis images,
the SDK `aapt2` 36.1.0 under `/build/native/dgpu/gpu-ui/sdk` run to dump two resource values, and one temporary
`~/gk/rv.*` folder (the strongbox APEX payload, extracted to read its VINTF fragment, then removed); nothing in
`/build/dist*`, `/var/tmp/gbiso` or the k15 dirs was touched. No downloads. On the host, only metadata of
`<a personal folder on D:>` and `D:\$Recycle.Bin` was listed (VM-5), and this session's own transcript and the user's
screenshot were read for the backup's provenance.

Tags: **INFERRED** = reasoned, not measured or read in source. Source citations are to the AOSP android17-release
clones in `~/gk/aosp/` on the builder (same commits as GATEKEEPER.md section 12).

---

## 1. Verdict

**Conditional GO.**

| Part | Verdict | Condition |
|---|---|---|
| Backup `<BK>` (`D:\GB-vm-backup\2026-10-26-pre-upgrade`) | **GO** | complete for a full restore; provenance now known (VM-5); must be re-paired with the live disk right before the ESP write (VM-1, applied as 2.4 step 0) |
| Restore paths (gb_vmbackup.py, 3.2 / 3.3) | **GO** | tool logic reviewed, no defect found; *when* to use which path was wrong once a PIN exists (VM-2, applied: post-upgrade ESP snapshot 2.9, new 3.0/3.1) |
| Disk budgets | **GO** | re-measured now: C: 87.60 GB, D: 54.08 GB free; W4 worst case on D: about 15 GB (floor 3 GB) |
| VM upgrade (k15, zram, fs-verity, wave-3 fixes) | **GO** after P1-P9 | no new data-loss path found; fs-verity bit already set (no superblock change) |
| Software Gatekeeper on the throwaway rehearsal VM and the V-8 set/verify/clear test | **GO** | rehearsal extended (VM-4) |
| **The user setting a real PIN** (VM or native) | **NO-GO until** | (1) W4-GK-1 (align the HAL's post-reboot timer with LockSettingsService) is applied and tested, or the user accepts GK-2 in writing; (2) the no-wipe recovery recipes of GATEKEEPER.md 8 have been run once in the rehearsal |

No finding loses data **today**, because /data has no file-based encryption (measured, GATEKEEPER.md 4) and a full
verified backup exists. The real exposures are **lock-outs** whose only documented recovery was "factory reset"
(a wipe). They are now classified, and each has a non-destructive recovery (INFERRED until the rehearsal runs them),
with the wipe as the last resort.

---

## 2. Findings -- software Gatekeeper

Severity: **High** = a realistic operation leads to a lock-out or to a wipe the user did not need; **Medium** = needs an
unusual event, or the docs mislead an operator; **Low** = cosmetic or accepted.

| # | Sev | Finding | Evidence | Correction |
|---|---|---|---|---|
| GK-1 | **High** | **LockSettingsService counts every Gatekeeper non-match, not only wrong PINs, and never drops duplicates here.** Its software rate-limiter increments its *persistent* counter for any response that is not a match: a wrong PIN, a Gatekeeper `RETRY`, a service-specific error, a `RemoteException` (all `OTHER_ERROR`). Duplicate detection only works for `credIncorrect`, which only Weaver's `INCORRECT_KEY` produces. lapis enforces it. So any period in which gb_gatekeeper refuses (self-test failed, key file unreadable, wrong owner/label, crash loop) turns each unlock attempt with the **correct** PIN into a counted failure, escalating 1 min, 5 min, ... 41 d, and the counter survives the fix. GATEKEEPER.md 5 ("duplicate wrong guesses not counted") and 9.2 step 5 ("drops repeated identical wrong guesses") were wrong for this device. | `LockSettingsService.java:2723-2747` (every non-match goes to `reportFailure`); `SoftwareRateLimiter.java:418-447` ("Increment the failure counter regardless ... Gatekeeper only ever returns generic failures", `numFailures++` at :446); `SyntheticPasswordManager.java:783` (only `credIncorrect` producer, Weaver); counter file `/data/system_de/<u>/spblob/<%016x protector>.failure_counter` (`LockSettingsStorage.java:482,494`, `SyntheticPasswordManager.java:160`), damaged/missing = 0 (`:2289-2310`). **Measured:** lapis R1 `framework-res.apk` `bool/config_softwareLskfRateLimiterEnforcing = true`; the vendor RRO `framework-res__lapis__auto_generated_rro_vendor.apk` does not override it (aapt2 36.1.0 dump); product overlays not extracted (INFERRED unchanged). | GATEKEEPER.md 5, 9.2, 10 corrected; new 4 rows; new no-wipe recovery 8.2 (delete both counters as root, reboot; INFERRED, rehearsal test added in 9.5). |
| GK-2 | **High** | **After a reboot with a pending lock-out, the first attempt that reaches the HAL is refused and LockSettingsService escalates one tier.** Our HAL persists failure records (design choice); libgatekeeper restarts the full timeout at the first attempt whose timestamp is at or before the stored one, i.e. after almost every reboot, and answers `RETRY` even for the right PIN. LSS's own timer restarts from boot and LSS cannot query a Gatekeeper's remaining timeout, so it lets that attempt through and then counts the `RETRY` (GK-1). Each reboot while the HAL count is >= 5 and not yet cleared adds one tier. On a VM that is shut down daily, tiers of 12 h and more need that much continuous uptime (both timers restart at boot): a practical lock-out. The host test `test_throttling` (:472-476) proves the HAL half; 9.2 step 8's expectation "refused for a full 1 min, then accepted" is wrong (expected: refused, then a 5 min countdown, then accepted). | `gatekeeper.cpp:150-157` (throttle, increment before verify), `:338-366` (reboot rule); `SoftwareRateLimiter.java:276-300` (state from counter, `timeSinceBootOfLastFailure` = 0 at boot); `SyntheticPasswordManager.java:2361-2370` (hardware timeout query only for Weaver); `tests\test_main.cpp:472-476`. | **W4-GK-1 (required before a real PIN):** on the first record load of a new boot (boot_id from `/proc/sys/kernel/random/boot_id` differs from the one stored in the state dir), set `last_checked_timestamp = 0`, so the HAL timer also runs from boot and never refuses an attempt that LSS let through; keep the counter. Same-boot restarts keep the timestamp. Test: the reboot case in `test_throttling` must then return the remaining time measured from boot, and an attempt after LSS's timeout must reach DoVerify. Written into GATEKEEPER.md 5 and 7.8. |
| GK-3 | **High** | **The lock-out recovery said "factory reset" for cases that do not need one.** With FBE off, K_dev, the handles and the user's data all survive these causes: gb_gatekeeper missing or broken, an older tree booted (ESP-only rollback, pre-W4 fallback images such as `D:\GB-iso\prev\...`), key files with a wrong owner or label, throttling. Recovery is fix-forward (boot a tree with the same gb_gatekeeper scheme), `chown`/`restorecon`, or clearing the two counters. Only a forgotten PIN or a truly lost K_dev needs a wipe, and even then the user's files can be copied out first while FBE is off. | design reading; `unlockLskfBasedProtector` (`SyntheticPasswordManager.java:1534-1690`: any Gatekeeper response other than OK or RETRY is `OTHER_ERROR`, counted per GK-1); `gatekeeperd.cpp:116-128` (old tree: no HAL declared, "Could not find Gatekeeper device", every call an error) | GATEKEEPER.md 8 rewritten (8.1-8.6); VM_UPGRADE.md 3.0/3.1 rewritten; new 2.9 post-upgrade ESP snapshot = the fix-forward target for the VM. |
| GK-4 | Medium | **The "only a first enrollment creates K_dev" guard is nominal.** Android 17 enrolls every LSKF protector and the SP handle with *no* current handle (only the rare re-enroll passes one), so in practice the rule is "any enrollment when both key files are ENOENT on a directory that opened". After a real K_dev loss the next enrollment of any user, or an escrow-token reset, silently creates a new key. No extra data is lost (the old handles were already unverifiable) and the host test asserts exactly this (`test_key_loss_does_not_count` :526-528), but the doc's "a PIN change while K_dev is missing fails without creating one" is only true because LSS verifies the old PIN first. | `SyntheticPasswordManager.java:1139` (LSKF enroll, null handle), `:916-918` (SP handle enroll, null), `:1618`/`:1899` (re-enroll only) | GATEKEEPER.md 6.2 corrected. |
| GK-5 | Medium | **"A KeyMint swap later does not break the PIN" is misleading.** Correct MACs keep tokens valid, but every existing Keystore key blob belongs to the KeyMint implementation that made it, including the auth-bound key that wraps LSS's synthetic-password blob. Replacing the KeyMint (the Rust nonsecure one, a new graft) makes the right PIN unable to unlock (unwrap fails) and loses app keys, PIN or not (INFERRED: blob formats are implementation-specific). | `SyntheticPasswordCrypto.java:189-207` (SP blob key in AndroidKeyStore), GATEKEEPER.md 3 | GATEKEEPER.md 3 and 4 corrected: a KeyMint change is its own migration (clear the PIN first, accept loss of app keys). |
| GK-6 | Medium | **A second `ISharedSecret/gatekeeper` declaration would fail the whole device manifest.** libvintf stops at the first fragment that cannot be added and returns the error; the device manifest then fails and no vendor AIDL HAL is declared (boot failure, not data loss: RescueParty and its factory reset are disabled in both trees). lapis already labels `ISharedSecret/gatekeeper` in `vendor_service_contexts:2`, so a later lapis may declare it. The fragment had `override` only on the gatekeeper HAL. | `VintfObject.cpp:234-266` (`addDirectoryManifests` returns the first error), `:316-354`; `HalManifest.cpp:219-235` (override removes same-name AIDL HALs first); lapis R1 vendor `manifest.xml` and fragments declare no ISharedSecret (grep); of the six vendor APEX payloads only the strongbox one mentions "sharedsecret" (a binary string), and its extracted VINTF fragment declares only `IKeyMintDevice/strongbox` and `IRemotelyProvisionedComponent/strongbox` (temp extract in `~/gk`, removed); `build_esp.py:917-919, 1556-1558` (RescueParty off) | **Applied:** `override="true"` on the sharedsecret HAL in the fragment (removes nothing in lapis R1). Trade-off written in GATEKEEPER.md 7.2. Add a byte check of the fragment to 7.5. |
| GK-7 | Medium | **A crypto failure during verify counts as a wrong guess.** libgatekeeper writes the incremented failure record before computing the signature; if scrypt fails (its 16 MiB allocation), the adapter returns an error but the HAL counter stays incremented and persisted, and LSS counts the error too (GK-1). | `gatekeeper.cpp:150-157`; `src\aidl_service.cpp:316-321`; `src\gb_gatekeeper.cpp:106-122` | **W4-GK-2 (should):** when `CryptoFailed()` after Verify, restore the record read before the call and persist it. Doc: GATEKEEPER.md 5, 7.8. |
| GK-8 | Low | **Boot hang if IGatekeeper registration fails while the process lives.** After 5 failed `addService` attempts the service idles in `joinThreadPool`; `interface aidl` cannot restart a running service; gatekeeperd waits forever; system_server's watchdog loops (no wipe: RescueParty off). Needs a declared instance that servicemanager still refuses, e.g. a future enforcing policy. | `src\service_main.cpp:56-66, 111-115`; `gatekeeperd.cpp:116-118` | **W4-GK-3 (should):** exit non-zero if IGatekeeper did not register, so init restarts it and the failure is visible as a crash loop. Plus the pairing check binary <=> fragment <=> rc (7.5). |
| GK-9 | Low | A broken ISharedSecret participant stops keystore2's negotiation: phase 1 retries forever, phase 2 (`try_fold`) stops at the first error, so the KeyMint may never get `computeSharedSecret`. Harmless with today's KeyMint (it does not verify token MACs); background thread, no boot block. | `shared_secret_negotiation.rs:230-275`, spawn at :44 | noted in GATEKEEPER.md 4. |
| GK-10 | Info | Verified true: gatekeeperd waits forever when IGatekeeper is declared (`gatekeeperd.cpp:116-118`), `chdir` in `main.cpp:30`, `.coldboot` deleteAllUsers (`:142-171`, also from `reportDeviceSetupComplete`); `early_hal` at late-fs (`init.rc:635-642`), `/data/misc/gatekeeper` 0700 system (`init.rc:777`); every domain permissive (`inject\odm_sepolicy.cil`: 2,694 `typepermissive` lines incl. gatekeeperd, keystore, servicemanager, hal_gatekeeper_default, init, system_server, vold); `hal_gatekeeper_server` includes `hal_gatekeeper_default`, keystore is a `hal_gatekeeper_client` (lapis `vendor_sepolicy.cil:319-320`); libvintf `kMetaVersion{9, 0}` and our `se_manifest.xml` already uses 9.0; component `mode` "0644"/"0o100755" both parse (`build_esp.py:1783-1786`); lapis vendor rc creates no `/data/vendor/gatekeeper`; FRP `config_enableCredentialFactoryResetProtection = true`, no `ro.frp.pst` in lapis build.prop; binary sha256 matches `SHA256SUMS`. | as cited | none needed |
| GK-11 | Info | SELinux at boot: no denial can block (everything permissive). If the policy is ever made enforcing, the lapis policy already allows the flows (GK-10), but the new files' labels are still untested on a device (9.2 step 1). FBE: off today, so no CE-key binding risk now; if FBE is turned on later, a lock-out loses CE data and K_dev must stay on DE storage (it does: `/data/vendor` is DE). | as cited | unchanged |

## 3. Findings -- VM backup and upgrade plan

| # | Sev | Finding | Evidence | Correction |
|---|---|---|---|---|
| VM-1 | **High** | **The backup was paired with the live disk hours before the write, never at the write.** Step 1.4 (about 07:15) proves `<BK>` equals lapis.bin; the ESP write (2.4) is planned for about 12:00. If the user uses the VM in between, `<BK>` and `<BKX>` are stale: a later 3.3 would silently roll the user back to 00:18 today, and 2.5's "only EFI-SYSTEM differs" would fail *after* lapis.bin was written. | VM_UPGRADE.md 1.3, 2.0 P1, 2.4 (only `vmrun list` before the write) | **Applied:** 2.4 step (0) re-hashes the live disk and requires `PAIRED` (3 min) or a fresh backup first; new precondition P9. |
| VM-2 | **High** | **Rollback with a PIN.** The only ESP image kept is the *pre-upgrade* one (no Gatekeeper), so after the user sets a PIN every ESP-only rollback is a lock-out unless the PIN is cleared first, and 3.0 sent a locked-out user to 3.3 (full restore = everything since 26/10 00:18:46 lost). | VM_UPGRADE.md 3.0, 3.1 | **Applied:** new 2.9 post-upgrade ESP snapshot `<BKP>` (0.2 GB, taken after V-8, before the user's real PIN) = the fix-forward target; 3.0/3.1 rewritten (fix-forward first, copy data out before any 3.3 or wipe). |
| VM-3 | Medium | 2.2(c), 2.4's expected tree diff ("gatekeeper empty-VINTF bind gone"), V-9 ("auth-bound-key errors expected possible") and section 4 ("no throttling, fixed keys") still described the gbOS86-style service, not the one Task A built. Following 2.2(c) literally (remove the Trusty bind, `class hal`, `user system`) would let system_server read K_dev and lose the bind's protection. | VM_UPGRADE.md 2.2(c), 2.4, 2.7, 4 vs GATEKEEPER.md 7 | **Applied:** all four rewritten to GATEKEEPER.md 7. |
| VM-4 | Medium | The rehearsal (2.3) tested set/verify/clear only: not the lock-out and recovery paths this review relies on (counter clearing, fix-forward after an ESP-only rollback with a PIN set, the ESP-only rollback after a PIN clear, the reboot escalation of GK-2). | VM_UPGRADE.md 2.3 | **Applied:** 2.3 now runs GATEKEEPER.md 9.2 and 9.5 on the throwaway VM, plus the two rollback cases. |
| VM-5 | Info (resolved) | **Backup provenance and the 35 GB freed on D:.** `<BK>` was made by the orchestrating session of this workflow: its transcript shows `scratchpad\vm_backup.py` written at 02:34:35Z (04:34:35 local), run at 04:34:42, ending `BACKUP OK in 647s` with all 9 files `VERIFIED`. The space on D: was freed by the user: their screenshot at 04:33:43 shows "Local Disk (D:) 95.6 GB free of 391 GB", and the session recorded "user freed more space". The top level of `<a personal folder on D:>` changed at 04:35:07; a search of today's session transcripts for that folder found only directory listings, no delete. That it was the user's own activity is INFERRED. `<BK>\SHA256SUMS` format checked: lowercase, LF, no BOM, `<sha>  <name>`: `gb_vmbackup.py verify/restore` parse it. | the planning session's transcript and a screenshot (not published); `Get-Item`/`Get-ChildItem` metadata only | VM_UPGRADE.md 0 updated. |
| VM-6 | Low | Disk budget re-measured: C: 87.60 GB, D: 54.08 GB free (D: -1.07 GB since 04:50; not in `D:\GB-iso\*`, `D:\gb-w2test`, `D:\gb-test`, `D:\gb-carve`; cause not identified). With the new 2.9 snapshot (-0.2 GB) the W4 worst case on D: is about 15 GB (floor 3 GB); after wave 3 about 46.6 GB. An optional sparse backup of the *upgraded* VM (-23.5 GB) still fits. | `Get-PSDrive`, folder sums | VM_UPGRADE.md 1.2 updated. |
| VM-7 | Low | mmd's 8 GiB writeback file stays on /data after an ESP-only rollback (the old tree's mmd aborts before using it). Wastes space, no loss. INFERRED. | VM_UPGRADE.md 2.2(a) | noted in 3.2. |
| VM-8 | Low | `<K15_REL>` and `<K15_DIST>` exist now (`C:\GB-build\kernel\k15_release.txt` 04:58:09 = `6.18.21-android17-5-g3c2c2f588d3f-ab16302715`; `/build/dist15` present). Wave 3 can still rebuild: re-read at P2. | file and builder listing | placeholders table updated. |
| VM-9 | Info | Restore correctness checked by reading `gb_vmbackup.py`: full restore verifies the backup before writing, rewrites differing 4 MiB blocks in place, re-hashes the whole disk and keeps the replaced small files; `--esp-only` checks the image hash and the partition offset/size and reads back. No defect found. `<BK>` holds everything the VM needs to run (disk, descriptor, vmx, vmxf, vmsd, nvram); not in it but not needed: `lapis-boot.vmx.pre-*` history copies, `vmware*.log`, `*.scoreboard`, `lapis-recovery.zip` (7.7 GB install source, still on C:). The two `*.REDO_*.lck` folders are empty and no REDO log file exists. | `Get-ChildItem C:\GB-x86`; Appendix B code | none |

## 4. What the "no-wipe" recoveries assume (all INFERRED until the rehearsal)

* Root shell: the VM's COM2 root console (`gbsh.py`, the user's tool). Native: no root shell on a user build; boot a
  Linux stick and mount the internal `userdata` (FBE off, so the files are plain ext4).
* LockSettingsService reads its counter only when it first needs it after boot, and the HAL caches its records, so
  both files are deleted with the device at the lock screen and then the device is rebooted.
* `adb` (our trees pre-authorise the host key) can pull `/sdcard` while the screen is locked when FBE is off.

## 5. Corrections applied

**GATEKEEPER.md:** status line; 1 (records note); 3 (KeyMint swap, GK-5); 4 (new rows: service refusing, reboot
escalation, sharedsecret duplicate, older tree booted, KeyMint replaced, ISharedSecret broken; registration residual;
K_dev-loss recovery; FRP measured); 5 (LSS counting, measured enforcing, reboot interaction, crypto-failure counting,
practical-tier note); 6.2 (creation guard, GK-4); 7.2 (override on both HALs, trade-off, meta-version); 7.3 (mkdir on
an existing directory; 2.2(c) wording); 7.5 (fragment byte check, pairing rule, porting check); new 7.8
(W4-GK-1..3); 8 (rewritten: 8.1 classification, 8.2 counters, 8.3 service broken, 8.4 older tree booted, 8.5
forgotten PIN / K_dev lost, 8.6 avoidable); 9.2 steps 4, 5, 8, 11; 9.5 (new recovery tests); 10 (open items).

**VM_UPGRADE.md:** review note; placeholders (+`<BKP>`); 0 (provenance, re-measured space); 1.2 (budget rows); 1.3
(re-pair); 2.0 (P5, P9); 2.2(c) (rewritten to GATEKEEPER.md 7) and 2.2(d) (pairing check); 2.3 (rehearsal scope);
2.4 (step 0 re-pair, expected diff); 2.6 (Gatekeeper lines); 2.7 (V-7, V-8, V-9); 2.8 (done criterion); new 2.9
(post-upgrade ESP snapshot); 3.0, 3.1 (rewritten), 3.2 (`<BKP>`, mmd file), 3.3 (copy out first), 3.4 (`<BKP>`
values); 4 (risk rows); 5 (k15 build state, extra rehearsal time). Appendix B (`gb_vmbackup.py`) was not touched; its
extraction check still passes (sha256 `458514b8...2ae0`, re-run after the edits, byte-identical to the tested
scratchpad copy). The new 2.4 step (0) one-liner was run on a stand-in file (`PAIRED` / `CHANGED` both as expected).

**Prototype:** `vintf\android.hardware.gatekeeper-service.gb.xml` only (GK-6; parses, `override="true"` on both
HALs, no CR/tab). The builder's copy `~/gk/proto/gatekeeper/vintf/` still has the old file (not used for
integration). The code findings W4-GK-1..3 are left to wave 4 on purpose: they change the accepted binary and need
their own build, host tests, lapis-bionic run and device test.

## 6. Open items for the orchestrator / user

1. W4-GK-1 (required before the user's real PIN), W4-GK-2 and W4-GK-3 (should): GATEKEEPER.md 7.8.
2. The rehearsal must run GATEKEEPER.md 9.5's recovery tests before the no-wipe recoveries can be called tested.
3. Confirm with the user that they removed the files from `<a personal folder on D:>` themselves around 04:33-04:35 (VM-5:
   everything points to it, nothing proves it).
4. D: lost 1.07 GB between 04:50 and about 05:40 for an unidentified reason (VM-6); harmless for the budget.
5. lapis product overlays were not extracted; `config_softwareLskfRateLimiterEnforcing` is `true` in system
   framework-res and not overridden by the vendor RRO. 9.2 step 4 prints the live value (`Enforcing:`).
6. Moving the stale `.lck` folders (VM_UPGRADE.md 2.1) still needs the user's OK.

---

## 7. Verification of the W4 code fixes (gb_gatekeeper 0.2.0)

Date: 2026-10-26, about 07:00-07:30 local. Independent check of the wave-4 fix report (W4-GK-1, W4-GK-2, W4-GK-3;
binary `505b404d...3d51`). This section was added; sections 1-6 above are unchanged and describe 0.1.0: their
`src\` and `tests\test_main.cpp` line numbers refer to the 0.1.0 sources, now kept in
`C:\GB-build\native\gatekeeper\tests\regress\pre_w4_src\`, and the remark in 5 that the builder's VINTF fragment is
stale no longer holds (all 51 source files of the builder copy are byte-identical to the Windows copy, re-hashed).

**Verdict: the three fixes are correct as specified; no High or Medium defect found.** One test added, two Low
residuals documented, no source file of the service changed (binary unchanged). Section 1's NO-GO for a real PIN
stays until GATEKEEPER.md 9.2 step 8 and the 9.5 recovery tests have run on the VM.

| Check | Result | Evidence |
|---|---|---|
| The kept pre-W4 sources are the reviewed code | **proven**: `tests\regress\pre_w4_src` rebuilt with the 0.1.0 recipe (BoringSSL with its DWARF directory mapped to the 0.1.0 build's `out/bssl-android`, the same compile/link lines, version `0.1.0`) gives exactly `b62b205c...a46e` (stripped) and `9d3b1efd...21d2` (unstripped), the hashes of `~/gk/proto/build10.log`; the citations of section 2 (`aidl_service.cpp:316-321`, `gb_gatekeeper.cpp:106-122`, `service_main.cpp:56-66, 111-115`) match that copy | `gatekeeper\out\verify\vfy_old.sh`, `vfy_old.log`, `old_src.sha256` |
| Diffs read | 6 files changed (`gk_store.h/.cpp`, `gb_gatekeeper.h/.cpp`, `aidl_service.cpp`, `service_main.cpp`), 7 unchanged; only new import `read@LIBC` (imports of the rebuilt 0.1.0 vs 0.2.0) | `diff -u` against `pre_w4_src` |
| Each new test fails on the pre-fix code | **yes**, own build (not `regress_old.sh`) of the current tests against the proven copy: 121 passed, 51 failed; `test_throttling` 22 (e.g. RETRY 60,000 instead of 59,950 after the reboot; RETRY 49 ms instead of an unlock at 60,001 ms), `test_reboot_loop_no_bypass` 14 (new, below), `test_crypto_failure_not_counted` 10 (counter 5 after the crypto failure, then RETRY 59,980 for the right PIN), `test_registration` 3 (`IGatekeeper refused: serving=1 exited=0`), plus the 2 boot_id file checks. `build.sh` with `REGRESS_OLD` agrees | `out\verify\regress_own.txt`; `out\regress\summary.txt` |
| Current code | 172 passed, 0 failed (host ASan/UBSan and on lapis bionic), ref check PASS | `out\host\test.log`, `out\android\test\test.log` |
| Reproducibility of 0.2.0 | a third clean build into an empty `vfy_w4/out_clean` and the acceptance build into an emptied `out` both give `505b404d...3d51` / `b3c68729...3597`, identical to the fixer's `out_repro` | `out\verify\out_clean.ACCEPTANCE.txt`, `out\ACCEPTANCE.txt` |
| LockSettingsService's expectations (Android 17) | match: state rebuilt at first use after boot from the persistent counter with `timeSinceBootOfLastFailure = 0` (`SoftwareRateLimiter.java:168-172, 276-300`), lock-out end = that + T(n) (`:316-321`), earlier attempts refused by LSS itself (`:240-248`), every non-match counted (`:438-447`, `LockSettingsService.java:2723-2747`), hardware timeout queried only for Weaver (`SyntheticPasswordManager.java:2361-2370`), clock `elapsedRealtime()` (`LockSettingsService.java:679-680`) = CLOCK_BOOTTIME = the HAL's. 0.2.0's HAL lock-out after a new boot also ends at T(n) after boot and sees each attempt after LSS did, so an attempt LSS lets through is not answered `RETRY` while the HAL counter is not above LSS's | GATEKEEPER.md 5 |
| No reboot bypass | **none**: `ResetRecordTimestamps` and `CachedRecord` change only `last_checked_timestamp`; counters and SIDs are never reset by the boot logic; a refused attempt is not counted; every boot comes after the last counted failure, so a tier served from boot ends later in real time. New `test_reboot_loop_no_bypass`: 20 reboots and 3 starts without `boot_id` during a lock-out never unlock early or change a counter; the right PIN is accepted only at the full 300,000 ms after the last boot. W4-GK-2 restores a record only when the signature could not be computed (random bytes, so no comparison is possible) or libgatekeeper failed internally (e.g. an allocation in `MintAuthToken`); a mismatch (`ERROR_INVALID`) or a throttle answer is never restored, so it gives no free guess | `tests\test_main.cpp`, GATEKEEPER.md 5 |
| boot_id readable under lapis's policy | yes: `plat_sepolicy.cil:197` (`/sys/kernel/random` -> `proc_random`), `:24677, 24682` (`allow domain proc_random` dir/file read) | lapis R1 system image on the builder |

Residuals found (all Low, documented in GATEKEEPER.md 5 and 10, no code change):

| # | Sev | Residual | Why not changed |
|---|---|---|---|
| V-1 | Low | A **system_server restart** without a kernel reboot resets LSS's timer to "from boot" while the HAL keeps its timer (same boot_id): an attempt in that gap gets `RETRY` and is counted, one tier per restart during a pending lock-out. | Same as every Trusty Gatekeeper; forgetting the timer within a boot would weaken the HAL limiter against a compromised system_server, which cannot read K_dev. |
| V-2 | Low | `setLockCredentialInternal` verifies the old credential without LSS's limiter (`LockSettingsService.java:2185-2202`), so the HAL counter can exceed LSS's; after a reboot the HAL then holds out longer and its `RETRY` is counted until both agree. Settings and `cmd lock_settings` check the old PIN through the limited path first (`LockSettingsShellCommand.java:116, 337-349`). | Needs a privileged caller passing a wrong old PIN directly; bounded. |
| V-3 | Low | If the kernel boot_id is unreadable or the `boot_id`/record rewrite keeps failing, every **HAL** restart within a boot is treated as a new boot (timers from boot). | Fail-open only on the timer, never on a counter; LSS still enforces; restarting the HAL needs system or root. Already listed as a residual by the fix. |

Written by this verification: `gatekeeper\tests\test_main.cpp` (`test_reboot_loop_no_bypass`, header comment),
`gatekeeper\tests\regress_old.sh` (the new test in its list, neutral label for the other failures),
`gatekeeper\tests\regress\README` (the proof), `gatekeeper\out\` (replaced by the acceptance build, plus
`out\verify\`), GATEKEEPER.md (status, 5, 7.8, 9.1, 9.2 step 8 fail text, 10, 11) and this section; the builder copy
and its `vfy_w4/` work folder. Not touched: `src\`, the binary, rc, VINTF, component JSON, VM_UPGRADE.md, the VM,
`C:\GB-x86`, `/build/dist*`, `/var/tmp/gbiso`, the k15 dirs.
