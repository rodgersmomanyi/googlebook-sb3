#!/system/bin/sh
# gb_hw.sh - Surface Book 3 native-mode hardware glue for lapis (installed as /odm/gb_hw.sh, native tree only).
# Source: C:\GB-build\native\hw-gap-audit\tree\gb_hw.sh. Started by gb_hw.rc (appended to GB_RC_NATIVE).
#
#   gb_hw.sh rapl-snapshot   early-init, before lapis init.lapis.rc 'on init' overwrites the CPU package power
#                            limits (it writes PL1=25 W / PL2=55 W unconditionally, tuned for the lapis board).
#                            Reads the firmware values straight from MSR 0x610 (MSR_PKG_POWER_LIMIT) and 0x606
#                            (MSR_RAPL_POWER_UNIT) through lapis's own msr.ko, saves them in /dev/gb_hw/rapl_fw and
#                            vendor.gb.rapl.fw.
#   gb_hw.sh boot            'on boot' (after lapis 'on init'): restores the firmware PL1/PL2 (or a user override)
#                            through powercap, and gives the composer (user system) write access to every backlight.
#   gb_hw.sh diag            after boot_completed: one hardware summary to the kernel log (ends up in gb_bootlog).
#
# Properties:
#   persist.vendor.gb.rapl.mode   fw (default) = restore the Surface firmware limits | lapis = leave lapis's values |
#                                 custom = use persist.vendor.gb.rapl.pl1_uw / persist.vendor.gb.rapl.pl2_uw
#   vendor.gb.rapl.fw             written by rapl-snapshot: "pl1_uw=.. pl2_uw=.. pl1_en=.. pl2_en=.. locked=.. pu=.."
#
# Test hook: GB_HW_ROOT prefixes /sys, /dev and /proc (host tests with a fake tree); GB_HW_LOG=- logs to stdout.
# mksh arithmetic is 32-bit signed: all MSR math is done on single bytes and stays far below 2^31.

R=${GB_HW_ROOT:-}
STATE=$R/dev/gb_hw
LOGDEV=$R/dev/kmsg
[ "${GB_HW_LOG:-}" = "-" ] && LOGDEV=/dev/stdout

log() { echo "gb_hw: $*" > "$LOGDEV" 2>/dev/null || echo "gb_hw: $*"; }
prop() { getprop "$1" 2>/dev/null; }

