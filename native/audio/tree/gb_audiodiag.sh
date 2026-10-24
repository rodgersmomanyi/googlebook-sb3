#!/system/bin/sh
# GB native audio diagnostics (Surface Book 3). Run as root:
#   sh /odm/gb_audiodiag.sh          dump state into <boot log partition>\gblog\audio-bN-tT.txt
#   sh /odm/gb_audiodiag.sh test     also stops DRAS, plays a 1 s tone on the speaker/headphone PCMs, records
#                                    4 s from the internal DMIC (PCM 6) and from the headset mic (PCM 0) into
#                                    gblog\audio-bN-*.wav, then starts DRAS again
#   sh /odm/gb_audiodiag.sh auto     same as the default, after a 60 s delay (used by gb.rc at boot_completed)
# Or from any root shell: setprop debug.gb.audiodiag 1   (or: test)
# The partition comes from gb.logpart= (default EFI-SYSTEM), as for gb_bootlog. If mounting it fails, the
# output goes to /data/local/tmp/.
MODE=$1
[ "$MODE" = auto ] && sleep 60
P=EFI-SYSTEM
for a in $(cat /proc/cmdline); do case $a in gb.logpart=*) P=${a#gb.logpart=};; esac; done
D=/dev/block/by-name/$P; M=/mnt/gb_adiag; OUT=
mkdir -p $M
if mount -t vfat -o rw,nosuid,nodev,noexec $D $M 2>/dev/null; then OUT=$M/gblog; else OUT=/data/local/tmp; fi
mkdir -p $OUT
B=$(cat $OUT/bootcount 2>/dev/null || echo 0)
T=$(cut -d. -f1 /proc/uptime)
F=$OUT/audio-b$B-t$T.txt
TM=/vendor/bin/tinymix; TP=/vendor/bin/tinyplay; TC=/vendor/bin/tinycap; TI=/vendor/bin/tinypcminfo
HAL=vendor.audio-hal-aidl
S() { echo; echo "===== $*"; }
R() { S "$*"; timeout 25 "$@" 2>&1; }
cardnum() { for c in /proc/asound/card[0-9]*; do [ "$(cat $c/id 2>/dev/null)" = "$1" ] && { echo ${c##*/card}; return; }; done; }
N=$(cardnum sofhdadsp); [ -z "$N" ] && N=$(cardnum PCH)

{
  echo "GB audio diag: uptime $(cat /proc/uptime) boot_completed=$(getprop sys.boot_completed) hw=$(cat /odm/etc/gb_hw 2>/dev/null) mode=${MODE:-dump}"
  echo "dmi: $(cat /sys/class/dmi/id/sys_vendor 2>/dev/null) / $(cat /sys/class/dmi/id/product_name 2>/dev/null) / $(cat /sys/class/dmi/id/product_sku 2>/dev/null)"
  echo "build: $(getprop ro.build.fingerprint)"
  S cmdline; cat /proc/cmdline
  S "module parameters (dspcfg, sof, hda)"
  for p in /sys/module/snd_intel_dspcfg/parameters/* /sys/module/snd_sof*/parameters/* /sys/module/snd_hda*/parameters/* /sys/module/snd_soc_skl_hda_dsp/parameters/*; do
    [ -r "$p" ] && echo "$p = $(cat $p 2>/dev/null)"
  done
  S "loaded sound modules"; grep -E "^(snd|soundwire|i915|drm_display)" /proc/modules
  S "gbinit module log (snd)"; grep -i snd /odm/gbinit_modules.log 2>/dev/null
  S "PCI audio + display"
  for d in /sys/bus/pci/devices/*; do
    c=$(cat $d/class 2>/dev/null)
    case $c in 0x0403*|0x0401*|0x0300*|0x0302*) echo "${d##*/} $(cat $d/vendor):$(cat $d/device) class=$c driver=$(basename $(readlink $d/driver) 2>/dev/null)";; esac
  done
  S "firmware"; ls -l /odm/firmware/intel/sof /odm/firmware/intel/sof-tplg 2>&1
  R /vendor/bin/mod_options --sof-fw
  mount -t debugfs none /sys/kernel/debug 2>/dev/null
  S "SOF fw_profile (debugfs)"
  for f in /sys/kernel/debug/sof/fw_profile/*; do [ -r "$f" ] && echo "${f##*/}: $(cat $f 2>/dev/null)"; done
  R /vendor/bin/sof_helper profile
  S "/proc/asound"
  for f in version cards devices pcm; do echo "--- $f"; cat /proc/asound/$f 2>&1; done
  for c in /proc/asound/card[0-9]*; do
    echo "--- $c id=$(cat $c/id 2>/dev/null)"
    for x in $c/codec#*; do [ -r "$x" ] && { echo "-- $x"; head -n 40 $x; }; done
    for x in $c/pcm*/info; do [ -r "$x" ] && { echo "-- $x"; cat $x; }; done
    for x in $c/pcm*/sub0/hw_params; do [ -r "$x" ] && echo "-- $x: $(cat $x | tr '\n' ' ')"; done
  done
  if [ -n "$N" ]; then
    R $TM -D $N contents
    for d in 0 31 3 4 5 6 7; do R $TI -D $N -d $d; done
  fi
  S "input devices (jack names for audio_config.xml JackDev)"; cat /proc/bus/input/devices
  S "HAL config in effect"
  cat /vendor/etc/data/hal_configs/audio_default.xml 2>&1
  ls -l /vendor/etc/audio/sof-hda-dsp_sb3 /vendor/etc/audio/PCH_sb3_legacy 2>&1
  S "audio properties + services"; getprop | grep -i -E "audio|dras|sof|sound" | head -n 120
  S "dmesg (sound)"
  dmesg | grep -i -E "sof|snd|hda|hdaudio|dsp|nhlt|dmic|tplg|topology|alc2|realtek|asoc|skl_hda|soundwire|i915.*(audio|component)|GB_AUDIO|firmware" | tail -n 800
  S "logcat (DRAS / audio)"
  logcat -d -b all -v threadtime 2>/dev/null | grep -i -E "dras|AlsaCard|InternalCard|audio_route|audioroute|halconfig|hal_config|AudioFlinger|audioserver|APM_|AudioPolicy|tinyalsa|soundcard|sound_card|WiredAccessory|AudioService.*(device|connect)|IModule" | tail -n 1500
  R dumpsys android.hardware.audio.core.IModule/default options
  R dumpsys android.hardware.audio.core.IModule/default mixer
  R dumpsys android.hardware.audio.core.IModule/default streams
  S "dumpsys media.audio_flinger"; timeout 25 dumpsys media.audio_flinger 2>&1 | head -n 250
  S "dumpsys media.audio_policy"; timeout 25 dumpsys media.audio_policy 2>&1 | head -n 300
} > $F 2>&1

if [ "$MODE" = test ] && [ -n "$N" ]; then
  {
    S "active test (card$N)"
    W=$(getprop init.svc.$HAL)
    [ "$W" = running ] && { setprop ctl.stop $HAL; sleep 2; echo "stopped $HAL"; }
    echo "-- tone on PCM 0 (speaker path)"; timeout 10 $TP /odm/gb_test_tone.wav -D $N -d 0 2>&1
    echo "-- tone on PCM 31 (headphone path, deep buffer)"; timeout 10 $TP /odm/gb_test_tone.wav -D $N -d 31 2>&1
    echo "-- DMIC capture PCM 6 (2 ch, then 4 ch)"
    timeout 10 $TC $OUT/audio-b$B-t$T-dmic.wav -D $N -d 6 -c 2 -r 48000 -b 16 -t 4 2>&1
    # tinycap exits 0 even when the open fails (44-byte header only): retry with the -4ch topology's 4 channels
    [ $(stat -c %s $OUT/audio-b$B-t$T-dmic.wav 2>/dev/null || echo 0) -gt 1000 ] ||
      timeout 10 $TC $OUT/audio-b$B-t$T-dmic.wav -D $N -d 6 -c 4 -r 48000 -b 16 -t 4 2>&1
    echo "-- headset mic capture PCM 0"
    timeout 10 $TC $OUT/audio-b$B-t$T-hsmic.wav -D $N -d 0 -c 2 -r 48000 -b 16 -t 4 2>&1
    ls -l $OUT/audio-b$B-t$T-*.wav 2>&1
    [ "$W" = running ] && { setprop ctl.start $HAL; sleep 2; setprop ctl.restart audioserver; echo "started $HAL, restarted audioserver"; }
  } >> $F 2>&1
fi

sync
echo "gb_audiodiag: wrote $F"
echo "gb_audiodiag: wrote $F" > /dev/kmsg
[ "$OUT" = $M/gblog ] && umount $M
exit 0
