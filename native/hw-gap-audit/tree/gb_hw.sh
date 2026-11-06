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
#   gb_hw.sh sam             'on boot', own oneshot service (first native boot, wave 9 hw-native1): binds the Surface
#                            Aggregator serial device (ACPI MSHW0084, e.g. serial0-0) to surface_serial_hub again once
#                            the INT3455 GPIO controller has its driver (k16's first-stage probe fails for good with
#                            -EINVAL), then waits for the keyboard, touchpad, batteries, charger and DTX. Idempotent,
#                            bounded (GB_HW_SAM_WAIT s for the GPIO controller, default 30), never fatal.
#   gb_hw.sh ish             'early-boot', own oneshot service: if lapis's intel_ish_ipc probe of the Integrated Sensor
#                            Hub (8086:34fc) failed at coldboot, one PCI function reset and one re-bind, in parallel with
#                            gb_sensors_wait, so the accelerometer can still be there when the sensors HAL scans.
#   gb_hw.sh vcm             'early-init', own oneshot service (second native boot, wave 10 misc-cameras): loads the
#                            rear camera's focus-motor driver dw9719.ko once ipu-bridge has created the motor's i2c
#                            device (i2c-INT347A:00-VCM) AND its runtime-PM link to the rear sensor, so the sensor that
#                            powers the motor is on for its probe and its first runtime suspend. Idempotent, bounded
#                            (GB_HW_VCM_WAIT s for the device and its link, default 30), never fatal.
#   gb_hw.sh diag            after boot_completed: one hardware summary to the kernel log (ends up in gb_bootlog),
#                            including the buttons binding, the posture (device_state, built-in input enabled), the
#                            Surface Aggregator and its clients, the sensor hub and the rear focus motor.
#
# Properties:
#   persist.vendor.gb.rapl.mode   fw (default) = restore the Surface firmware limits | lapis = leave lapis's values |
#                                 custom = use persist.vendor.gb.rapl.pl1_uw / persist.vendor.gb.rapl.pl2_uw
#   vendor.gb.rapl.fw             written by rapl-snapshot: "pl1_uw=.. pl2_uw=.. pl1_en=.. pl2_en=.. locked=.. pu=.."
#   vendor.gb.buttons             written by buttons: "bound driver=.. inputs=N ..." | "FAILED ..." | "absent ..."
#   vendor.gb.posture             one boot (gb\gb.cfg GB_PROP_n=vendor.gb.posture=open with gb.overlay=1, or setprop
#   persist.vendor.gb.posture     at runtime) | every boot: a device-state name to force, reset to cancel; unset/off = no-op
#   vendor.gb.sam                 written by sam: "bound|kernel|already binds=.. waited_ms=.. kbd= tp= bat= adp= dtx=
#                                 base=" | "FAILED ..." | "absent" (at most 91 characters)
#   vendor.gb.ish                 written by ish: "already accel=N" | "rebound reset=.. accel=N ms=.." | "FAILED ..." |
#                                 "absent"
#   vendor.gb.vcm                 written by vcm: "loaded|bound|already|rebound rpm=.. sensor=.. waited_ms=.." |
#                                 "FAILED ..." | "absent"
#
# Test hooks: GB_HW_ROOT prefixes /sys, /dev and /proc (host tests with a fake tree); GB_HW_LOG=- logs to stdout;
# GB_HW_MSR (rapl-snapshot); GB_HW_BTN_WAIT, GB_HW_BTN_KO (buttons); GB_HW_SAM_WAIT, GB_HW_SAM_CLIENT_WAIT (sam);
# GB_HW_ISH_WAIT (ish); GB_HW_VCM_WAIT, GB_HW_VCM_KO (vcm).
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