# ---------------------------------------------------------------------------------------------------- RAPL
# Print the 8 bytes of MSR <idx> (decimal) read through <dev> as decimal numbers b0..b7 (little endian), or nothing.
# The msr driver reads the MSR whose number is the file offset, and only in multiples of 8 bytes, so the reader must
# lseek to exactly <idx> and then read 8: plain dd can only do that for 8-aligned indexes (0x610); 0x606 needs
# dd iflag=skip_bytes (toybox, GNU) or xxd -s (toybox).
msr_bytes() {
	dev=$1; idx=$2; out=
	[ $((idx % 8)) -eq 0 ] &&
		out=$(dd if="$dev" bs=8 count=1 skip=$((idx / 8)) 2>/dev/null | od -An -tu1 -v 2>/dev/null)
	[ -n "$out" ] ||
		out=$(dd if="$dev" bs=8 count=1 skip=$idx iflag=skip_bytes 2>/dev/null | od -An -tu1 -v 2>/dev/null)
	if [ -z "$out" ]; then
		h=$(xxd -s $idx -l 8 -p "$dev" 2>/dev/null | tr -d ' \n')
		if [ ${#h} -eq 16 ]; then
			i=0
			while [ $i -lt 16 ]; do out="$out $((16#$(echo "$h" | cut -c$((i + 1))-$((i + 2)))))"; i=$((i + 2)); done
		fi
	fi
	echo $out
}

rapl_snapshot() {
	mkdir -p "$STATE"
	if [ -z "$R" ]; then
		# lapis vendor_dlkm ships msr.ko (vendor modules load on our kernel); first-stage init mounted vendor_dlkm.
		[ -d /sys/module/msr ] || /vendor/bin/modprobe --dirname=/vendor/lib/modules msr 2>/dev/null ||
			insmod /vendor/lib/modules/msr.ko 2>/dev/null
	fi
	dev=$R/dev/cpu/0/msr
	if [ ! -c "$dev" ] && [ -z "$R" ]; then
		# ueventd may not have created /dev/cpu/0/msr yet (coldboot runs in parallel with early-init).
		dev=$STATE/msr0
		[ -c "$dev" ] || mknod "$dev" c 202 0 2>/dev/null
	fi
	[ -n "${GB_HW_MSR:-}" ] && dev=$GB_HW_MSR     # test hook: regular file laid out like /dev/cpu/0/msr
	if [ ! -r "$dev" ]; then log "rapl-snapshot: no MSR device ($dev)"; return 1; fi

	set -- $(msr_bytes "$dev" 1542)               # 0x606 MSR_RAPL_POWER_UNIT
	if [ $# -eq 8 ]; then pu=$(($1 & 15))
	else pu=3; log "rapl-snapshot: cannot read MSR 0x606, assuming the Intel default power unit 1/8 W"; fi
	set -- $(msr_bytes "$dev" 1552)               # 0x610 MSR_PKG_POWER_LIMIT
	[ $# -eq 8 ] || { log "rapl-snapshot: cannot read MSR 0x610"; return 1; }
	pl1_raw=$(($1 + ($2 & 127) * 256)); pl1_en=$((($2 >> 7) & 1))
	pl2_raw=$(($5 + ($6 & 127) * 256)); pl2_en=$((($6 >> 7) & 1))
	locked=$((($8 >> 7) & 1))
	if [ $pu -le 6 ]; then unit=$((1000000 >> pu)); else unit=$((1000000 / (1 << pu))); fi
	# 32-bit guard (raw is at most 0x7fff): never overflow.
	if [ $pl1_raw -gt $((2147483647 / unit)) ] || [ $pl2_raw -gt $((2147483647 / unit)) ]; then
		log "rapl-snapshot: implausible raw values pl1=$pl1_raw pl2=$pl2_raw pu=$pu"; return 1
	fi
	pl1=$((pl1_raw * unit)); pl2=$((pl2_raw * unit))
	v="pl1_uw=$pl1 pl2_uw=$pl2 pl1_en=$pl1_en pl2_en=$pl2_en locked=$locked pu=$pu"
	echo "$v" > "$STATE/rapl_fw"
	setprop vendor.gb.rapl.fw "$v" 2>/dev/null
	log "rapl-snapshot: firmware MSR_PKG_POWER_LIMIT $v"
}

# Value of key $1 in /dev/gb_hw/rapl_fw ("k=v k=v ...").
fwval() {
	for kv in $(cat "$STATE/rapl_fw" 2>/dev/null); do
		case "$kv" in "$1="*) echo "${kv#*=}"; return 0;; esac
	done
}

rapl_restore() {
	mode=$(prop persist.vendor.gb.rapl.mode); [ -n "$mode" ] || mode=fw
	[ "$mode" = lapis ] && { log "rapl: mode=lapis, leaving the lapis limits"; return 0; }
	z=
	for d in "$R"/sys/class/powercap/intel-rapl:*; do
		[ "$(cat "$d/name" 2>/dev/null)" = package-0 ] && { z=$d; break; }
	done
	[ -n "$z" ] || { log "rapl: no intel-rapl package-0 zone (intel_rapl_msr not loaded?)"; return 1; }
	if [ "$mode" = custom ]; then
		pl1=$(prop persist.vendor.gb.rapl.pl1_uw); pl2=$(prop persist.vendor.gb.rapl.pl2_uw)
	else
		[ -f "$STATE/rapl_fw" ] || { log "rapl: no firmware snapshot, leaving the limits alone"; return 1; }
		[ "$(fwval locked)" = 1 ] && { log "rapl: firmware locked MSR_PKG_POWER_LIMIT, nothing to restore"; return 0; }
		pl1=$(fwval pl1_uw); pl2=$(fwval pl2_uw)
	fi
	# Sanity window for a 15 W Ice Lake-U part: PL1 5..65 W, PL2 >= PL1 and <= 120 W.
	case "$pl1$pl2" in ''|*[!0-9]*) log "rapl: bad values pl1='$pl1' pl2='$pl2' (mode $mode)"; return 1;; esac
	if [ "$pl1" -lt 5000000 ] || [ "$pl1" -gt 65000000 ] || [ "$pl2" -lt "$pl1" ] || [ "$pl2" -gt 120000000 ]; then
		log "rapl: values out of range pl1=$pl1 pl2=$pl2 (mode $mode), not applied"; return 1
	fi
	before="$(cat "$z/constraint_0_power_limit_uw" 2>/dev/null)/$(cat "$z/constraint_1_power_limit_uw" 2>/dev/null)"
	[ "$(cat "$z/constraint_0_name" 2>/dev/null)" = long_term ] && echo "$pl1" > "$z/constraint_0_power_limit_uw"
	[ "$(cat "$z/constraint_1_name" 2>/dev/null)" = short_term ] && echo "$pl2" > "$z/constraint_1_power_limit_uw"
	after="$(cat "$z/constraint_0_power_limit_uw" 2>/dev/null)/$(cat "$z/constraint_1_power_limit_uw" 2>/dev/null)"
	log "rapl: mode=$mode PL1/PL2 uW $before -> $after"
}

# ---------------------------------------------------------------------------------------------------- backlight
backlight_perms() {
	for b in "$R"/sys/class/backlight/*; do
		[ -f "$b/brightness" ] || continue
		chown system:system "$b/brightness" 2>/dev/null
		chmod 0664 "$b/brightness" 2>/dev/null
		log "backlight: ${b##*/} max=$(cat "$b/max_brightness" 2>/dev/null) now=$(cat "$b/brightness" 2>/dev/null) type=$(cat "$b/type" 2>/dev/null)"
	done
}

# ---------------------------------------------------------------------------------------------------- diag
diag() {
	log "diag: dmi '$(cat "$R"/sys/class/dmi/id/product_name 2>/dev/null)' bios $(cat "$R"/sys/class/dmi/id/bios_version 2>/dev/null)"
	log "diag: mem_sleep '$(cat "$R"/sys/power/mem_sleep 2>/dev/null)' suspend_stats success=$(cat "$R"/sys/power/suspend_stats/success 2>/dev/null) fail=$(cat "$R"/sys/power/suspend_stats/fail 2>/dev/null)"
	c=$R/sys/devices/system/cpu/cpu0/cpufreq
	log "diag: cpufreq driver=$(cat $c/scaling_driver 2>/dev/null) gov=$(cat $c/scaling_governor 2>/dev/null) epp=$(cat $c/energy_performance_preference 2>/dev/null) min/max=$(cat $c/scaling_min_freq 2>/dev/null)/$(cat $c/scaling_max_freq 2>/dev/null) pstate=$(cat "$R"/sys/devices/system/cpu/intel_pstate/status 2>/dev/null)"
	for z in "$R"/sys/class/thermal/thermal_zone*; do
		[ -d "$z" ] && log "diag: thermal ${z##*/} type=$(cat $z/type 2>/dev/null) temp=$(cat $z/temp 2>/dev/null)"
	done
	for z in "$R"/sys/class/powercap/intel-rapl*; do
		[ -d "$z" ] && log "diag: powercap ${z##*/} name=$(cat $z/name 2>/dev/null) pl1=$(cat $z/constraint_0_power_limit_uw 2>/dev/null) pl2=$(cat $z/constraint_1_power_limit_uw 2>/dev/null) enabled=$(cat $z/enabled 2>/dev/null)"
	done
	log "diag: rapl firmware snapshot: $(cat "$STATE/rapl_fw" 2>/dev/null || echo none)"
	log "diag: platform_profile=$(cat "$R"/sys/firmware/acpi/platform_profile 2>/dev/null) choices='$(cat "$R"/sys/firmware/acpi/platform_profile_choices 2>/dev/null)'"
	backlight_perms
	for p in "$R"/sys/class/typec/*; do [ -e "$p" ] && log "diag: typec ${p##*/}"; done
	[ -e "$R"/sys/bus/acpi/devices/USBC000:00/status ] && log "diag: ACPI USBC000 (UCSI) status=$(cat "$R"/sys/bus/acpi/devices/USBC000:00/status)"
	for r in "$R"/sys/class/rtc/rtc*; do
		[ -d "$r" ] && log "diag: ${r##*/} name=$(cat $r/name 2>/dev/null) date=$(cat $r/date 2>/dev/null) time=$(cat $r/time 2>/dev/null) hctosys=$(cat $r/hctosys 2>/dev/null) system_utc=$(date -u +%Y-%m-%dT%H:%M:%S 2>/dev/null)"
	done
	for k in "$R"/sys/class/rfkill/rfkill*; do
		[ -d "$k" ] && log "diag: ${k##*/} type=$(cat $k/type 2>/dev/null) name=$(cat $k/name 2>/dev/null) soft=$(cat $k/soft 2>/dev/null) hard=$(cat $k/hard 2>/dev/null)"
	done
	for i in "$R"/sys/class/input/input*; do
		[ -f "$i/name" ] || continue
		log "diag: ${i##*/} '$(cat $i/name)' bus=$(cat $i/id/bustype 2>/dev/null) $(cat $i/id/vendor 2>/dev/null):$(cat $i/id/product 2>/dev/null) sw=$(cat $i/capabilities/sw 2>/dev/null) key=$(cat $i/capabilities/key 2>/dev/null | tr ' ' '.')"
	done
	w=$(grep -E '\*enabled' "$R"/proc/acpi/wakeup 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
	log "diag: ACPI wake enabled: $w"
	for d in "$R"/sys/bus/pci/devices/*; do
		[ "$(cat $d/vendor 2>/dev/null)" = 0x10de ] && log "diag: dGPU ${d##*/} power_state=$(cat $d/power_state 2>/dev/null) runtime=$(cat $d/power/runtime_status 2>/dev/null)"
	done
	dmesg 2>/dev/null | grep -iE 'firmware|dmc|ibt-|iwlwifi.*loaded|sof.*(fw|tplg)|surface_|soc_button|apds9960|lid|tablet' | tail -n 60 |
		while read -r l; do log "diag: k: $l"; done
}

case "$1" in
rapl-snapshot) rapl_snapshot ;;
boot) rapl_restore; backlight_perms ;;
diag)
	[ -n "$R" ] || sleep 45
	rapl_restore
	diag ;;
*) echo "usage: $0 rapl-snapshot|boot|diag"; exit 2 ;;
esac
exit 0
