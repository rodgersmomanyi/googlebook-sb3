#!/system/bin/sh
# VMware virtual HD Audio (vmx sound.virtualDev = "hdaudio", PCI 15ad:1977). lapis blocklists snd_hda_intel for
# modalias autoload and our GKI cannot request_module the codec drivers, so load the legacy HDA stack from lapis's
# vendor_dlkm explicitly, then unmute the generic codec (Master starts muted at volume 0, capture off).
# Runs at post-fs-data, before the audio HAL (class hal) starts on boot.
exec >/dev/kmsg 2>&1
found=
for d in /sys/bus/pci/devices/*; do
  [ "$(cat $d/vendor):$(cat $d/device)" = "0x15ad:0x1977" ] && found=$d
done
[ -n "$found" ] || { echo "gb_vmaudio: no VMware HD Audio device (vmx: sound.present/sound.virtualDev=hdaudio)"; exit 0; }
M=/vendor/lib/modules
for m in snd-hda-core snd-hda-codec snd-hda-codec-generic snd-hda-scodec-component snd-hda-codec-realtek-lib \
         snd-hda-codec-alc269 snd-hda-codec-alc882 snd-hda-codec-alc662 snd-hda-codec-hdmi snd-hda-intel; do
  n=$(echo $m | /system/bin/toybox tr - _)
  [ -d /sys/module/$n ] || /system/bin/insmod $M/$m.ko || echo "gb_vmaudio: insmod $m failed"
done
n=0
while [ ! -e /dev/snd/controlC0 ] && [ $n -lt 50 ]; do /system/bin/sleep 0.1; n=$((n+1)); done
TM=/vendor/bin/tinymix
for c in "Master Playback Switch" "Front Playback Switch" "Capture Switch"; do $TM -D 0 set "$c" 1 >/dev/null; done
for c in "Master Playback Volume" "Front Playback Volume"; do $TM -D 0 set "$c" 64 >/dev/null; done
$TM -D 0 set "Capture Volume" 36 >/dev/null
echo "gb_vmaudio: $(head -1 /proc/asound/cards) master=$($TM -D 0 get 'Master Playback Volume' 2>/dev/null | head -1)"
exit 0
