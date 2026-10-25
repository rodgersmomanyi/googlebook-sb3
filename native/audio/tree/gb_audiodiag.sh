#!/system/bin/sh
# GB native audio diagnostics (Surface Book 3). Run as root:
#   sh /odm/gb_audiodiag.sh          dump state into <boot log partition>\gblog\audio-bN-tT.txt
#   sh /odm/gb_audiodiag.sh test     also stops DRAS, plays a 1 s tone on the speaker/headphone PCMs, records
#                                    4 s from the internal DMIC (PCM 6) and from the headset mic (PCM 0) into
#                                    gblog\audio-bN-tT-*.wav, then starts DRAS again
#   sh /odm/gb_audiodiag.sh auto     same as the default, after a 60 s delay (used by gb.rc at boot_completed)
# Or from any root shell: setprop debug.gb.audiodiag 1   (or: test)
# Everything is collected in /data/vendor/gb/audiodiag/t<T>/ first (T = uptime in s). That directory must be root-only
# (a real directory owned by this script's uid, mode 0700, like gb_nouveau.sh's payload/); anything else there (a
# symlink, a file, another owner or mode) is removed and re-created first, and symlinks are never copied, so no other
# uid (adb shell can write /data/local/tmp) can make root write through a planted link. Then the ESP lock is taken
# (INTERFACES 5.4, owner gb_audiodiag; after 600 s it gives up), the partition named by gb.logpart= (default
# EFI-SYSTEM) is mounted rw,flush only for the copy, and each file is copied as gblog\audio-b<N>-<name> (N =
# gblog\bootcount, the boot number gb_bootlog keeps). Nothing is copied while the ESP has less than 128 MiB free. The
# local copy is removed only after a successful copy; otherwise it stays (at most 3 older ones are kept).
# GB_ADIAG_LOCK, GB_ADIAG_MNT, GB_ADIAG_TMP and GB_ADIAG_KMSG exist for the test harness only (init never sets them).
MODE=$1
[ "$MODE" = auto ] && sleep 60
P=EFI-SYSTEM
# shellcheck disable=SC2013  # the kernel command line is split into words on purpose
for a in $(cat /proc/cmdline); do case $a in gb.logpart=*) P=${a#gb.logpart=};; esac; done
D=/dev/block/by-name/$P; M=${GB_ADIAG_MNT:-/mnt/gb_adiag}; L=${GB_ADIAG_LOCK:-/dev/gb_esp.lock}
TB=${GB_ADIAG_TMP:-/data/vendor/gb/audiodiag}; K=${GB_ADIAG_KMSG:-/dev/kmsg}; OWN=; FLOOR_KB=131072
T=$(cut -d. -f1 /proc/uptime)
O=$TB/t$T
F=$O/t$T.txt
TM=/vendor/bin/tinymix; TP=/vendor/bin/tinyplay; TC=/vendor/bin/tinycap; TI=/vendor/bin/tinypcminfo
HAL=vendor.audio-hal-aidl
S() { echo; echo "===== $*"; }
R() { S "$*"; timeout 25 "$@" 2>&1; }
cardnum() { for c in /proc/asound/card[0-9]*; do [ "$(cat "$c/id" 2>/dev/null)" = "$1" ] && { echo "${c##*/card}"; return; }; done; }

# ---- ESP lock (INTERFACES 5.4; the same protocol as gb_bootlog and gb_nouveau.sh)
steal() {   # $1 = owner line seen, $2 = inode seen; 0 = the stale lock was removed: retry mkdir
  s=$L.stale.$$; rm -rf "$s" 2>/dev/null
  mv -T "$L" "$s" 2>/dev/null || return 1
  o2=; [ -r "$s/owner" ] && IFS= read -r o2 < "$s/owner"
  if [ "$(stat -c %i "$s" 2>/dev/null)" = "$2" ] && [ "$o2" = "$1" ]; then
    echo "gb_audiodiag: stealing a stale ESP lock (owner '$1')" >> "$K"
    rm -f "$s/owner"; rmdir "$s" 2>/dev/null; return 0
  fi
  mv -T "$s" "$L" 2>/dev/null || echo "gb_audiodiag: ESP lock changed while stealing it, left as ${s##*/}" >> "$K"
  return 1
}
lock() {    # 1 s polls; stale = dead or non-numeric owner, or 5 polls without an owner file; 600 s: give up (1)
  w=0; miss=0; OWN=
  while ! mkdir "$L" 2>/dev/null; do
    i=$(stat -c %i "$L" 2>/dev/null); ol=; [ -r "$L/owner" ] && IFS= read -r ol < "$L/owner"; o=${ol%% *}; st=0
    if [ -n "$ol" ]; then miss=0; case $o in ''|*[!0-9]*) st=1;; *) [ -d "/proc/$o" ] || st=1;; esac
    else miss=$((miss+1)); [ $miss -ge 5 ] && st=1; fi
    if [ $st = 1 ] && [ -n "$i" ]; then miss=0; steal "$ol" "$i" && continue; fi
    [ $w -ge 600 ] && return 1
    sleep 1; w=$((w+1))
  done
  OWN="$$ gb_audiodiag $(cut -d. -f1 /proc/uptime)"; echo "$OWN" > "$L/owner"
}
unlock() {  # only our own lock (a lock stolen from us belongs to someone else now)
  [ -n "$OWN" ] || return 0
  ol=; [ -r "$L/owner" ] && IFS= read -r ol < "$L/owner"
  [ "$ol" = "$OWN" ] && { rm -f "$L/owner"; rmdir "$L" 2>/dev/null; }
  OWN=
}
copy_out() {   # 0 = every file of $O copied to the ESP's gblog/ (B = the boot number used)
  B=0
  if ! lock; then
    echo "gb_audiodiag: ESP lock held over 600 s (owner '$ol'), files kept in $O" >> "$K"; return 1
  fi
  if ! mount -t vfat -o rw,flush,nosuid,nodev,noexec "$D" "$M" 2>/dev/null; then
    unlock; echo "gb_audiodiag: cannot mount $D, files kept in $O" >> "$K"; return 1
  fi
  r=1
  B=$(cat "$M/gblog/bootcount" 2>/dev/null); case $B in ''|*[!0-9]*) B=0;; esac
  # shellcheck disable=SC2046  # df's fields are split into words on purpose
  set -- $(df -k "$M" 2>/dev/null | tail -n 1); fr=$4
  case $fr in ''|*[!0-9]*) fr=;; esac
  if [ -n "$fr" ] && [ "$fr" -lt $FLOOR_KB ]; then
    echo "gb_audiodiag: ESP free ${fr} KiB below the 128 MiB floor, files kept in $O" >> "$K"
  else
    mkdir -p "$M/gblog"; r=0
    for f in "$O"/*; do
      [ -L "$f" ] && continue          # never follow a link (the directory is root-only; belt and braces)
      [ -f "$f" ] || continue
      cp "$f" "$M/gblog/audio-b$B-${f##*/}" || r=1
    done
  fi
  sync; umount "$M"; unlock
  return $r
}

