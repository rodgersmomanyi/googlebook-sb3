# audio (native)

Internal audio of the Surface Book 3: Ice Lake SOF DSP (8086:34C8, IPC3) with the Realtek ALC274 codec, DMICs and
HDMI, on lapis's own vendor modules and its DRAS audio HAL. No kernel change.

* `tree/` mirrors `/odm`: `gb_audio.sh` (codec preload check, card wait, PCI re-probe, volume defaults, DRAS restart),
  `gb_audiodiag.sh` (report to `gblog\audio-*.txt`, `setprop debug.gb.audiodiag 1|test`), the HAL config override
  `vup/etc/data/hal_configs/audio_default.xml` and the card configs `vup/etc/audio/sof-hda-dsp_sb3/` (and
  `PCH_sb3_legacy/` for `snd_intel_dspcfg.dsp_driver=1`).
* `rc/gb_audio.rc`: services, appended to the native gb.rc.
* `modules/modules.load.audio`: the 8 lapis HDA codec modules gbinit preloads after i915 (GKI cannot
  `request_module`, so SOF would drop codecs that are not resident). The `.ko` files come from **your** lapis
  vendor_dlkm: `src/refresh_modules.sh <vendor_dlkm.img>` (checks their CRCs against our kernel).
* `src/build_tinyalsa.sh` + `src/tinymix-setall.patch`: static tinymix/tinyplay/tinycap/tinypcminfo.
* `tools/gen_test_tone.py`: writes `tree/gb_test_tone.wav`.
* Not included: `volume_curves.xml` (copy lapis's, see [docs/INPUTS.md](../../docs/INPUTS.md)), SOF firmware and
  topologies (sof-bin), the decoded topology references.

Status: untested on hardware.
