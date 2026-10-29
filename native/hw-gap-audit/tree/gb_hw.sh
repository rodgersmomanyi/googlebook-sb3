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
#   gb_hw.sh buttons         'on boot', own oneshot service: binds the power button / volume rocker (MSHW0040) to
#                            soc_button_array once lapis's pinctrl-icelake drives the INT3455 GPIO controller.
#                            Idempotent, bounded (GB_HW_BTN_WAIT s for the GPIO controller, default 30), never fatal.
#   gb_hw.sh posture         boot_completed with vendor.gb.posture or persist.vendor.gb.posture set: opt-in override
#                            of lapis's device-state posture (open|tablet|docked|closed|reset); off by default.
#   gb_hw.sh diag            after boot_completed: one hardware summary to the kernel log (ends up in gb_bootlog),
#                            including the buttons binding and the posture (device_state, built-in input enabled).
#
# Properties:
#   persist.vendor.gb.rapl.mode   fw (default) = restore the Surface firmware limits | lapis = leave lapis's values |
#                                 custom = use persist.vendor.gb.rapl.pl1_uw / persist.vendor.gb.rapl.pl2_uw
#   vendor.gb.rapl.fw             written by rapl-snapshot: "pl1_uw=.. pl2_uw=.. pl1_en=.. pl2_en=.. locked=.. pu=.."
#   vendor.gb.buttons             written by buttons: "bound driver=.. inputs=N ..." | "FAILED ..." | "absent ..."
#   vendor.gb.posture             one boot (gb\gb.cfg GB_PROP_n=vendor.gb.posture=open with gb.overlay=1, or setprop
#   persist.vendor.gb.posture     at runtime) | every boot: a device-state name to force, reset to cancel; unset/off = no-op
#
# Test hooks: GB_HW_ROOT prefixes /sys, /dev and /proc (host tests with a fake tree); GB_HW_LOG=- logs to stdout;
# GB_HW_MSR (rapl-snapshot); GB_HW_BTN_WAIT, GB_HW_BTN_KO (buttons).
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

# ---------------------------------------------------------------------------------------------------- buttons
# Power button and volume rocker (readiness A-H1): ACPI MSHW0040:00 (\_SB.MSBT) -> soc_button_array -> two gpio-keys
# input devices ("gpio-keys": KEY_POWER, a wake source; KEY_VOLUMEUP/KEY_VOLUMEDOWN). gbinit (rdinit) insmods
# soc_button_array before lapis's vendor_dlkm pinctrl-icelake (loaded at ueventd coldboot) has registered the INT3455
# GPIO controller that every MSBT GpioInt resource points at. The driver ignores -EPROBE_DEFER there on purpose
# (soc_button_array.c, "virtual GPIOs"), so MSHW0040 fails with -ENODEV, which the kernel never retries: bind it again
# once the GPIO controller has its driver. Idempotent (a bound device with inputs is only reported), bounded (the
# controller wait, 3 bind writes, 5 s for the inputs, one unbind/bind if a bound device has no inputs), never fatal
# (always returns 0; it runs in its own oneshot service, started with 'start'). Result: one 'gb_hw: buttons:' line,
# /dev/gb_hw/buttons and vendor.gb.buttons (short form, for gb_bootlog's summary).
BTN=MSHW0040:00
BTN_GPIOC=INT3455:00
BTN_KO=${GB_HW_BTN_KO:-/odm/lib/modules/soc_button_array.ko}