# ---------------------------------------------------------------------------------------------------- sam
# Surface Aggregator (SAM): base keyboard and touchpad, both batteries, the charger, the performance profile and the
# clipboard detach (first native boot 2026-10-31, wave 9 hw-native1). The SAM's serial device (ACPI MSHW0084 on the
# dw-apb-uart of lapis's first-stage intel-lpss-pci, e.g. serial0-0) probes in first stage, before lapis's vendor_dlkm
# pinctrl-icelake (ueventd coldboot) has registered the INT3455 GPIO controller of the SAM wakeup GpioInt. k16's
# ssam_irq_setup() then falls back from gpiod_get()'s -EPROBE_DEFER to fwnode_irq_get(), which finds no plain interrupt
# on ACPI: "error -EINVAL: failed to setup IRQ", probe -22, never retried (kernel fix for a later build: w9prep
# hw-native1 kernel patch 0114). The probe's error path undoes everything (k16 core.c ssam_serial_hub_probe
# err_irq..err_ctrl_init; devres released by the driver core), so a bind write once INT3455:00 has its driver is a clean
# second probe; the registry's platform hub, deferred while there was no controller, then registers the clients.
# Idempotent (a bound device is only reported; after a wait for the GPIO controller, 2 s of grace for a kernel that
# defers by itself), bounded (the GPIO-controller wait, 3 bind writes per device, the client wait), never fatal (always
# returns 0; own oneshot service, started with 'start'). Result: one 'gb_hw: sam:' line, /dev/gb_hw/sam, vendor.gb.sam.
SAM_HID=MSHW0084
SAM_DRV=surface_serial_hub
SAM_KBD='Microsoft Surface 045E:09AE Keyboard'
SAM_TP='Microsoft Surface 045E:09AF Touchpad'

