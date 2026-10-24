# ---- GB native audio (Surface Book 3) - paste into build_esp.py's native section ----
# 1) Add AFTER the GB_RC_NATIVE definition (before the _add_native loop that writes etc/init/gb.rc):
AUDIO_DIR = r"C:\GB-build\native\audio"
GB_RC_NATIVE += b"\n" + open(os.path.join(AUDIO_DIR, "rc", "gb_audio.rc"), "rb").read()

# 2) Add next to the other _add_native() calls (after `for _n, _d in NATIVE_MODS: ...`):
#    tree/ mirrors /odm: gb_audio.sh, gb_audiodiag.sh, gb_test_tone.wav, vup/bin/tiny*, vup/etc/...
for _n, _d in _tree(os.path.join(AUDIO_DIR, "tree")):
    _x = _n.endswith(".sh") or _n.startswith("vup/bin/")
    _add_native(_n, _d, 0o100755 if _x else 0o100644)
print(f"native audio: {len(_tree(os.path.join(AUDIO_DIR, 'tree')))} files", flush=True)

# 3) Modules (not done by this snippet): copy C:\GB-build\native\audio\modules\*.ko into
#    C:\GB-x86\inject\native\modules\ and append the 8 lines of modules\modules.load.audio to
#    inject\native\modules\modules.load (after i915.ko). gbinit then insmods them before Android init.
# 4) Firmware: already staged in inject\native\firmware\intel\sof\sof-icl.ri and \intel\sof-tplg\sof-hda-generic*.tplg
#    (-> /odm/firmware/intel/...). Nothing to add.
# 5) Cmdline: build_usb.py native_cmdline() already has snd_intel_dspcfg.dsp_driver=3 and
#    firmware_class.path=/odm/firmware,/vendor/firmware. Nothing to add.