btn_inputs() {      # input devices below MSHW0040's gpio-keys children
	n=0
	for x in "$R/sys/bus/platform/devices/$BTN"/gpio-keys.*/input/input*; do [ -e "$x" ] && n=$((n + 1)); done
	echo $n
}
btn_driver() {      # the driver bound to MSHW0040:00, or none
	l=$(readlink "$R/sys/bus/platform/devices/$BTN/driver" 2>/dev/null); l=${l##*/}
	echo "${l:-none}"
}
btn_gpioc() {       # bound | unbound | absent: the GPIO controller of every MSBT GpioInt resource
	g=$R/sys/bus/platform/devices/$BTN_GPIOC
	if [ -e "$g/driver" ]; then echo bound; elif [ -e "$g" ]; then echo unbound; else echo absent; fi
}
btn_bound_wait() {  # $1 = polls of 0.25 s; 0 as soon as MSHW0040 has a driver
	k=0
	while [ ! -e "$R/sys/bus/platform/devices/$BTN/driver" ]; do
		[ $k -ge $1 ] && return 1
		sleep 0.25; k=$((k + 1))
	done
	return 0
}
btn_inputs_wait() { # $1 = polls of 0.5 s; prints the number of gpio-keys inputs once there are any (or at the end)
	k=0
	while [ "$(btn_inputs)" -eq 0 ] && [ $k -lt $1 ]; do sleep 0.5; k=$((k + 1)); done
	btn_inputs
}
btn_state() {       # $1 = short state: /dev/gb_hw/buttons and vendor.gb.buttons (at most 91 characters)
	mkdir -p "$STATE" 2>/dev/null; echo "$1" > "$STATE/buttons" 2>/dev/null
	setprop vendor.gb.buttons "$1" 2>/dev/null
}
btn_result() {      # $1 = log text, $2 = short state
	log "buttons: $1"
	btn_state "$2"
}
buttons() {         # $1 = how long to wait for the GPIO controller's driver, in 0.5 s polls (0 = no wait)
	d=$R/sys/bus/platform/devices/$BTN
	drv=$R/sys/bus/platform/drivers/soc_button_array
	if [ ! -e "$d" ]; then btn_result "no $BTN device (no Surface button array here), nothing to do" absent; return 0; fi
	n=$(btn_inputs)
	if [ -e "$d/driver" ] && [ "$n" -gt 0 ]; then
		log "buttons: $BTN already bound to $(btn_driver) with $n gpio-keys input(s), nothing to do"
		case "$(cat "$STATE/buttons" 2>/dev/null)" in bound*) ;; *) btn_state "bound inputs=$n already";; esac
		return 0
	fi
	i=0
	while [ "$(btn_gpioc)" = unbound ] && [ $i -lt "$1" ]; do sleep 0.5; i=$((i + 1)); done
	ins=; c=
	if [ ! -e "$drv" ]; then      # gbinit's insmod failed: load it now (its probe binds MSHW0040 if the GPIOs are there)
		if insmod "$BTN_KO" 2>/dev/null; then ins=" insmod=ok"; else ins=" insmod=failed"; fi
		btn_bound_wait 4
	fi
	b=0
	while [ ! -e "$d/driver" ] && [ $b -lt 3 ] && [ -e "$drv/bind" ]; do
		[ $b -gt 0 ] && sleep 1
		b=$((b + 1))
		echo "$BTN" > "$drv/bind" 2>/dev/null
		btn_bound_wait 4
	done
	n=0
	if [ -e "$d/driver" ]; then
		n=$(btn_inputs_wait 10)
		if [ "$n" -eq 0 ] && [ -e "$drv/unbind" ] && [ -e "$drv/bind" ]; then   # bound without inputs: re-probe once
			echo "$BTN" > "$drv/unbind" 2>/dev/null; c=" reprobed=1"
			k=0; while [ -e "$d/driver" ] && [ $k -lt 4 ]; do sleep 0.25; k=$((k + 1)); done
			echo "$BTN" > "$drv/bind" 2>/dev/null
			btn_bound_wait 4 && n=$(btn_inputs_wait 10)
		fi
	fi
	ms=$((i * 500)); g=$(btn_gpioc)
	pm=no; [ -d "$R/sys/module/pinctrl_icelake" ] && pm=yes
	if [ -e "$d/driver" ] && [ "$n" -gt 0 ]; then
		w=; [ "$n" -lt 2 ] && w=" (2 expected: power, volume)"
		btn_result "soc_button_array bound $BTN after $ms ms waiting for $BTN_GPIOC, $b bind write(s)$ins$c: $n gpio-keys input(s)$w" \
			"bound inputs=$n waited_ms=$ms binds=$b$ins$c"
	else
		btn_result "binding $BTN FAILED after $ms ms: driver=$(btn_driver) inputs=$n, GPIO controller $BTN_GPIOC $g, pinctrl_icelake loaded: $pm, soc_button_array loaded: $([ -e "$drv" ] && echo yes || echo no), $b bind write(s)$ins$c" \
			"FAILED gpio=$g pinctrl=$pm inputs=$n waited_ms=$ms binds=$b$ins$c"
	fi
	return 0
}