sam_devs() {        # the SAM's serial devices (names below /sys/bus/serial/devices), one per line
	for x in "$R"/sys/bus/serial/devices/*; do
		[ -e "$x" ] || continue
		case "${x##*/}" in *[!A-Za-z0-9._:-]*) continue;; esac
		case "$(cat "$x/modalias" 2>/dev/null)" in "acpi:$SAM_HID:"*) echo "${x##*/}"; continue;; esac
		[ "$(cat "$x/firmware_node/hid" 2>/dev/null)" = "$SAM_HID" ] && echo "${x##*/}"
	done
}
sam_drv_of() {      # the driver bound to serial device $1, or none
	l=$(readlink "$R/sys/bus/serial/devices/$1/driver" 2>/dev/null); l=${l##*/}
	echo "${l:-none}"
}
sam_bound_wait() {  # $1 = serial device, $2 = polls of 0.25 s; 0 as soon as it has a driver
	k=0
	while [ ! -e "$R/sys/bus/serial/devices/$1/driver" ]; do
		[ $k -ge "$2" ] && return 1
		sleep 0.25; k=$((k + 1))
	done
	return 0
}
sam_has_input() {   # 1 if an input device is named exactly $1, else 0
	for x in "$R"/sys/class/input/input*/name; do
		[ "$(cat "$x" 2>/dev/null)" = "$1" ] && { echo 1; return 0; }
	done
	echo 0
}
sam_clients() {     # sets kbd tp b1 b2 adp dtx (1/0), base (gb_power's vendor.gb.dtx.base or -), nc (SAM bus devices)
	kbd=$(sam_has_input "$SAM_KBD"); tp=$(sam_has_input "$SAM_TP")
	b1=0; [ -e "$R/sys/class/power_supply/BAT1" ] && b1=1
	b2=0; [ -e "$R/sys/class/power_supply/BAT2" ] && b2=1
	adp=0; [ -e "$R/sys/class/power_supply/ADP1" ] && adp=1
	dtx=0; [ -e "$R/sys/class/misc/surface_dtx" ] && dtx=1
	base=$(prop vendor.gb.dtx.base); case "$base" in attached|detached) ;; *) base=-;; esac
	nc=0; for x in "$R"/sys/bus/surface_aggregator/devices/*; do [ -e "$x" ] && nc=$((nc + 1)); done
}
sam_clients_wait() { # $1 = polls of 0.5 s, until BAT1, ADP1 and DTX exist and the keyboard, touchpad and BAT2 too (or
	k=0              # gb_power reports a detached base); 0 when complete, k = polls used
	while :; do
		sam_clients
		if [ "$b1$adp$dtx" = 111 ] && { [ "$kbd$tp$b2" = 111 ] || [ "$base" = detached ]; }; then return 0; fi
		[ $k -ge "$1" ] && return 1
		sleep 0.5; k=$((k + 1))
	done
}
sam_state() {       # $1 = short state: /dev/gb_hw/sam and vendor.gb.sam (at most 91 characters)
	mkdir -p "$STATE" 2>/dev/null; echo "$1" > "$STATE/sam" 2>/dev/null
	setprop vendor.gb.sam "$1" 2>/dev/null
}
sam_klast() {       # the kernel's last surface_serial_hub line for device $1 saying why its probe failed (not the
	dmesg 2>/dev/null | grep -F "$SAM_DRV $1: " | grep -v -F 'probe with driver' | tail -n 1 |    # driver core's
		sed "s/^[^]]*\] $SAM_DRV $1: //" | cut -c1-160                                           # summary), <= 160
}
sam() {             # $1 = GPIO-controller wait in 0.5 s polls (0 = none), $2 = client wait in 0.5 s polls
	drv=$R/sys/bus/serial/drivers/$SAM_DRV
	devs=$(sam_devs | tr '\n' ' '); devs=${devs% }
	if [ -z "$devs" ]; then
		if [ -e "$R/sys/bus/acpi/devices/$SAM_HID:00" ]; then
			log "sam: ACPI $SAM_HID:00 exists but no serial device has it: the SAM UART did not register (see the 8250.nr_uarts, dw-apb-uart and intel-lpss lines of the kernel log)"
			sam_state "FAILED nouart"
		else
			log "sam: no $SAM_HID device (no Surface Aggregator here), nothing to do"
			sam_state absent
		fi
		return 0
	fi
	todo=; for s in $devs; do [ -e "$R/sys/bus/serial/devices/$s/driver" ] || todo="$todo $s"; done
	w=0; b=0; how=already
	if [ -n "$todo" ]; then
		while [ "$(btn_gpioc)" = unbound ] && [ $w -lt "$1" ]; do sleep 0.5; w=$((w + 1)); done
		how=kernel
		for s in $todo; do
			[ $w -gt 0 ] && sam_bound_wait "$s" 8 && continue     # a kernel that defers the probe binds it by itself
			n=0
			while [ ! -e "$R/sys/bus/serial/devices/$s/driver" ] && [ $n -lt 3 ] && [ -e "$drv/bind" ]; do
				[ $n -gt 0 ] && sleep 1
				n=$((n + 1)); b=$((b + 1)); how=bound
				echo "$s" > "$drv/bind" 2>/dev/null
				sam_bound_wait "$s" 8
			done
		done
	fi
	ms=$((w * 500)); g=$(btn_gpioc)
	bad=; for s in $devs; do [ -e "$R/sys/bus/serial/devices/$s/driver" ] || bad="$bad $s"; done
	if [ -n "$bad" ]; then
		dl=no; [ -e "$drv" ] && dl=yes
		why=; for s in $bad; do why="$why $s: $(sam_klast "$s");"; done
		log "sam: binding$bad to $SAM_DRV FAILED after $ms ms waiting for $BTN_GPIOC ($g), $b bind write(s), $SAM_DRV loaded: $dl; kernel:$why"
		sam_state "FAILED gpio=$g drv=$dl binds=$b waited_ms=$ms"
		return 0
	fi
	sam_clients_wait "$2"; cms=$((k * 500))
	m=
	for x in "keyboard:$kbd" "touchpad:$tp" "BAT2:$b2"; do [ "${x#*:}" = 1 ] || [ "$base" = detached ] || m="$m ${x%:*}"; done
	for x in "BAT1:$b1" "ADP1:$adp" "DTX:$dtx"; do [ "${x#*:}" = 1 ] || m="$m ${x%:*}"; done
	c="keyboard=$kbd touchpad=$tp BAT1=$b1 BAT2=$b2 ADP1=$adp dtx=$dtx base=$base ssam_devices=$nc after $cms ms"
	[ -n "$m" ] && c="$c, missing:$m"
	case $how in
	already) log "sam: $SAM_DRV already bound to $devs, nothing to do; clients: $c" ;;
	kernel) log "sam: $SAM_DRV bound $devs by the kernel itself after $ms ms waiting for $BTN_GPIOC, 0 bind writes; clients: $c" ;;
	*) log "sam: $SAM_DRV bound $devs after $ms ms waiting for $BTN_GPIOC, $b bind write(s); clients: $c" ;;
	esac
	if [ "$how" = already ]; then
		case "$(cat "$STATE/sam" 2>/dev/null)" in bound*|kernel*) return 0;; esac    # keep this boot's earlier result
	fi
	sam_state "$how binds=$b waited_ms=$ms kbd=$kbd tp=$tp bat=$b1$b2 adp=$adp dtx=$dtx base=$base"
	return 0
}

# ---------------------------------------------------------------------------------------------------- ish
# Integrated Sensor Hub (ISH 8086:34fc: the clipboard's accelerometer, gyroscope and orientation; first native boot,
# boot 3): lapis's intel-ish-ipc probe at ueventd coldboot can time out waiting for the ISH firmware ("waiting for ishtp
# start failed", "HBM haven't started", "ISHTP: Protocol init failed": -ENODEV, never retried); then there is no HID
# sensor hub and no accelerometer for the whole boot, and the sensors HAL scans /sys/bus/iio only once. Started at
# early-boot: hw.json sorts before sensors.json in gb.rc, so this runs while gb_sensors_wait holds init (at most 8 s,
# before class hal) waiting for cros-ec-accel. An unbound ISH gets one PCI function reset (pci_reset_function, as the
# driver's own _ish_hw_reset does) and one bind write. Idempotent (a bound ISH is only reported), bounded (one reset, one
# bind, then GB_HW_ISH_WAIT s for the accelerometer), never fatal. Result: one 'gb_hw: ish:' line, /dev/gb_hw/ish and
# vendor.gb.ish.
ISH_IDS="0x34fc"
ISH_DRV=intel_ish_ipc

ish_dev() {         # the PCI address of the ISH, or nothing
	for x in "$R"/sys/bus/pci/devices/*; do
		[ "$(cat "$x/vendor" 2>/dev/null)" = 0x8086 ] || continue
		case " $ISH_IDS " in *" $(cat "$x/device" 2>/dev/null) "*) echo "${x##*/}"; return 0;; esac
	done
}
ish_accel() {       # 1 if the sensor hub's IIO accelerometer exists (cros-ec-accel in Android mode, else accel_3d)
	for x in "$R"/sys/bus/iio/devices/iio:device*/name; do
		case "$(cat "$x" 2>/dev/null)" in cros-ec-accel|accel_3d*) echo 1; return 0;; esac
	done
	echo 0
}
ish_state() {       # $1 = short state: /dev/gb_hw/ish and vendor.gb.ish
	mkdir -p "$STATE" 2>/dev/null; echo "$1" > "$STATE/ish" 2>/dev/null
	setprop vendor.gb.ish "$1" 2>/dev/null
}
ish_klast() {       # the kernel's last intel_ish_ipc line for $1 (its message only), at most 160 characters
	dmesg 2>/dev/null | grep -F "$ISH_DRV $1: " | tail -n 1 | sed "s/^[^]]*\] $ISH_DRV $1: //" | cut -c1-160
}
ish() {             # $1 = accelerometer wait after a re-bind, in 0.5 s polls
	p=$(ish_dev)
	if [ -z "$p" ]; then log "ish: no Intel ISH ($ISH_IDS) here, nothing to do"; ish_state absent; return 0; fi
	d=$R/sys/bus/pci/devices/$p; drv=$R/sys/bus/pci/drivers/$ISH_DRV
	if [ -e "$d/driver" ]; then
		a=$(ish_accel); l=$(readlink "$d/driver" 2>/dev/null)
		log "ish: $p already bound to ${l##*/}, accelerometer=$a, nothing to do"
		ish_state "already accel=$a"
		return 0
	fi
	if [ ! -e "$drv/bind" ]; then
		log "ish: $p has no driver and $ISH_DRV is not loaded (lapis's ueventd loads it by modalias), nothing to do"
		ish_state "FAILED nodriver"
		return 0
	fi
	r=none; if [ -e "$d/reset" ]; then if echo 1 > "$d/reset" 2>/dev/null; then r=ok; else r=failed; fi; fi
	echo "$p" > "$drv/bind" 2>/dev/null
	k=0; while [ ! -e "$d/driver" ] && [ $k -lt 8 ]; do sleep 0.25; k=$((k + 1)); done
	if [ ! -e "$d/driver" ]; then
		log "ish: re-probe of $p by $ISH_DRV FAILED (reset: $r); kernel: $(ish_klast "$p")"
		ish_state "FAILED reset=$r"
		return 0
	fi
	k=0; while [ "$(ish_accel)" = 0 ] && [ $k -lt "$1" ]; do sleep 0.5; k=$((k + 1)); done
	a=$(ish_accel)
	log "ish: $p bound to $ISH_DRV again after its failed coldboot probe (reset: $r); accelerometer=$a after $((k * 500)) ms"
	ish_state "rebound reset=$r accel=$a ms=$((k * 500))"
	return 0
}

