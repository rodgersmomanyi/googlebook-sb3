#!/system/bin/sh
# GB native audio helper (Surface Book 3). Started in the background from gb.rc at post-fs-data, as root.
#  1. Makes sure the HDA codec drivers are resident before SOF probes the codecs. The kernel cannot
#     request_module() (CONFIG_MODPROBE_PATH=""), so a codec module that is missing at probe time makes SOF drop
#     that codec. gbinit normally preloads lapis copies of them; if those copies do not link with the booted
#     vendor_dlkm's SOF modules (another lapis build), it swaps in the vendor_dlkm ones and loads SOF itself.
#  2. Waits for the internal card (SOF "sof-hda-dsp", id sofhdadsp). If the card or the ALC274 codec is missing,
#     re-probes the audio PCI device once.
#  3. Sets the HDA codec volume defaults in percent (their ranges are codec-specific). The SOF PGAs and the
#     switches are set in mixer_paths.xml.
#  4. Restarts the DRAS audio HAL if it started before the card was ready.
# Legacy fallback: with snd_intel_dspcfg.dsp_driver=1 on the cmdline, it loads snd-hda-intel (blocklisted for
# modalias autoload in vendor_dlkm) and points the HAL config at the PCH card.
exec >/dev/kmsg 2>&1
L() { echo "GB_AUDIO: $*"; }
TM=/vendor/bin/tinymix
VM=/vendor/lib/modules
MP=/vendor/bin/modprobe
HAL=vendor.audio-hal-aidl
DSP=$(cat /sys/module/snd_intel_dspcfg/parameters/dsp_driver 2>/dev/null)
ID=sofhdadsp
L "start uptime=$(cut -d' ' -f1 /proc/uptime) dsp_driver=$DSP"

N=
find_card() {
  for c in /proc/asound/card[0-9]*; do
    [ "$(cat $c/id 2>/dev/null)" = "$ID" ] && { N=${c##*/card}; return 0; }
  done
  return 1
}
analog_ok() {
  [ "$ID" = sofhdadsp ] || return 0
  grep -qsi -e realtek -e alc2 /proc/asound/card$N/codec#* && return 0
  $TM -D $N controls 2>/dev/null | grep -q -e "Speaker Playback" -e "Headphone Playback"
}
hda_pci() {
  for d in /sys/bus/pci/devices/*; do
    [ "$(cat $d/vendor 2>/dev/null)" = 0x8086 ] || continue
    case "$(cat $d/class 2>/dev/null)" in 0x040100|0x040380|0x040300) echo $d; return;; esac
  done
}
wait_for() {  # wait_for <seconds> <command...>
  w=$1; shift; i=0
  while ! "$@" && [ $i -lt $w ]; do sleep 1; i=$((i+1)); done
  "$@"
}
card_ready() { find_card && analog_ok; }
sof_loaded() { [ -d /sys/module/snd_sof_pci_intel_icl ]; }
hal_stop() { RUN=$(getprop init.svc.$HAL); [ "$RUN" = running ] && { setprop ctl.stop $HAL; sleep 2; }; }
hal_back() { [ "$RUN" = running ] && setprop ctl.start $HAL; RUN=; }

HAD=0; card_ready && HAD=1

if [ "$DSP" = 1 ]; then
  ID=PCH
  S=/vendor/etc/audio/PCH_sb3_legacy/audio_default.xml
  cat $S > /vendor/etc/data/hal_configs/audio_default.xml 2>/dev/null ||
    cat $S > /odm/vup/etc/data/hal_configs/audio_default.xml
  L "legacy HDA mode: HAL config -> PCH_sb3_legacy"
fi

# codec drivers first (no-op when gbinit preloaded them)
$MP -a -d $VM snd_hda_codec_alc269 snd_hda_codec_intelhdmi || L "modprobe codec modules failed"

if [ "$ID" = PCH ]; then
  $MP -d $VM snd_hda_intel || L "modprobe snd_hda_intel failed"
elif ! wait_for 30 sof_loaded; then
  if dmesg | grep -q -E "snd[_a-z0-9]*: (disagrees about version of symbol|Unknown symbol)"; then
    L "vendor SOF modules reject the preloaded HDA modules: swapping in the vendor_dlkm copies"
    for m in snd_hda_codec_intelhdmi snd_hda_codec_hdmi snd_hda_codec_alc269 snd_hda_scodec_component \
             snd_hda_codec_realtek_lib snd_hda_codec_generic snd_hda_ext_core snd_hda_codec snd_hda_core; do
      [ -d /sys/module/$m ] && { rmmod $m || L "rmmod $m failed"; }
    done
    $MP -a -d $VM snd_hda_codec_alc269 snd_hda_codec_intelhdmi || L "modprobe vendor codec modules failed"
  fi
  L "SOF not loaded by ueventd: modprobe snd_sof_pci_intel_icl"
  $MP -d $VM snd_sof_pci_intel_icl || L "modprobe snd_sof_pci_intel_icl failed"
fi

if ! wait_for 60 card_ready; then
  P=$(hda_pci); DRV=$(readlink $P/driver 2>/dev/null); DRV=${DRV##*/}
  L "card $ID or its ALC274 codec missing (card=${N:-none}, pci=${P##*/}, driver=${DRV:-none}): re-probing"
  if [ -n "$P" ]; then
    hal_stop   # DRAS keeps the mixer open and unbind waits for the card's files to close
    [ -n "$DRV" ] && echo ${P##*/} > /sys/bus/pci/drivers/$DRV/unbind
    sleep 1
    echo ${P##*/} > /sys/bus/pci/drivers_probe
    REB=1
    wait_for 60 card_ready && L "card ready after re-probe (card$N)" || L "still no complete card after re-probe"
    hal_back
  fi
fi
if ! find_card; then
  P=$(hda_pci)
  L "giving up: no $ID card; pci=${P##*/} class=$(cat $P/class 2>/dev/null) driver=$(readlink $P/driver 2>/dev/null)"
  L "cards: $(cat /proc/asound/cards 2>/dev/null | tr '\n' '|')"
  exit 1
fi
L "card $ID = card$N"

# HDA codec volumes (100% = 0 dB on the Realtek DAC amps; ADC around 0 dB; +10 dB mic boost). Missing names fail quietly.
setv() { $TM -D $N set "$1" "$2" >/dev/null 2>&1 && L "mixer '$1' = $2"; }
setv "Master Playback Volume" 100%
setv "Speaker Playback Volume" 100%
setv "Headphone Playback Volume" 100%
setv "Master Playback Switch" 1
setv "Speaker Playback Switch" 1
setv "Headphone Playback Switch" 1
setv "Capture Volume" 40%
setv "Capture Switch" 1
setv "Mic Boost Volume" 1
setv "Headset Mic Boost Volume" 1

if [ "$(getprop init.svc.$HAL)" = running ] && [ $HAD = 0 -o "$REB" = 1 ]; then
  setprop ctl.restart $HAL
  L "restarted $HAL (it may have started before the card was ready)"
fi
L "done: $(cat /proc/asound/cards 2>/dev/null | head -n 1)"