# ---------------------------------------------------------------------------------------------------- posture
# lapis's device-state policy (com.android.server.desktop.power.DesktopDeviceStateProvider, desktop-services.jar;
# states CLOSED=0, DOCKED=1, OPEN=2, TABLET=3 on build 276) follows the lid switch (ACPI LID0), SW_TABLET_MODE
# (surface_dtx) and external displays. In CLOSED it disables the built-in keyboard and touchpad and forces sleep (the
# VM, which has no lid, sat in CLOSED: readiness A-M1). posture_report logs what Android decided. 'posture' is the
# opt-in recovery, off by default: vendor.gb.posture (one boot: gb\gb.cfg GB_PROP_1=vendor.gb.posture=open with
# gb.overlay=1) or persist.vendor.gb.posture names the state to force, resolved by name from 'cmd device_state
# print-states' like gb_showhome in the VM tree; 'reset' cancels the override. While an override is active, closing
# the lid no longer puts the device in CLOSED (no sleep on lid close: use the power button).
posture_report() {  # $1 = log prefix. Device names only for the built-in devices (never a Bluetooth device's name).
	timeout 20 dumpsys device_state 2>/dev/null |
		grep -E 'mCommittedState|mBaseState|mOverrideState|Override Request active' | head -n 8 |
		sed -e 's/^ *//' -e 's/, app_accessible=[a-z]*//' -e 's/, cancel_when_requester_not_on_top=[a-z]*//' |
		while IFS= read -r l; do log "$1: device_state $l"; done
	timeout 20 dumpsys input 2>/dev/null | awk '
		/^[^ ]/ { show = 0; next }
		/^  BuiltInKeyboardId: / { sub(/^ +/, ""); print; next }
		/^    -?[0-9]+: / || /^  Device -?[0-9]+: / {
			show = ($0 ~ /Microsoft Surface|Lid Switch|gpio-keys|Power Button/)
			if (show) { sub(/^ +/, ""); print }
			next
		}
		show && / (Enabled|Classes|Sources|IsExternal|SwitchValues):/ { sub(/^ +/, ""); print }' | head -n 60 |
		while IFS= read -r l; do log "$1: input $l"; done
}
posture() {
	src=vendor.gb.posture; want=$(prop $src)
	[ -n "$want" ] || { src=persist.vendor.gb.posture; want=$(prop $src); }
	case "$want" in
	''|off|none) log "posture: no override requested (vendor.gb.posture and persist.vendor.gb.posture unset or off)"; return 0 ;;
	*[!A-Za-z_]*) log "posture: $src='$want' is not a device-state name, ignored"; return 0 ;;
	esac
	w=$(echo "$want" | tr a-z A-Z)
	if [ "$w" = RESET ]; then
		id=reset
	else
		L=$(timeout 20 cmd device_state print-states 2>&1)
		id=$(echo "$L" | sed -n "s/.*identifier=\([0-9]*\), name='$w'.*/\1/p" | head -n 1)
		[ -z "$id" ] && [ "$w" = OPEN ] &&
			id=$(echo "$L" | sed -n "s/.*identifier=\([0-9]*\), name='OPENED'.*/\1/p" | head -n 1)
		if [ -z "$id" ]; then
			log "posture: $src=$want: no such device state (have: $(echo "$L" | sed -n "s/.*identifier=\([0-9]*\), name='\([A-Za-z_]*\)'.*/\2=\1/p" | tr '\n' ' '))"
			return 0
		fi
	fi
	r=$(timeout 20 cmd device_state state "$id" 2>&1 | head -n 3 | tr '\n' ' ')
	log "posture: $src=$want -> cmd device_state state $id: ${r:-ok}"
	[ -n "$R" ] || sleep 2      # DeviceStateManager commits the new state asynchronously
	posture_report posture
	return 0
}

# ---------------------------------------------------------------------------------------------------- diag
diag() {
	log "diag: dmi '$(cat "$R"/sys/class/dmi/id/product_name 2>/dev/null)' bios $(cat "$R"/sys/class/dmi/id/bios_version 2>/dev/null)"
	# (readiness A-L4) the S0ix residency counters (ACPI LPIT): growing between two snapshots = S0ix was reached
	ci=$R/sys/devices/system/cpu/cpuidle
	log "diag: mem_sleep '$(cat "$R"/sys/power/mem_sleep 2>/dev/null)' suspend_stats success=$(cat "$R"/sys/power/suspend_stats/success 2>/dev/null) fail=$(cat "$R"/sys/power/suspend_stats/fail 2>/dev/null) lpi_residency_us system=$(cat $ci/low_power_idle_system_residency_us 2>/dev/null || echo -) cpu=$(cat $ci/low_power_idle_cpu_residency_us 2>/dev/null || echo -)"
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
	# (readiness A-H1) power button / volume rocker: one more bind attempt (no waiting) if MSHW0040 still has no
	# driver and gb_hw_buttons has finished, then the result
	if [ -e "$R/sys/bus/platform/devices/$BTN" ] && [ ! -e "$R/sys/bus/platform/devices/$BTN/driver" ] &&
		[ "$(prop init.svc.gb_hw_buttons)" != running ]; then
		log "diag: buttons: $BTN still has no driver, one more bind attempt"
		buttons 0
	fi
	log "diag: buttons $BTN driver=$(btn_driver) gpio-keys inputs=$(btn_inputs) $BTN_GPIOC=$(btn_gpioc) result: $(cat "$STATE/buttons" 2>/dev/null || echo none)"
	# (readiness A-M1) posture: lapis's device state and whether InputReader enabled the built-in keyboard/touchpad
	posture_report "diag: posture"
	dmesg 2>/dev/null | grep -iE 'firmware|dmc|ibt-|iwlwifi.*loaded|sof.*(fw|tplg)|surface_|soc_button|gpio-keys|MSHW0040|INT3455|apds9960|lid|tablet' | tail -n 60 |
		while read -r l; do log "diag: k: $l"; done
}

case "$1" in
rapl-snapshot) rapl_snapshot ;;
boot) rapl_restore; backlight_perms ;;
buttons)
	bw=${GB_HW_BTN_WAIT:-30}; case "$bw" in ''|*[!0-9]*) bw=30;; esac; [ ${#bw} -gt 3 ] && bw=300
	buttons $((bw * 2)) ;;
posture) posture ;;
diag)
	[ -n "$R" ] || sleep 45
	rapl_restore
	diag ;;
*) echo "usage: $0 rapl-snapshot|boot|buttons|posture|diag"; exit 2 ;;
esac
exit 0