# ---- local collection directory: root-only, else re-created (no parent is created: /data/vendor/gb is init's, 0751)
dir_ok() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -c '%u %a' "$1" 2>/dev/null)" = "$(id -u) 700" ]; }
if ! dir_ok "$TB"; then
  if [ -e "$TB" ] || [ -L "$TB" ]; then
    echo "gb_audiodiag: $TB is not a root-only directory: recreated" >> "$K"
    rm -rf "$TB"                       # a symlink is removed itself, never followed
  fi
  mkdir -m 0700 "$TB" 2>/dev/null
  if ! dir_ok "$TB"; then
    echo "gb_audiodiag: cannot create a root-only $TB, nothing collected" >> "$K"
    echo "gb_audiodiag: cannot create a root-only $TB, nothing collected"; exit 0
  fi
fi
# keep at most 3 older collection directories (left behind by failed copies)
n=0
# shellcheck disable=SC2045  # fixed names t<digits> below $TB, no spaces; ls -t gives the newest first
for d in $(ls -dt "$TB"/t[0-9]* 2>/dev/null); do
  [ "$d" = "$O" ] && continue
  n=$((n+1)); [ $n -gt 3 ] && rm -rf "$d"
done
mkdir -p "$O" "$M"
N=$(cardnum sofhdadsp); [ -z "$N" ] && N=$(cardnum PCH)