# ---------------------------------------------------------------------------------------------------- vcm
# The rear camera's focus motor (second native boot, wave 10 misc-cameras): the DW9719 voice-coil driver at 0x0c on
# I2C3, the OV8865 module's second I2cSerialBus, powered with the module (its own "vdd" is a dummy regulator).
# ipu-bridge creates its i2c client i2c-INT347A:00-VCM with the sensor runtime-resumed (k16 ipu-bridge.c
# ipu_bridge_instantiate_vcm_work) and only then adds the runtime-PM device link VCM -> sensor (DL_FLAG_PM_RUNTIME,
# without DL_FLAG_RPM_ACTIVE). Loaded by gbinit (modules.load up to wave 9), dw9719 probed inside that client creation,
# before the link existed: the link then never held the sensor for it, the sensor powered off when ipu-bridge dropped
# its reference, and 1 s later the VCM's first runtime suspend parked the lens by writing reg 0x0003 to an unpowered
# motor ("dw9719 i2c-INT347A:00-VCM: Error writing reg 0x0003: -121" in all four native boots), which left the VCM in
# the runtime-PM error state (no focus control; its system-suspend callback runs the same write). From wave 10
# dw9719.ko is in no load list (stage_kernel NATIVE_NOLOAD) and this loads it once the device AND that link exist: the
# driver core powers the sensor for the probe (pm_runtime_get_suppliers) and the link keeps it on until the VCM's first
# runtime suspend has parked the lens; then both power off. A dw9719 already bound in the error state (an older load
# list) gets one unbind + bind (the same path as reloading the module). It never touches the sensors themselves.
# Idempotent (a healthy bound VCM is only reported), bounded (GB_HW_VCM_WAIT s for the device and its link, then at
# most 2 + 4 + 2 s, a re-bind 2 + 2 + 4 s more), never fatal. Result: one 'gb_hw: vcm:' line, /dev/gb_hw/vcm and
# vendor.gb.vcm.
VCM_DEV=i2c-INT347A:00-VCM
VCM_SUP=i2c-INT347A:00
VCM_DRV=dw9719
VCM_KO=${GB_HW_VCM_KO:-/odm/lib/modules/dw9719.ko}