{
  echo "GB audio diag: uptime $(cat /proc/uptime) boot_completed=$(getprop sys.boot_completed) hw=$(cat /odm/etc/gb_hw 2>/dev/null) mode=${MODE:-dump}"
  echo "dmi: $(cat /sys/class/dmi/id/sys_vendor 2>/dev/null) / $(cat /sys/class/dmi/id/product_name 2>/dev/null) / $(cat /sys/class/dmi/id/product_sku 2>/dev/null)"
  echo "build: $(getprop ro.build.fingerprint)"
  S cmdline; cat /proc/cmdline
  S "module parameters (dspcfg, sof, hda)"
  for p in /sys/module/snd_intel_dspcfg/parameters/* /sys/module/snd_sof*/parameters/* /sys/module/snd_hda*/parameters/* /sys/module/snd_soc_skl_hda_dsp/parameters/*; do
    [ -r "$p" ] && echo "$p = $(cat "$p" 2>/dev/null)"
  done
  S "loaded sound modules"; grep -E "^(snd|soundwire|i915|drm_display)" /proc/modules
  S "gbinit module log (snd)"; grep -i snd /odm/gbinit_modules.log 2>/dev/null
  S "PCI audio + display"
  for d in /sys/bus/pci/devices/*; do
    c=$(cat "$d/class" 2>/dev/null)
    case $c in 0x0403*|0x0401*|0x0300*|0x0302*) echo "${d##*/} $(cat "$d/vendor"):$(cat "$d/device") class=$c driver=$(basename "$(readlink "$d/driver")" 2>/dev/null)";; esac
  done
  S "firmware"; ls -l /odm/firmware/intel/sof /odm/firmware/intel/sof-tplg 2>&1
  R /vendor/bin/mod_options --sof-fw
  mount -t debugfs none /sys/kernel/debug 2>/dev/null
  S "SOF fw_profile (debugfs)"
  for f in /sys/kernel/debug/sof/fw_profile/*; do [ -r "$f" ] && echo "${f##*/}: $(cat "$f" 2>/dev/null)"; done
  R /vendor/bin/sof_helper profile
  S "/proc/asound"
  for f in version cards devices pcm; do echo "--- $f"; cat /proc/asound/$f 2>&1; done
  for c in /proc/asound/card[0-9]*; do
    echo "--- $c id=$(cat "$c/id" 2>/dev/null)"
    for x in "$c"/codec#*; do [ -r "$x" ] && { echo "-- $x"; head -n 40 "$x"; }; done
    for x in "$c"/pcm*/info; do [ -r "$x" ] && { echo "-- $x"; cat "$x"; }; done
    for x in "$c"/pcm*/sub0/hw_params; do [ -r "$x" ] && echo "-- $x: $(tr '\n' ' ' < "$x")"; done
  done
  if [ -n "$N" ]; then
    R $TM -D "$N" contents
    for d in 0 31 3 4 5 6 7; do R $TI -D "$N" -d $d; done
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
} > "$F" 2>&1

if [ "$MODE" = test ] && [ -n "$N" ]; then
  {
    S "active test (card$N)"
    W=$(getprop init.svc.$HAL)
    [ "$W" = running ] && { setprop ctl.stop $HAL; sleep 2; echo "stopped $HAL"; }
    echo "-- tone on PCM 0 (speaker path)"; timeout 10 $TP /odm/gb_test_tone.wav -D "$N" -d 0 2>&1
    echo "-- tone on PCM 31 (headphone path, deep buffer)"; timeout 10 $TP /odm/gb_test_tone.wav -D "$N" -d 31 2>&1
    echo "-- DMIC capture PCM 6 (2 ch, then 4 ch)"
    timeout 10 $TC "$O/t$T-dmic.wav" -D "$N" -d 6 -c 2 -r 48000 -b 16 -t 4 2>&1
    # tinycap exits 0 even when the open fails (44-byte header only): retry with the -4ch topology's 4 channels
    [ "$(stat -c %s "$O/t$T-dmic.wav" 2>/dev/null || echo 0)" -gt 1000 ] ||
      timeout 10 $TC "$O/t$T-dmic.wav" -D "$N" -d 6 -c 4 -r 48000 -b 16 -t 4 2>&1
    echo "-- headset mic capture PCM 0"
    timeout 10 $TC "$O/t$T-hsmic.wav" -D "$N" -d 0 -c 2 -r 48000 -b 16 -t 4 2>&1
    ls -l "$O"/t"$T"-*.wav 2>&1
    [ "$W" = running ] && { setprop ctl.start $HAL; sleep 2; setprop ctl.restart audioserver; echo "started $HAL, restarted audioserver"; }
  } >> "$F" 2>&1
fi

if copy_out; then
  rm -rf "$O"
  echo "gb_audiodiag: wrote gblog/audio-b$B-t$T.*"
  echo "gb_audiodiag: wrote gblog/audio-b$B-t$T.*" >> "$K"
else
  echo "gb_audiodiag: kept $O (not copied to the ESP)"
fi
exit 0