vcm_rpm() {         # the runtime-PM status of i2c device $1 (active | suspended | error | ...), or none
	v=$(cat "$R/sys/bus/i2c/devices/$1/power/runtime_status" 2>/dev/null); echo "${v:-none}"
}
vcm_drv() {         # the driver bound to the VCM, or none
	l=$(readlink "$R/sys/bus/i2c/devices/$VCM_DEV/driver" 2>/dev/null); l=${l##*/}
	echo "${l:-none}"
}
vcm_link() {        # the VCM's device link to the sensor: 1 = with runtime PM, 0 = without, - = none
	L=$R/sys/bus/i2c/devices/$VCM_DEV/supplier:i2c:$VCM_SUP
	if [ ! -e "$L" ]; then echo -; elif [ "$(cat "$L/runtime_pm" 2>/dev/null)" = 1 ]; then echo 1; else echo 0; fi
}
vcm_wait_drv() {    # $1 = yes (until the VCM has a driver) | no (until it has none), $2 = polls of 0.25 s
	k=0
	while :; do
		if [ -e "$R/sys/bus/i2c/devices/$VCM_DEV/driver" ]; then [ "$1" = yes ] && return 0
		else [ "$1" = no ] && return 0; fi
		[ $k -ge "$2" ] && return 1
		sleep 0.25; k=$((k + 1))
	done
}
vcm_settle() {      # $1 = polls of 0.25 s, until the VCM is runtime-suspended or in the error state; sets vr, vms
	k=0; vr=$(vcm_rpm $VCM_DEV)
	while [ "$vr" != suspended ] && [ "$vr" != error ] && [ $k -lt "$1" ]; do
		sleep 0.25; k=$((k + 1)); vr=$(vcm_rpm $VCM_DEV)
	done
	vms=$((k * 250))
}
vcm_sensor() {      # the sensor's runtime-PM status once the VCM has let it go (at most 2 s)
	k=0; v=$(vcm_rpm $VCM_SUP)
	while [ "$v" = active ] && [ $k -lt 8 ]; do sleep 0.25; k=$((k + 1)); v=$(vcm_rpm $VCM_SUP); done
	echo "$v"
}
vcm_state() {       # $1 = short state: /dev/gb_hw/vcm and vendor.gb.vcm (at most 91 characters)
	mkdir -p "$STATE" 2>/dev/null; echo "$1" > "$STATE/vcm" 2>/dev/null
	setprop vendor.gb.vcm "$1" 2>/dev/null
}
vcm_klast() {       # the kernel's last dw9719 line for the VCM (its message only), at most 160 characters
	dmesg 2>/dev/null | grep -F "$VCM_DRV $VCM_DEV: " | tail -n 1 | sed "s/^[^]]*\] $VCM_DRV $VCM_DEV: //" | cut -c1-160
}
vcm() {             # $1 = wait for the VCM device and its runtime-PM link, in 0.25 s polls
	d=$R/sys/bus/i2c/devices/$VCM_DEV; drv=$R/sys/bus/i2c/drivers/$VCM_DRV
	if [ ! -e "$R/sys/bus/acpi/devices/INT347A:00" ] && [ ! -e "$d" ]; then
		log "vcm: no rear camera (ACPI INT347A:00) here, nothing to do"; vcm_state absent; return 0
	fi
	w=0
	while { [ ! -e "$d" ] || [ "$(vcm_link)" != 1 ]; } && [ $w -lt "$1" ]; do sleep 0.25; w=$((w + 1)); done
	wms=$((w * 250)); lk=$(vcm_link); ins=-
	# a link that has just appeared: its sysfs entry exists before device_link_add() has put it in the VCM's
	# supplier list (core.c: device_register() of the link, then the list_add); let that finish
	[ $w -gt 0 ] && [ "$lk" = 1 ] && sleep 0.25
	if [ ! -e "$d" ]; then
		log "vcm: loading $VCM_DRV FAILED: no $VCM_DEV after $wms ms (ipu-bridge creates it once the rear sensor ov8865 is bound to the IPU)"
		vcm_state "FAILED nodev waited_ms=$wms"; return 0
	fi
	if [ -e "$d/driver" ]; then
		how=already
	elif [ "$lk" != 1 ]; then
		log "vcm: loading $VCM_DRV FAILED: $VCM_DEV has no runtime-PM device link to $VCM_SUP after $wms ms (link=$lk), so the sensor would be off for its probe: not loaded"
		vcm_state "FAILED nolink link=$lk waited_ms=$wms"; return 0
	elif [ -d "$R/sys/module/$VCM_DRV" ]; then
		how=bound       # loaded (by hand, or an older load list), but the VCM has no driver: one bind write
		[ -e "$drv/bind" ] && echo "$VCM_DEV" > "$drv/bind" 2>/dev/null
		vcm_wait_drv yes 8
	else
		how=loaded
		if insmod "$VCM_KO" 2>/dev/null; then ins=ok; else ins=failed; fi
		vcm_wait_drv yes 8
	fi
	if [ ! -e "$d/driver" ]; then
		log "vcm: binding $VCM_DEV to $VCM_DRV FAILED after $wms ms waiting for it and its runtime-PM link to $VCM_SUP (insmod $ins); kernel: $(vcm_klast)"
		vcm_state "FAILED unbound insmod=$ins waited_ms=$wms"; return 0
	fi
	vcm_settle 16
	if [ "$how" = already ] && [ "$vr" = error ] && [ "$lk" = 1 ] && [ -e "$drv/unbind" ] && [ -e "$drv/bind" ]; then
		how=rebound     # bound before its link existed (an older load list): one re-probe, now through the link
		echo "$VCM_DEV" > "$drv/unbind" 2>/dev/null; vcm_wait_drv no 8
		echo "$VCM_DEV" > "$drv/bind" 2>/dev/null; vcm_wait_drv yes 8
		if [ ! -e "$d/driver" ]; then
			log "vcm: re-binding $VCM_DEV to $VCM_DRV (it was in the runtime-PM error state) FAILED; kernel: $(vcm_klast)"
			vcm_state "FAILED rebind waited_ms=$wms"; return 0
		fi
		vcm_settle 16
	fi
	vs=$(vcm_sensor); dn=$(vcm_drv); st="rpm=$vr sensor=$vs waited_ms=$wms"
	case "$vr" in
	error)
		log "vcm: $dn on $VCM_DEV ($how): its runtime suspend FAILED (runtime_status error after $vms ms, sensor $vs); kernel: $(vcm_klast)"
		vcm_state "FAILED $how $st" ;;
	suspended)
		case $how in
		loaded) log "vcm: $VCM_DRV loaded after $wms ms waiting for $VCM_DEV and its runtime-PM link to $VCM_SUP: bound, runtime suspended after $vms ms (lens parked), sensor $vs" ;;
		bound) log "vcm: $VCM_DEV bound to the already loaded $VCM_DRV after $wms ms waiting, 1 bind write: runtime suspended after $vms ms (lens parked), sensor $vs" ;;
		rebound) log "vcm: $VCM_DEV was bound to $VCM_DRV before its runtime-PM link existed (runtime_status error, an older load list): unbound and bound again once, runtime suspended after $vms ms (lens parked), sensor $vs" ;;
		*) log "vcm: $VCM_DEV already bound to $dn, runtime suspended, sensor $vs, nothing to do" ;;
		esac
		vcm_state "$how $st" ;;
	*)
		log "vcm: $dn on $VCM_DEV ($how): runtime $vr after $vms ms (an open focus control keeps it active), sensor $vs"
		vcm_state "$how $st" ;;
	esac
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
	# (wave 9 hw-native1) Surface Aggregator: one more bind attempt (no waiting) if a SAM serial device still has no
	# driver and gb_hw_sam has finished, then the devices, the clients and the result; the sensor hub's state
	sd=$(sam_devs | tr '\n' ' '); u=
	for s in $sd; do [ -e "$R/sys/bus/serial/devices/$s/driver" ] || u="$u $s"; done
	if [ -n "$u" ] && [ "$(prop init.svc.gb_hw_sam)" != running ]; then
		log "diag: sam:$u still without a driver, one more bind attempt"
		sam 0 10
	fi
	sam_clients; x=
	for s in $sd; do x="$x $s=$(sam_drv_of "$s")"; done
	log "diag: sam${x:- none} keyboard=$kbd touchpad=$tp BAT1=$b1 BAT2=$b2 ADP1=$adp dtx=$dtx base=$base ssam_devices=$nc result: $(cat "$STATE/sam" 2>/dev/null || echo none)"
	p=$(ish_dev); l=; [ -n "$p" ] && l=$(readlink "$R/sys/bus/pci/devices/$p/driver" 2>/dev/null)
	log "diag: ish ${p:-none} driver=$([ -n "$l" ] && echo "${l##*/}" || echo none) accelerometer=$(ish_accel) result: $(cat "$STATE/ish" 2>/dev/null || echo none)"
	# (wave 10 misc-cameras) the rear focus motor, read only: its driver, the runtime PM of the VCM and of the sensor,
	# the device link between them and gb_hw_vcm's result
	log "diag: vcm $VCM_DEV driver=$(vcm_drv) runtime=$(vcm_rpm $VCM_DEV) sensor=$(vcm_rpm $VCM_SUP) link=$(vcm_link) result: $(cat "$STATE/vcm" 2>/dev/null || echo none)"
	# (readiness A-M1) posture: lapis's device state and whether InputReader enabled the built-in keyboard/touchpad
	posture_report "diag: posture"
	dmesg 2>/dev/null | grep -iE 'firmware|dmc|ibt-|iwlwifi.*loaded|sof.*(fw|tplg)|surface_|soc_button|gpio-keys|MSHW0040|INT3455|apds9960|lid|tablet|intel_ish|ISHTP|HBM haven' | tail -n 60 |
		while read -r l; do log "diag: k: $l"; done
}

case "$1" in
rapl-snapshot) rapl_snapshot ;;
boot) rapl_restore; backlight_perms ;;
buttons)
	bw=${GB_HW_BTN_WAIT:-30}; case "$bw" in ''|*[!0-9]*) bw=30;; esac; [ ${#bw} -gt 3 ] && bw=300
	buttons $((bw * 2)) ;;
posture) posture ;;
sam)
	sw=${GB_HW_SAM_WAIT:-30}; case "$sw" in ''|*[!0-9]*) sw=30;; esac; [ ${#sw} -gt 3 ] && sw=300; [ "$sw" -gt 300 ] && sw=300
	sc=${GB_HW_SAM_CLIENT_WAIT:-15}; case "$sc" in ''|*[!0-9]*) sc=15;; esac; [ ${#sc} -gt 3 ] && sc=120; [ "$sc" -gt 120 ] && sc=120
	sam $((sw * 2)) $((sc * 2)) ;;
ish)
	iw=${GB_HW_ISH_WAIT:-8}; case "$iw" in ''|*[!0-9]*) iw=8;; esac; [ ${#iw} -gt 2 ] && iw=60; [ "$iw" -gt 60 ] && iw=60
	ish $((iw * 2)) ;;
vcm)
	vw=${GB_HW_VCM_WAIT:-30}; case "$vw" in ''|*[!0-9]*) vw=30;; esac; [ ${#vw} -gt 3 ] && vw=300; [ "$vw" -gt 300 ] && vw=300
	vcm $((vw * 4)) ;;
diag)
	[ -n "$R" ] || sleep 45
	rapl_restore
	diag ;;
*) echo "usage: $0 rapl-snapshot|boot|buttons|posture|sam|ish|vcm|diag"; exit 2 ;;
esac
exit 0
