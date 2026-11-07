#!/system/bin/sh
# gb_hwcheck.sh - read-only hardware self-test of the Googlebook OS native image on the Surface Book 3 (lapis 276.R4).
# Installed as /vendor/bin/gb_hwcheck.sh in the native tree only, by the component inject\native\components\hwcheck.json
# (this one file, no init service, no rc text). Source: C:\GB-build\native\hw-gap-audit\tree\gb_hwcheck.sh.
#
# The person runs it by hand while sitting at the laptop. lapis is a user build (ro.debuggable=0): there is no su and
# adb runs as the shell user, which is enough for everything here (sysfs/procfs are world-readable, shell may dump the
# system services and read logcat). On the laptop: Settings > System > Developer options > Wireless debugging; on
# another computer on the same network: adb pair <ip:port> <code>, adb connect <ip:port>, then
#     adb shell sh /vendor/bin/gb_hwcheck.sh                every item, with the physical steps it asks for
#     adb shell sh /vendor/bin/gb_hwcheck.sh -n             no physical steps (state checks only, about a minute)
#     adb shell sh /vendor/bin/gb_hwcheck.sh battery ac     only these items
# Options: -n/--no-prompt (never ask for a physical step: transition checks are UNKNOWN), -q/--quick (= -n and short
# sampling windows), -w/--wait S (seconds to wait for each physical step, 10..600, default 120), --no-record, -h/--help.
# Items, always run in this order: sam input battery wifi bt audio cameras dgpu ac power dtx posture rotation suspend
# ('suspend' also prints 's0ix'; 'dtx' feeds the tablet-posture parts of 'posture' and 'rotation').
# A physical step is announced by a '>>> ACTION n:' line; nothing is ever typed: the script watches for the change and
# goes on when it happens or after the wait (then that check is UNKNOWN). If the adb session ends while the lid is
# closed, reconnect and run 'gb_hwcheck.sh suspend' (it judges the counters since boot).
# Output: per item one line 'PASS|FAIL|UNKNOWN <item> <why>' (+ indented detail lines), then one summary line
# 'GB_HWCHECK RESULT: PASS=n FAIL=n UNKNOWN=n | FAIL: ... | UNKNOWN: ... | v2 uid=... sb3=...'.
# (v2, wave 10) battery: test_battery in percent units (charge_full 100), the desktop health HAL's level (its formula
# and its own log line) == gb_power's composite == Android's level, and the low-battery guard's state when gb_power has
# one; ac: exactly one charger state change per unplug and per plug (Android's AC flag, the health HAL's log, healthd).
# Exit 0 = no FAIL, 1 = at least one FAIL, 2 = usage error, 3 = the output went away (session closed) without a record.
# Off the Surface Book 3 (dmi product name) a FAIL is reported as UNKNOWN: the hardware is judged only on the laptop.
#
# Safety (host tests: C:\GB-build\native\w11prep\selftest-tool\work\review-w10\tests): it only reads. Never a
# setprop, a 'settings put', a sysfs/procfs write, a DRM node or a PCI config read, an input injection or a grab;
# every framework call is bounded (timeout); no temp files. The only file it may write: as root (uid 0) only, its own record of this
# output in /data/vendor/gb/hwcheck/ (0700 directory, 0600 files, the newest 10 kept; --no-record: none). As the shell
# user it writes nothing. Privacy: it prints numbers, states and fixed words; values taken from the device are reduced
# to plain tokens, and every line is scrubbed of MAC/Bluetooth addresses, e-mail addresses, UUIDs and IPv4 addresses.
# It never prints network names (SSIDs), serial numbers, account or user names, device names other than the built-in
# hardware, app/package names, wake-lock tags or the kernel command line.
# mksh arithmetic is 32-bit signed: large numbers (uWh, uW, us) are compared and computed in awk only.
# Test hooks: GB_ROOT prefixes every absolute file path; GB_TEST_PATH is prepended to PATH (fake commands).

LC_ALL=C
export LC_ALL
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin:$PATH
export PATH
R=${GB_ROOT:-}
umask 077
HCV=2
PS=$R/sys/class/power_supply
PCI=$R/sys/bus/pci/devices
KBD='Microsoft Surface 045E:09AE Keyboard'
TPD='Microsoft Surface 045E:09AF Touchpad'
DTXIN='Microsoft Surface DTX Device Mode Switch'
RECDIR=$R/data/vendor/gb/hwcheck
ALL="sam input battery wifi bt audio cameras dgpu ac power dtx posture rotation suspend"

# ------------------------------------------------------------------------------------------------- helpers
rd() { V=; [ -r "$1" ] || return 1; IFS= read -r V < "$1" || [ -n "$V" ]; }     # first line of $1 -> V
tok() {             # $1 if it is one plain token (at most 64 characters), else '?'; $2 (default -) for an empty one
	case "$1" in '') printf '%s' "${2:--}";; *[!A-Za-z0-9_.,:/+=-]*) printf '?';; *) printf '%.64s' "$1";; esac
}
num() { case "$1" in ''|-|*[!0-9-]*|?*-*) printf '%s' "${2:--}";; *) printf '%s' "$1";; esac; }   # an integer or $2
prop() { getprop "$1" 2>/dev/null; }
T() { timeout 20 "$@" 2>/dev/null; }                 # every framework call is bounded
TW() { _s=$1; shift; timeout "$_s" "$@" 2>/dev/null; }
lnk() { _l=$(readlink "$1" 2>/dev/null); printf '%s' "${_l##*/}"; }      # the basename of a symlink's target
upt() { rd "$R/proc/uptime"; UP=${V%%.*}; case "$UP" in ''|*[!0-9]*) UP=0;; esac; }
gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !((a + 0) > (b + 0)) }'; }   # $1 > $2 (any size)
inname() {          # IN = 1 if an input device is named exactly $1 (sysfs), else 0; EVN = its event node name (or empty)
	IN=0; EVN=
	for _x in "$R"/sys/class/input/input*; do
		[ -r "$_x/name" ] || continue
		rd "$_x/name"
		[ "$V" = "$1" ] || continue
		for _e in "$_x"/event*; do [ -e "$_e" ] && EVN=${_e##*/}; done
		IN=1; return 0
	done
	return 1
}
inp() { inname "$1"; printf '%s' "$IN"; }          # the same as a value (for $(...))
holders() {         # the dGPU holders (vendor.gb.dgpu.holders "comm:pid,..."): native names only, an app's name -> 'app'
	prop vendor.gb.dgpu.holders | awk -F, '{ for (i = 1; i <= NF; i++) { c = $i; sub(/:.*/, "", c)
		if (c !~ /^\+?[A-Za-z0-9_-]+$/) c = "app"; o = o (o == "" ? "" : ",") c } } END { print (o == "" ? "none" : o) }'
}

# ------------------------------------------------------------------------------------------------- output
NP=0; NF=0; NU=0; LP=; LF=; LU=; REC=; OUTDEAD=0
scrub() {           # MAC/Bluetooth addresses, e-mail addresses, UUIDs, IPv4 addresses -> placeholders
	sed -e 's/[0-9A-Fa-f][0-9A-Fa-f]\([:-][0-9A-Fa-f][0-9A-Fa-f]\)\{5\}/<mac>/g' \
		-e 's/[A-Za-z0-9._%+-][A-Za-z0-9._%+-]*@[A-Za-z0-9-][A-Za-z0-9.-]*\.[A-Za-z][A-Za-z]*/<email>/g' \
		-e 's/[0-9A-Fa-f]\{8\}-[0-9A-Fa-f]\{4\}-[0-9A-Fa-f]\{4\}-[0-9A-Fa-f]\{4\}-[0-9A-Fa-f]\{12\}/<uuid>/g' \
		-e 's/[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}/<ipv4>/g'
}
emit() {            # one output line: scrubbed, to stdout and (root) to the record
	_o=$*
	case "$_o" in *@*|*:*:*|*-*-*-*|*.*.*.*) _o=$(printf '%s\n' "$_o" | scrub);; esac
	printf '%s\n' "$_o" 2>/dev/null || OUTDEAD=1
	[ -n "$REC" ] && printf '%s\n' "$_o" >> "$REC" 2>/dev/null
	if [ "$OUTDEAD" = 1 ] && [ -z "$REC" ]; then exit 3; fi
	return 0
}
verdict() {         # $1 item, $2 PASS|FAIL|UNKNOWN, $3... why
	_i=$1; _s=$2; shift 2; _w=$*
	if [ "$_s" = FAIL ] && [ "$SB3" != 1 ]; then _s=UNKNOWN; _w="$_w (not a Surface Book 3: judged only on the laptop)"; fi
	case $_s in
	PASS) NP=$((NP + 1)); LP="$LP $_i" ;;
	FAIL) NF=$((NF + 1)); LF="$LF $_i" ;;
	*) _s=UNKNOWN; NU=$((NU + 1)); LU="$LU $_i" ;;
	esac
	emit "$(printf '%-7s %-8s %s' "$_s" "$_i" "$_w")"
}
note() { _i=$1; shift; emit "        $_i: $*"; }

# ------------------------------------------------------------------------------------------------- physical steps
PROMPT=1; WAIT=120; QUICK=0; NACT=0
ask() {             # announce a physical step; 1 (skip it) with --no-prompt
	[ "$PROMPT" = 1 ] || return 1
	NACT=$((NACT + 1))
	emit ">>> ACTION $NACT: $* (waiting up to $WAIT s; nothing to type)"
	return 0
}
waitfor() {         # $1 = condition command, $2 = seconds; WAITED = seconds it took; 0 when the condition holds
	upt; _t0=$UP; WAITED=0; _k=0
	while :; do
		if "$1"; then upt; WAITED=$((UP - _t0)); return 0; fi
		upt; WAITED=$((UP - _t0)); _k=$((_k + 1))
		if [ "$WAITED" -ge "$2" ] || [ "$_k" -gt $(($2 * 2 + 5)) ]; then return 1; fi
		sleep 1
	done
}

# ------------------------------------------------------------------------------------------------- power supplies
# One line per supply: name type present online status capacity e_now e_full e_design q_now q_full q_design v_design
# v_now p_now i_now scope charge_counter driver ('-' = not reported). Only these POWER_SUPPLY_* keys are read (never
# SERIAL_NUMBER, MODEL_NAME or MANUFACTURER).
AWK_PSY='
BEGIN { t = "-"; pr = "-"; on = "-"; st = "-"; cap = "-"; en = "-"; ef = "-"; ed = "-"; qn = "-"; qf = "-"; qd = "-"
        vd = "-"; vn = "-"; pn = "-"; inow = "-"; sc = "-"; cc = "-" }
{
	i = index($0, "="); if (i == 0) next
	k = substr($0, 1, i - 1); v = substr($0, i + 1)
	if (substr(k, 1, 13) != "POWER_SUPPLY_") next
	k = substr(k, 14); isn = (v ~ /^-?[0-9]+$/)
	if (k == "TYPE") t = (v == "Battery" ? "Battery" : (v == "Mains" ? "Mains" : (v ~ /^USB/ ? "USB" : "Other")))
	else if (k == "STATUS") st = (v == "Charging" ? "charging" : (v == "Discharging" ? "discharging" : (v == "Not charging" ? "not-charging" : (v == "Full" ? "full" : "unknown"))))
	else if (k == "SCOPE") sc = (v == "Device" ? "Device" : (v == "System" ? "System" : "Unknown"))
	else if (isn) {
		if (k == "PRESENT") pr = v; else if (k == "ONLINE") on = v; else if (k == "CAPACITY") cap = v
		else if (k == "ENERGY_NOW") en = v; else if (k == "ENERGY_FULL") ef = v; else if (k == "ENERGY_FULL_DESIGN") ed = v
		else if (k == "CHARGE_NOW") qn = v; else if (k == "CHARGE_FULL") qf = v; else if (k == "CHARGE_FULL_DESIGN") qd = v
		else if (k == "VOLTAGE_MIN_DESIGN") vd = v; else if (k == "VOLTAGE_NOW") vn = v; else if (k == "POWER_NOW") pn = v
		else if (k == "CURRENT_NOW") inow = v; else if (k == "CHARGE_COUNTER") cc = v
	}
}
END { print n, t, pr, on, st, cap, en, ef, ed, qn, qf, qd, vd, vn, pn, inow, sc, cc, drv }'
psy_all() {         # PSY = the AWK_PSY line of every supply
	PSY=
	for _p in "$PS"/*; do
		[ -r "$_p/uevent" ] || continue
		_n=$(tok "${_p##*/}"); _d=$(tok "$(lnk "$_p/device/driver")")
		_l=$(awk -v n="$_n" -v drv="$_d" "$AWK_PSY" "$_p/uevent" 2>/dev/null)
		[ -n "$_l" ] && PSY="$PSY$_l
"
	done
}
psy_field() {       # $1 supply name, $2 field number (1-based, AWK_PSY order): the value or '-'
	printf '%s' "$PSY" | awk -v n="$1" -v f="$2" '$1 == n { v = $f } END { print (v == "" ? "-" : v) }'
}
# gb_power's combined battery (gb_power.c combine()): the energy-weighted sum of every real pack (a Battery that is not
# test_power's, driven by surface_battery or not device-scoped), charge converted with the pack's design voltage, level
# floor(E_now*100/E_full + 0.05), Full -> 100; net power signed (+ into the packs). Output line 1: nreal nbat cap status
# ac e_now e_full net_uW rate_known; line 2: the packs, '|'-separated.
AWK_COMBO='
function pos(x) { return (x != "-" && x + 0 > 0) }
{
	name = $1; ty = $2; pres = $3; onl = $4; stat = $5; capa = $6; e_now = $7; e_full = $8; e_des = $9; q_now = $10
	q_full = $11; q_des = $12; v_des = $13; v_now = $14; p_now = $15; i_now = $16; scope = $17; drv = $19
	if (ty != "Battery") { if (substr(name, 1, 5) != "test_" && onl != "-") { ac = ((ac == 1 || onl + 0 > 0) ? 1 : 0); acs = 1 }; next }
	if (substr(name, 1, 5) == "test_") next
	if (!(drv == "surface_battery" || scope != "Device")) next
	nreal++
	if (pres == "0") { packs = packs sprintf("%s%s absent", (packs == "" ? "" : " | "), name); next }
	vd = pos(v_des) ? v_des : (pos(v_now) ? v_now : 11100000)
	if (e_now != "-" && e_now + 0 >= 0 && (pos(e_full) || pos(e_des))) { now = e_now; full = pos(e_full) ? e_full : e_des; des = pos(e_des) ? e_des : full }
	else if (q_now != "-" && q_now + 0 >= 0 && (pos(q_full) || pos(q_des))) { now = q_now * vd / 1e6; full = (pos(q_full) ? q_full : q_des) * vd / 1e6; des = (pos(q_des) ? q_des : (pos(q_full) ? q_full : q_des)) * vd / 1e6 }
	else if (capa != "-") { full = 50000000; des = full; now = full * capa / 100 }
	else next
	if (full + 0 <= 0) next
	if (now + 0 > full + 0) now = full
	if (now + 0 < 0) now = 0
	nbat++; en += now; ef += full; ed += des
	pw = (p_now != "-" && p_now + 0 >= 0) ? p_now : ((i_now != "-" && i_now + 0 >= 0 && pos(v_now)) ? i_now * v_now / 1e6 : -1)
	if (stat == "charging") { nchg++; if (pw < 0) pk = 0; else pnet += pw }
	else if (stat == "discharging") { ndis++; if (pw < 0) pk = 0; else pnet -= pw }
	else if (stat == "full") nfull++
	sw = (stat == "charging" ? pw : (stat == "discharging" ? -pw : 0))
	packs = packs sprintf("%s%s %d%% %s %.1f/%.1f Wh, health %d%% of design, %s W", (packs == "" ? "" : " | "), name,
		int(now * 100 / full), stat, now / 1e6, full / 1e6, int(full * 100 / des + 0.5), (pw < 0 ? "?" : sprintf("%+.1f", sw / 1e6)))
}
BEGIN { ac = -1; pk = 1; pnet = 0 }
END {
	if (nbat + 0 == 0) { printf "%d 0 - - %d 0 0 0 0\n", nreal, (ac < 0 ? 1 : ac); print packs; exit }
	if (ac < 0) ac = (ndis + 0 == 0 && (nchg + nfull > 0)) ? 1 : 0
	cap = int(en * 100 / ef + 0.05); if (cap > 100) cap = 100
	if (!ac) st = "discharging"; else if (nchg > 0 && (!pk || pnet > 0)) st = "charging"
	else if (nfull == nbat || cap >= 100) st = "full"; else st = "not-charging"
	if (st == "full") cap = 100
	printf "%d %d %d %s %d %.0f %.0f %.0f %d\n", nreal, nbat, cap, st, ac, en, ef, pnet, pk
	print packs
}'
combo() {           # C_* from PSY: C_NREAL C_NBAT C_CAP C_ST C_AC C_EN C_EF C_PNET C_PK, C_PACKS
	_c=$(printf '%s' "$PSY" | awk "$AWK_COMBO")
	set -- $(printf '%s\n' "$_c" | head -n 1)
	C_NREAL=${1:-0}; C_NBAT=${2:-0}; C_CAP=${3:--}; C_ST=${4:--}; C_AC=${5:--}; C_EN=${6:-0}; C_EF=${7:-0}
	C_PNET=${8:-0}; C_PK=${9:-0}
	C_PACKS=$(printf '%s\n' "$_c" | sed -n 2p)
}
and_bat() {         # A_LEVEL A_AC A_STATUS A_PRESENT A_STOP A_CL from Android's BatteryService (dumpsys battery)
	set -- $(T dumpsys battery | awk '
		/UPDATES STOPPED/ { stop = 1 }
		/^  AC powered: / { ac = $3 }
		/^  status: / { st = $2 }
		/^  present: / { pr = $2 }
		/^  level: / { lv = $2 }
		/^  Capacity level: / { cl = $3 }
		END { printf "%s %s %s %s %d %s\n", (lv == "" ? "-" : lv), (ac == "" ? "-" : ac), (st == "" ? "-" : st), (pr == "" ? "-" : pr), stop, (cl == "" ? "-" : cl) }')
	A_LEVEL=$(num "${1:-}"); A_AC=$(tok "${2:-}"); A_STATUS=$(num "${3:-}"); A_PRESENT=$(tok "${4:-}"); A_STOP=${5:-0}
	A_CL=$(num "${6:-}")    # (w10) BatteryCapacityLevel: -1 unsupported, 0 unknown, 1 critical, 2 low, 3 normal, 4 high, 5 full
}
st_name() { case "$1" in 1) echo unknown;; 2) echo charging;; 3) echo discharging;; 4) echo not-charging;; 5) echo full;; *) echo "$1";; esac; }
absdiff() { awk -v a="$1" -v b="$2" 'BEGIN { d = a - b; if (d < 0) d = -d; print int(d) }'; }
hal_level() {       # the desktop health HAL's last 'Battery Debug: Level=N%' (logcat), or nothing
	T logcat -d -s android.hardware.health-service.android-desktop | sed -n 's/.*Battery Debug: Level=\([0-9][0-9]*\)%.*/\1/p' | tail -n 1
}
# (review) the 2nd boot's charger flicker was a flap of a few ms (the HAL read gb_power -o's 'no battery, on mains'
# defaults: AC=1 for 9-19 ms while unplugged, every ~10 s during gb_dgpu_report's boot-time loop), which a 1 s poll of
# 'dumpsys battery' does not see. The HAL logs every update ('Battery Debug: ... AC=n'), and healthd's kernel line says
# 'battery none' whenever it found no battery: both see a flap of any length while it is still in the log buffers.
hal_flaps() {       # HF = AC flaps in the HAL's log (the flag changed and changed back within 1 s), HN = its lines,
	# HT = the first line's time, HL = the last flap's time (hh:mm:ss; '-' = none)
	set -- $(T logcat -d -v threadtime -s android.hardware.health-service.android-desktop | awk '
		/Battery Debug: / && $2 ~ /^[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ && match($0, / AC=[01] /) {
			v = substr($0, RSTART + 4, 1); n++; split($2, c, ":"); s = c[1] * 3600 + c[2] * 60 + c[3]
			if (n == 1) ft = substr($2, 1, 8)
			else if (v != pv) {
				if (nc > 0 && v == ppv && s >= tc && s - tc <= 1) { f++; lt = substr($2, 1, 8) }
				ppv = pv; tc = s; nc++
			}
			pv = v
		}
		END { printf "%d %d %s %s\n", f, n, (ft == "" ? "-" : ft), (lt == "" ? "-" : lt) }')
	HF=$(num "${1:-}" 0); HN=$(num "${2:-}" 0); HT=$(tok "${3:-}"); HL=$(tok "${4:-}")
}
kmsg_none() {       # KN = healthd's 'battery none' kernel lines after its first 'battery l=' line ('-': log not readable)
	KN=-; _k=$(dmesg 2>/dev/null) || return 0
	KN=$(printf '%s\n' "$_k" | awk '/healthd: battery l=/ { s = 1 } s && /healthd: battery none/ { n++ } END { print n + 0 }')
}
tb_key() {          # (w10) $1 = a POWER_SUPPLY_ key: its value in test_battery's uevent as one plain token ('-' = absent)
	tok "$(awk -v k="POWER_SUPPLY_$1=" 'index($0, k) == 1 { v = substr($0, length(k) + 1) } END { print v }' "$PS/test_battery/uevent" 2>/dev/null)"
}
# (w10) one charger state change per plug/unplug: wave 10's fix (one test_power writer, gb_power -o read-only) means
# that an unplug or a plug changes the charger state exactly once everywhere. Before each step the newest line of the
# health HAL's log (every update: 'Battery Debug: ... AC=n') and of healthd's kernel lines ('chg=a' = on the charger,
# 'battery none' = no battery) is marked; afterwards only the lines after the mark are counted.
hal_ac() {          # the health HAL's 'Battery Debug' lines as "MM-DD_hh:mm:ss.mmm AC" (only the time and the flag)
	T logcat -d -v threadtime -s android.hardware.health-service.android-desktop | awk '
		/Battery Debug: / && $1 ~ /^[0-9][0-9]-[0-9][0-9]$/ && $2 ~ /^[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/ && match($0, / AC=[01] /) {
			print $1 "_" $2, substr($0, RSTART + 4, 1) }'
}
kmsg_hd() {         # healthd's kernel lines as "seconds AC none" (AC 1 = 'chg=a', none 1 = 'battery none'); 1 = not readable
	_k=$(dmesg 2>/dev/null) || return 1
	printf '%s\n' "$_k" | awk '/healthd: battery / && /^\[ *[0-9]+\.[0-9]+\]/ { t = $0; sub(/^\[ */, "", t); sub(/\].*$/, "", t)
		print t, (index($0, " chg=a") ? 1 : 0), (index($0, "battery none") ? 1 : 0) }'
}
step_mark() {       # before a step: SM_H SM_HV = the HAL's newest line (time, AC), SM_K SM_KV = healthd's ('-' = none/unreadable)
	set -- $(hal_ac | tail -n 1); SM_H=$(tok "${1:-}"); SM_HV=$(num "${2:-}")
	SM_K=-; SM_KV=-
	if _x=$(kmsg_hd); then
		set -- $(printf '%s\n' "$_x" | tail -n 1); SM_K=$(tok "${1:-0}"); SM_KV=$(num "${2:-}")
	fi
}
step_count() {      # $1 = the charger flag before the step: SC_HN SC_HC SC_HL = HAL lines after the mark, their AC changes,
	# the last flag; SC_KN SC_KC SC_KNONE = healthd's lines after the mark, charger changes, 'battery none' ('-' = not read)
	_b=$1                             # (review-w10) kept: 'set --' below replaces $1
	_p=$SM_HV; [ "$_p" = - ] && _p=$_b
	set -- $(hal_ac | awk -v m="$SM_H" -v p="$_p" 'm == "-" || $1 > m { n++; if ($2 != p) { c++; p = $2 } }
		END { printf "%d %d %s\n", n, c, (n ? p : "-") }')
	SC_HN=$(num "${1:-}" 0); SC_HC=$(num "${2:-}" 0); SC_HL=$(num "${3:-}")
	SC_KN=-; SC_KC=-; SC_KNONE=-
	[ "$SM_K" = - ] && return 0
	_x=$(kmsg_hd) || return 0
	_p=$SM_KV; [ "$_p" = - ] && _p=$_b  # (review-w10) no healthd line before the mark: the flag before the step
	set -- $(printf '%s\n' "$_x" | awk -v m="$SM_K" -v p="$_p" '$1 + 0 > m + 0 { n++; z += $3; if ($2 != p) { c++; p = $2 } }
		END { printf "%d %d %d\n", n, c, z }')
	SC_KN=$(num "${1:-}" 0); SC_KC=$(num "${2:-}" 0); SC_KNONE=$(num "${3:-}" 0)
}
dmi_sb3() { rd "$R/sys/class/dmi/id/product_name"; [ "$V" = "Surface Book 3" ]; }

# ------------------------------------------------------------------------------------------------- sam
chk_sam() {
	s=$(prop vendor.gb.sam); sw=${s%% *}
	sd=; drv=none; fw=-
	for x in "$R"/sys/bus/serial/devices/*; do
		[ -e "$x" ] || continue
		rd "$x/modalias"; m=$V; rd "$x/firmware_node/hid"; h=$V
		case "$m" in acpi:MSHW0084:*) ;; *) [ "$h" = MSHW0084 ] || continue;; esac
		sd=$(tok "${x##*/}"); drv=$(tok "$(lnk "$x/driver")" none)
		rd "$x/firmware_version" && fw=$(tok "$V")
	done
	nc=0; for x in "$R"/sys/bus/surface_aggregator/devices/*; do [ -e "$x" ] && nc=$((nc + 1)); done
	kbd=$(inp "$KBD"); tp=$(inp "$TPD")
	b1=0; [ -e "$PS/BAT1" ] && b1=1; b2=0; [ -e "$PS/BAT2" ] && b2=1; adp=0; [ -e "$PS/ADP1" ] && adp=1
	dtx=0; [ -e "$R/sys/class/misc/surface_dtx" ] && dtx=1
	base=$(prop vendor.gb.dtx.base)
	note sam "MSHW0084 ${sd:-none} driver=$drv firmware=$fw ssam_devices=$nc | keyboard=$kbd touchpad=$tp BAT1=$b1 BAT2=$b2 ADP1=$adp dtx=$dtx base=$(tok "$base") | vendor.gb.sam=$(tok "$sw")"
	if [ -z "$sd" ]; then
		verdict sam FAIL "no Surface Aggregator serial device (ACPI MSHW0084) on the serial bus (vendor.gb.sam=$(tok "$sw"))"; return
	fi
	if [ "$drv" != surface_serial_hub ]; then
		verdict sam FAIL "$sd is not bound to surface_serial_hub (driver=$drv, vendor.gb.sam=$(tok "$sw")): no keyboard base, touchpad, batteries, charger or DTX"; return
	fi
	miss=
	[ "$b1" = 1 ] || miss="$miss BAT1"; [ "$adp" = 1 ] || miss="$miss ADP1"; [ "$dtx" = 1 ] || miss="$miss DTX"
	if [ "$base" != detached ]; then
		[ "$kbd" = 1 ] || miss="$miss keyboard"; [ "$tp" = 1 ] || miss="$miss touchpad"; [ "$b2" = 1 ] || miss="$miss BAT2"
	fi
	case "$sw" in FAILED*) miss="$miss (gb_hw sam reported FAILED)";; esac
	if [ -n "$miss" ]; then verdict sam FAIL "surface_serial_hub bound, missing SAM clients:$miss"; return; fi
	verdict sam PASS "surface_serial_hub bound to $sd, $nc SAM devices, every client present${base:+ (base $(tok "$base"))}"
}

# ------------------------------------------------------------------------------------------------- input
inp_state() {       # "keyboard_enabled touchpad_enabled keyboard_class" from dumpsys input ('-' = not listed)
	T dumpsys input | awk -v k="$KBD" -v t="$TPD" '
		/^[^ ]/ { d = ""; next }
		/^    -?[0-9]+: / || /^  Device -?[0-9]+: / { d = ""; if (index($0, k)) d = "k"; else if (index($0, t)) d = "t"; next }
		d != "" && /^ *Enabled: / { v = $2; if (d == "k") { if (ke != "false") ke = v } else { if (te != "false") te = v } ; next }
		d == "k" && /^ *Classes: / { kc = (index($0, "GAMEPAD") ? "GAMEPAD" : "keyboard") }
		END { printf "%s %s %s\n", (ke == "" ? "-" : ke), (te == "" ? "-" : te), (kc == "" ? "-" : kc) }'
}
ev_seen() {         # $1 = event node name, $2 = seconds, $3 = events: 0 = events arrived, 1 = none, 2 = cannot read
	[ -n "$1" ] || return 2
	TW "$2" getevent -c "$3" "/dev/input/$1" > /dev/null
	case $? in 0) return 0;; 124|137|143) return 1;; *) return 2;; esac
}
chk_input() {
	base=$(prop vendor.gb.dtx.base)
	inname "$KBD"; kbd=$IN; kev=$EVN; inname "$TPD"; tp=$IN; tev=$EVN
	set -- $(inp_state); ke=$(tok "${1:-}"); te=$(tok "${2:-}"); kc=$(tok "${3:-}")
	note input "keyboard: present=$kbd android_enabled=$ke class=$kc | touchpad: present=$tp android_enabled=$te | base=$(tok "$base")"
	if [ "$base" = detached ]; then verdict input UNKNOWN "the keyboard base is detached (vendor.gb.dtx.base=detached)"; return; fi
	bad=
	[ "$kbd" = 1 ] || bad="$bad keyboard absent;"; [ "$tp" = 1 ] || bad="$bad touchpad absent;"
	[ "$kc" = GAMEPAD ] && bad="$bad the keyboard is classified GAMEPAD;"
	[ "$ke" = false ] && bad="$bad Android disabled the keyboard (posture?);"
	[ "$te" = false ] && bad="$bad Android disabled the touchpad (posture?);"
	if [ -n "$bad" ]; then verdict input FAIL "${bad% ;}"; return; fi
	ex=; miss=
	if ask "press any key on the keyboard base, then move a finger on the touchpad"; then
		ev_seen "$kev" "$WAIT" 1; r=$?      # only counted: which key it was is never read
		case $r in 0) ex="keys arrive";; 1) miss="no key event within $WAIT s";; *) ex="key events not readable here";; esac
		ev_seen "$tev" 30 3; r=$?
		case $r in 0) ex="${ex:+$ex, }touchpad events arrive";; 1) miss="${miss:+$miss, }no touchpad event within 30 s";; *) ex="${ex:+$ex, }touchpad events not readable here";; esac
		if [ -n "$miss" ]; then verdict input UNKNOWN "present and enabled, but $miss (not pressed/touched?)${ex:+; $ex}"; return; fi
	fi
	en_t="present and enabled"; [ "$ke$te" = -- ] && en_t="present (Android's input state not readable: dumpsys input)"   # (review)
	verdict input PASS "keyboard and touchpad $en_t${ex:+; $ex}"
}

# ------------------------------------------------------------------------------------------------- battery
# (w10) the low-battery guard (BACKLOG W10-BAT-LOWSHUT; wave 11's gb_power guard, area battery-lowshut): AOSP's
# BatteryService shuts down cleanly on its own only when the health HAL reports the capacity level CRITICAL, and k16's
# test_power reports Normal at any level (test_power.c 106-107, no parameter). So on k16 a guard asks for the clean
# shutdown itself (sys.powerctl) at <= G_CRIT % on battery and the capacity level stays Normal; with a test_power that
# has a battery_capacity_level parameter the guard can publish the level instead. The HAL reads
# test_battery/capacity_level (a value published over the driver's own shows in that file, not in its uevent); Android
# shows it as 'Capacity level: N' (1 critical, 2 low, 3 normal, 5 full). Checked, whichever path a guard takes: never
# Critical on the charger, never Critical above G_LATE %; at <= G_WARN % on battery a warning (plug in now); the kill
# switch gb.power.lowshut=0 on the kernel command line (only that token is looked for; the line is never printed).
# When the kernel log is readable (root, or a shell allowed to read it), gb_power's own lines tell whether this gb_power
# has the guard at all: 'gb_power: low-battery guard: on (...)' / '... off (gb.power.lowshut=0)' when it starts, and
# 'gb_power: low battery: ...' while it acts (only counted; 'requesting a clean shutdown' is noted). A guard whose
# start line disagrees with the kill switch is a FAIL.
G_CRIT=3; G_WARN=5; G_LATE=10
cl_num() { case "$1" in Unknown) echo 0;; Critical) echo 1;; Low) echo 2;; Normal) echo 3;; High) echo 4;; Full) echo 5;; *) echo -1;; esac; }
guard_state() {     # G_BAD = a FAIL reason (empty: none), G_TXT = the guard's state for the PASS line; one note
	G_BAD=; rd "$PS/test_battery/capacity_level"; g_f=$(tok "$V"); g_u=$(tb_key CAPACITY_LEVEL)
	g_p=-; rd "$R/sys/module/test_power/parameters/battery_capacity_level" && g_p=$(tok "$V")
	g_ks=-; rd "$R/proc/cmdline" && { g_ks=0; case " $V " in *" gb.power.lowshut=0 "*) g_ks=1;; esac; }
	g_seen=0; case "$g_f" in Critical|Low|High|Full) g_seen=1;; esac
	[ "$g_f" != - ] && [ "$g_u" != - ] && [ "$g_f" != "$g_u" ] && g_seen=1       # published over the driver's own value
	[ "$g_p" != - ] && g_seen=1
	g_cap=$(num "$tb_cap"); g_bat=0; [ "$C_AC" = 0 ] && [ "$C_ST" = discharging ] && g_bat=1
	g_lg=-; g_ll=0; g_lr=0                        # gb_power's guard lines in the kernel log ('-' = not readable)
	if _k=$(dmesg 2>/dev/null); then
		set -- $(printf '%s\n' "$_k" | awk '/gb_power: low-battery guard: on/ { g = "on" } /gb_power: low-battery guard: off/ { g = "off" }
			/gb_power: low battery: / { n++; if (index($0, "requesting a clean shutdown")) r = 1 }
			END { printf "%s %d %d\n", (g == "" ? "none" : g), n, r }')
		g_lg=$(tok "${1:-}"); g_ll=$(num "${2:-}" 0); g_lr=$(num "${3:-}" 0)
	fi
	if [ "$g_lg" = on ] && [ "$g_ks" = 1 ]; then
		G_BAD="gb_power's low-battery guard started 'on' although the kernel command line has gb.power.lowshut=0 (the kill switch is ignored)"
	elif [ "$g_lg" = off ] && [ "$g_ks" = 0 ]; then
		G_BAD="gb_power's low-battery guard started 'off' although the kernel command line has no gb.power.lowshut=0: nothing shuts Android down cleanly at a real 0 %"
	fi
	[ "$g_lr" = 1 ] && note battery "gb_power's low-battery guard has requested a clean shutdown (kernel log): Android is shutting down"
	if [ "$g_cap" != - ]; then
		if [ "$g_f" = Critical ] && [ "$C_AC" = 1 ]; then         # (review-w10) appended: a kill-switch fault stays named
			G_BAD="${G_BAD:+$G_BAD; }capacity level Critical on the charger at $g_cap %: Android may shut down while charging (a low-battery guard must never act on the charger)"
		elif [ "$g_f" = Critical ] && [ "$g_cap" -gt "$G_LATE" ]; then
			G_BAD="${G_BAD:+$G_BAD; }capacity level Critical at $g_cap %: Android shuts down far too early (a low-battery guard acts at <= $G_CRIT %)"
		fi
		if [ "$g_bat" = 1 ] && [ "$g_cap" -le "$G_WARN" ]; then
			note battery "WARNING: $g_cap % on battery: plug the charger in now (below about 10 % only a low-battery guard shuts Android down cleanly, at <= $G_CRIT %: W10-BAT-LOWSHUT$([ "$g_ks" = 1 ] && printf '; this boot has it switched off'))"
		fi
	fi
	g_a=; [ "$A_CL" != - ] && [ "$g_f" != - ] && [ "$A_CL" != "$(cl_num "$g_f")" ] && g_a=" (Android's capacity level $A_CL does not match: the health HAL did not pass $g_f on)"
	g_k=; [ "$g_ks" = 1 ] && g_k="; switched off on this boot (gb.power.lowshut=0)"
	g_kt="not readable here"; [ "$g_ks" = 0 ] && g_kt="not set"; [ "$g_ks" = 1 ] && g_kt=set
	g_g=; [ "$g_lg" = on ] && g_g="low-battery guard on (gb_power's start line); "; [ "$g_lg" = off ] && g_g="low-battery guard off (gb_power's start line); "
	[ "$g_lg" = none ] && g_g="no low-battery guard start line in the kernel log; "
	if [ "$g_p" != - ]; then G_TXT="${g_g}capacity level $g_f (test_power battery_capacity_level=$g_p)$g_a$g_k"
	elif [ "$g_seen" = 1 ]; then G_TXT="${g_g}capacity level $g_f (published)$g_a$g_k"
	else G_TXT="${g_g}capacity level $g_f at $g_cap % (k16 test_power has no capacity-level parameter: the level stays Normal; a low-battery guard, where gb_power has one (wave 11), shuts down via sys.powerctl)$g_k"; fi
	g_lt="not readable here"; [ "$g_lg" != - ] && g_lt="guard $g_lg, $g_ll 'low battery' line(s)"
	note battery "low-battery guard: test_battery/capacity_level=$g_f (driver uevent $g_u, test_power parameter $g_p) Android capacity level=$A_CL | $([ "$g_seen" = 1 ] && echo published || echo 'not published') | kill switch gb.power.lowshut=0: $g_kt | kernel log: $g_lt | rule: never Critical on the charger, never above $G_LATE %; warning at <= $G_WARN % on battery$g_a"
}
chk_battery() {
	n=0
	while :; do
		psy_all; combo; and_bat; hl=$(hal_level)
		tb_cap=$(psy_field test_battery 6); tb_st=$(psy_field test_battery 5)
		tb_qn=$(psy_field test_battery 10); tb_qf=$(psy_field test_battery 11); tb_cc=$(psy_field test_battery 18)
		# (w10) the desktop health HAL's own formula on test_battery: floor(charge_now*100/charge_full + 0.05) (charge_now
		# = test_power's capacity); with wave 10's percent units (charge_full 100) it is gb_power's composite exactly
		hc=-; case "$tb_qn:$tb_qf" in *-*) ;; *) gt "$tb_qf" 0 && hc=$(awk -v a="$tb_qn" -v b="$tb_qf" 'BEGIN { print int(a * 100 / b + 0.05) }');; esac
		bad=
		if [ "$C_NBAT" = 0 ]; then
			bad="no battery pack in /sys/class/power_supply ($C_NREAL battery supplies, all absent or none): the Surface Aggregator clients are missing"
		elif [ "$tb_cap" = - ]; then
			bad="gb_power's combined battery (test_power's test_battery) is missing: Android has no battery to show"
		elif [ "$(absdiff "$tb_cap" "$C_CAP")" -gt 1 ]; then
			bad="gb_power publishes $tb_cap % (test_battery) but the packs hold $C_CAP %"
		elif [ "$tb_qf" != - ] && [ "$tb_qf" != 100 ]; then
			bad="test_battery is not in percent units (charge_full=$tb_qf; wave 10 publishes 100): the desktop health HAL shows charge_now*100/charge_full = $tb_qn*100/$tb_qf = $hc % while gb_power's composite is $tb_cap % (the 2nd boot's 0 % bug: a gb_power older than wave 10)"
		elif [ "$hc" != - ] && [ "$hc" != "$tb_cap" ]; then
			bad="the desktop health HAL's formula floor(charge_now*100/charge_full + 0.05) = $tb_qn*100/$tb_qf gives $hc %, not gb_power's composite $tb_cap %"
		elif [ -n "$hl" ] && [ "$(absdiff "$hl" "$tb_cap")" -gt 1 ]; then
			bad="the health HAL shows $hl % (its newest 'Battery Debug' line) but gb_power's composite is $tb_cap %"
		elif [ "$A_STOP" = 1 ]; then
			bad=stopped
		elif [ "$A_LEVEL" = - ]; then
			bad=noandroid
		elif [ "$(absdiff "$A_LEVEL" "$tb_cap")" -gt 1 ]; then
			bad="Android shows $A_LEVEL % but gb_power's composite is $tb_cap % (the packs $C_CAP %${hl:+, the health HAL $hl %})"
		elif [ "$A_PRESENT" = false ]; then
			bad="Android says no battery is present (present: false)"
		fi
		[ -z "$bad" ] || [ "$bad" = stopped ] || [ "$bad" = noandroid ] || [ $n -ge 1 ] || [ "$QUICK" = 1 ] && break
		n=$((n + 1)); sleep 5           # one more look: a value may have been mid-update
	done
	note battery "packs: ${C_PACKS:-none}"
	note battery "combined (gb_power formula): $C_CAP % $C_ST ac=$C_AC $(awk -v a="$C_EN" -v b="$C_EF" -v p="$C_PNET" -v k="$C_PK" 'BEGIN { printf "%.1f/%.1f Wh, net %s W", a / 1e6, b / 1e6, (k ? sprintf("%+.1f", p / 1e6) : "?") }')"
	hl_t="Level=$hl%"; [ -n "$hl" ] || hl_t="no Battery Debug line in logcat's buffer"
	note battery "test_battery: capacity=$tb_cap status=$tb_st charge_now=$tb_qn charge_full=$tb_qf charge_counter=$tb_cc | Android: level=$A_LEVEL status=$(st_name "$A_STATUS") AC=$A_AC present=$A_PRESENT | health HAL: formula $hc %, log $hl_t"
	guard_state
	# (review-w10) a guard fault is never hidden: not by Android's own state (stopped / no level), not by another FAIL
	if [ -n "$G_BAD" ]; then case "$bad" in ''|stopped|noandroid) bad=$G_BAD;; *) bad="$bad; $G_BAD";; esac; fi
	hal_t="health HAL $hl %"; [ -n "$hl" ] || hal_t="health HAL (formula) $hc %"
	case "$bad" in
	'') verdict battery PASS "Android $A_LEVEL % = $hal_t = gb_power's composite $tb_cap % = the packs' $C_CAP % (percent units, $C_NBAT pack(s)); $G_TXT" ;;
	stopped) verdict battery UNKNOWN "Android's battery updates are stopped (an earlier 'dumpsys battery set/unplug'; 'dumpsys battery reset' restarts them)" ;;
	noandroid) verdict battery UNKNOWN "no level from 'dumpsys battery' (the packs hold $C_CAP %, gb_power $tb_cap %, $hal_t)" ;;
	*) verdict battery FAIL "$bad" ;;
	esac
}

# ------------------------------------------------------------------------------------------------- ac (charger)
adp() { psy_all; ADP=$(psy_field ADP1 4); TAC=$(psy_field test_ac 4); }
adp_off() { adp; [ "$ADP" = 0 ]; }
adp_on() { adp; [ "$ADP" = 1 ]; }
ac_watch() {        # $1 = expected ADP1 (0/1), $2 = seconds: AC_FLIPS AC_LAG ST_LAG ADP_FLIPS TAC_LAG of the samples
	_prev=$3; AC_FLIPS=0; ADP_FLIPS=0; AC_LAG=-; ST_LAG=-; TAC_LAG=-; _pa=$1
	upt; _t0=$UP; _k=0
	while :; do
		adp; and_bat; upt; _e=$((UP - _t0))
		[ "$ADP" != "$_pa" ] && ADP_FLIPS=$((ADP_FLIPS + 1)); _pa=$ADP
		[ "$A_AC" != "$_prev" ] && AC_FLIPS=$((AC_FLIPS + 1)); _prev=$A_AC
		if [ "$1" = 0 ]; then
			[ "$A_AC" = false ] && [ "$AC_LAG" = - ] && AC_LAG=$_e
			[ "$A_STATUS" = 3 ] && [ "$ST_LAG" = - ] && ST_LAG=$_e
			[ "$TAC" = 0 ] && [ "$TAC_LAG" = - ] && TAC_LAG=$_e
		else
			[ "$A_AC" = true ] && [ "$AC_LAG" = - ] && AC_LAG=$_e
			case "$A_STATUS" in 2|4|5) [ "$ST_LAG" = - ] && ST_LAG=$_e;; esac
			[ "$TAC" = 1 ] && [ "$TAC_LAG" = - ] && TAC_LAG=$_e
		fi
		_k=$((_k + 1))
		if [ "$_e" -ge "$2" ] || [ "$_k" -gt $(($2 * 2 + 5)) ]; then break; fi
		sleep 1
	done
}
ac_step() {         # $1 = unplug|plug: AC_RES = ok|fail text|skip text
	if [ "$1" = unplug ]; then want=0; cond=adp_off; was=true; txt="unplug the charger (the Surface Connect cable)"; else
		want=1; cond=adp_on; was=false; txt="plug the charger in again"; fi
	step_mark                         # (w10) before the prompt: only what the step changes is counted
	ask "$txt" || { AC_RES="skip"; return; }
	if ! waitfor "$cond" "$WAIT"; then AC_RES="skip: ADP1 did not change within $WAIT s"; return; fi
	ac_watch "$want" 20 "$was"
	note ac "$1 after $WAITED s: Android AC flag changes in 20 s: $AC_FLIPS, Android AC after ${AC_LAG} s, status after ${ST_LAG} s, test_ac after ${TAC_LAG} s, ADP1 changes: $ADP_FLIPS"
	step_count $((1 - want))
	note ac "$1: health HAL log: $SC_HN new 'Battery Debug' line(s), AC changes: $SC_HC (now AC=$SC_HL) | healthd (kernel log): $([ "$SC_KN" = - ] && printf 'not readable here' || printf "%s new line(s), charger changes: %s, 'battery none': %s" "$SC_KN" "$SC_KC" "$SC_KNONE")"
	f=
	if [ "$SC_HC" -gt 1 ]; then f="$f the health HAL's charger state changed $SC_HC times for one $1 (want 1: the charger animation shows more than once);"
	elif [ "$SC_HN" -gt 0 ] && [ "$SC_HL" != "$want" ]; then f="$f the health HAL's log still shows AC=$SC_HL after the $1;"; fi
	if [ "$SC_KN" != - ]; then
		[ "$SC_KC" -gt 1 ] && f="$f healthd's charger state changed $SC_KC times for one $1;"
		[ "$SC_KNONE" -gt 0 ] && f="$f healthd found no battery $SC_KNONE time(s) during the $1 (the 2nd boot's flicker);"
	fi
	_h=$SC_HC; [ "$SC_HN" -gt 0 ] || _h="no line"; AC_HCS="${AC_HCS:+$AC_HCS + }$_h"
	[ "$AC_LAG" = - ] || [ "$AC_LAG" -gt 10 ] && f="$f Android's AC flag did not follow within 10 s;"
	[ "$TAC_LAG" = - ] || [ "$TAC_LAG" -gt 10 ] && f="$f gb_power's test_ac did not follow within 10 s;"
	[ "$ST_LAG" = - ] || [ "$ST_LAG" -gt 15 ] && f="$f Android's battery status did not follow within 15 s;"
	if [ "$AC_FLIPS" -gt 1 ]; then
		if [ "$ADP_FLIPS" -gt 0 ]; then f="$f the plug itself bounced (ADP1 changed $ADP_FLIPS more times);"
		else f="$f Android's charger state flickered ($AC_FLIPS changes for one $1);"; fi
	fi
	AC_RES=${f:-ok}
}
ac_logs() {         # ALOG = the FAIL text for flaps in the HAL's log or the kernel log (empty: none) + one note
	hal_flaps; kmsg_none; ALOG=
	note ac "health HAL log: $HN 'Battery Debug' lines$([ "$HT" != - ] && printf ' since %s' "$HT"), AC flaps (on and off again within 1 s): $HF$([ "$HL" != - ] && printf ', last at %s' "$HL") | kernel log: $([ "$KN" = - ] && printf 'not readable here' || printf "healthd 'battery none' after it had a battery: %s" "$KN")"
	gt "$HF" 0 && ALOG="$ALOG Android's charger state flapped $HF time(s) (the health HAL's AC flag on and off again within 1 s, last at $HL: the charger animation flickers);"
	gt "$(num "$KN" 0)" 0 && ALOG="$ALOG the health HAL found no battery $KN time(s) after it had one (healthd 'battery none');"
	return 0
}
chk_ac() {
	adp; and_bat; AC_HCS=
	if [ "$ADP" = - ]; then verdict ac UNKNOWN "no charger supply ADP1 (Surface Aggregator)"; return; fi
	note ac "now: ADP1 online=$ADP test_ac online=$TAC Android AC powered=$A_AC status=$(st_name "$A_STATUS")"
	sbad=
	[ "$TAC" != - ] && [ "$TAC" != "$ADP" ] && sbad="$sbad gb_power's test_ac=$TAC but ADP1=$ADP;"
	case "$ADP:$A_AC" in 1:false|0:true) sbad="$sbad Android AC powered=$A_AC but ADP1=$ADP;";; esac
	if [ -n "$sbad" ]; then sleep 5; adp; and_bat; sbad=
		[ "$TAC" != - ] && [ "$TAC" != "$ADP" ] && sbad="$sbad gb_power's test_ac=$TAC but ADP1=$ADP;"
		case "$ADP:$A_AC" in 1:false|0:true) sbad="$sbad Android AC powered=$A_AC but ADP1=$ADP;";; esac
	fi
	if [ "$PROMPT" != 1 ]; then
		ac_logs; _x="${sbad}${ALOG}"
		if [ -n "$_x" ]; then verdict ac FAIL "${_x%;}"; else verdict ac UNKNOWN "charger state agrees (ADP1=$ADP), no flap in the logs; plug/unplug not tested (--no-prompt)"; fi
		return
	fi
	if [ "$ADP" = 1 ]; then s1=unplug; s2=plug; else s1=plug; s2=unplug; fi
	ac_step $s1; r1=$AC_RES
	if [ "$s1" = unplug ] && [ "$r1" = ok ] && want_item power; then pw_snapshot "on battery, right after the unplug"; fi
	r2=skip; case "$r1" in skip*) ;; *) ac_step $s2; r2=$AC_RES;; esac
	if [ "$s2" = unplug ] && [ "$r2" = ok ] && want_item power && [ "$PW_DONE" != 1 ]; then pw_snapshot "on battery, right after the unplug"; fi
	f=; case "$r1" in skip*|ok) ;; *) f="$s1:$r1";; esac; case "$r2" in skip*|ok) ;; *) f="$f $s2:$r2";; esac
	ac_logs; _x="${sbad}${f}${ALOG}"
	if [ -n "$_x" ]; then verdict ac FAIL "${_x%;}"
	elif [ "$r1" = ok ] && [ "$r2" = ok ]; then verdict ac PASS "unplug and plug both followed by gb_power and Android, one charger state change each (Android's AC flag; the health HAL's log: $AC_HCS), no flicker (no flap in the logs)"
	else verdict ac UNKNOWN "transition not done: $s1: $r1; $s2: $r2"; fi
}

# ------------------------------------------------------------------------------------------------- wifi / bt
chk_wifi() {
	ifc=; drv=-; ops=-; car=-
	for x in "$R"/sys/class/net/*; do
		[ -d "$x/wireless" ] || [ -e "$x/phy80211" ] || continue
		ifc=$(tok "${x##*/}"); drv=$(tok "$(lnk "$x/device/driver")"); rd "$x/operstate" && ops=$(tok "$V"); rd "$x/carrier" && car=$(tok "$V")
		break
	done
	rk=-; for k in "$R"/sys/class/rfkill/rfkill*; do rd "$k/type"; [ "$V" = wlan ] || continue; rd "$k/soft"; s=$V; rd "$k/hard"; rk="soft=$s hard=$V"; done
	ws=$(T cmd wifi status)
	en=-; case "$ws" in *"Wifi is enabled"*) en=yes;; *"Wifi is disabled"*) en=no;; esac
	co=-; case "$ws" in *"Wifi is connected to"*) co=yes;; *"Wifi is not connected"*|*"Wifi is disconnected"*) co=no;; esac
	[ "$co" = - ] && [ "$en" != - ] && co=no
	std=$(printf '%s\n' "$ws" | sed -n 's/.*Wi-Fi standard: \([0-9a-z]*\).*/\1/p' | head -n 1)
	rssi=$(printf '%s\n' "$ws" | sed -n 's/.*RSSI: \(-[0-9]*\).*/\1/p' | head -n 1)
	lnks=$(printf '%s\n' "$ws" | sed -n 's/.*Link speed: \([0-9]*\)Mbps.*/\1/p' | head -n 1)
	frq=$(printf '%s\n' "$ws" | sed -n 's/.*Frequency: \([0-9]*\)MHz.*/\1/p' | head -n 1)
	ns=$(T cmd wifi list-scan-results | grep -c '^ *[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:')
	lvl=$(awk 'NR > 2 { gsub(/\./, "", $4); print $4; exit }' "$R/proc/net/wireless" 2>/dev/null)
	note wifi "interface=${ifc:-none} driver=$drv operstate=$ops carrier=$car rfkill $rk | enabled=$en connected=$co standard=$(tok "$std") rssi=$(num "$rssi") dBm link=$(num "$lnks") Mbps freq=$(num "$frq") MHz | scan results=$(num "$ns" 0) | /proc/net/wireless level=$(num "$lvl")"
	if [ -z "$ifc" ]; then verdict wifi FAIL "no wireless network interface (iwlwifi did not register wlan0)"; return; fi
	if [ "$drv" != iwlwifi ]; then verdict wifi FAIL "$ifc is driven by $drv, not iwlwifi"; return; fi
	case "$rk" in *hard=1*) verdict wifi FAIL "the Wi-Fi radio is hard-blocked (rfkill)"; return;; esac
	if [ "$en" = no ]; then verdict wifi UNKNOWN "Wi-Fi is switched off in Android (turn it on in Quick Settings and run again)"; return; fi
	if [ "$co" = yes ] || [ "$car" = 1 ]; then verdict wifi PASS "$ifc (iwlwifi) connected${std:+, $(tok "$std")}${rssi:+, $rssi dBm}"; return; fi
	if gt "$(num "$ns" 0)" 0; then verdict wifi PASS "$ifc (iwlwifi) scans ($ns networks seen), not connected"; return; fi
	if [ "$en" = - ]; then verdict wifi UNKNOWN "$ifc (iwlwifi) present, Android's Wi-Fi state not readable (cmd wifi status)"; return; fi
	verdict wifi FAIL "$ifc (iwlwifi) enabled but not connected and no scan result"
}
chk_bt() {
	hci=0; [ -e "$R/sys/class/bluetooth/hci0" ] && hci=1
	rk=-; for k in "$R"/sys/class/rfkill/rfkill*; do rd "$k/type"; [ "$V" = bluetooth ] || continue; rd "$k/soft"; s=$V; rd "$k/hard"; rk="soft=$s hard=$V"; done
	on=$(tok "$(T settings get global bluetooth_on | head -n 1)")
	set -- $(T dumpsys bluetooth_manager | awk '/^  enabled: / && e == "" { e = $2 } /^  state: / && s == "" { s = $2 } END { printf "%s %s\n", (e == "" ? "-" : e), (s == "" ? "-" : s) }')
	be=$(tok "${1:-}"); bs=$(tok "${2:-}")
	note bt "hci0=$hci rfkill $rk bluetooth_on=$on manager: enabled=$be state=$bs"
	if [ "$hci" != 1 ]; then verdict bt FAIL "no Bluetooth controller hci0 (btintel firmware?)"; return; fi
	case "$rk" in *hard=1*) verdict bt FAIL "the Bluetooth radio is hard-blocked (rfkill)"; return;; esac
	if [ "$bs" = ON ] || { [ "$bs" = - ] && [ "$on" = 1 ]; }; then verdict bt PASS "hci0 present, Bluetooth on"; return; fi
	if [ "$on" = 0 ] || [ "$bs" = OFF ] || [ "$be" = false ]; then verdict bt UNKNOWN "Bluetooth is switched off in Android (turn it on and run again)"; return; fi
	verdict bt UNKNOWN "hci0 present, Android's Bluetooth state not readable (state=$bs)"
}

# ------------------------------------------------------------------------------------------------- audio
chk_audio() {
	cards=$(awk '/^ *[0-9]+ \[/ { s = $0; sub(/^ *[0-9]+ \[/, "", s); sub(/ *\].*$/, "", s); n = $1; printf "%s=%s ", n, s }' "$R/proc/asound/cards" 2>/dev/null)
	id=; cn=
	for c in $cards; do case "${c#*=}" in sofhdadsp|PCH) id=${c#*=}; cn=${c%%=*}; break;; esac; done
	pb=0; [ -n "$cn" ] && for p in "$R"/dev/snd/pcmC${cn}D*p; do [ -e "$p" ] && pb=$((pb + 1)); done
	hal=$(prop init.svc.vendor.audio-hal-aidl); as=$(prop init.svc.audioserver)
	note audio "cards: $(tok "$(printf '%s' "$cards" | tr ' ' ',')") | playback PCMs on card ${cn:--}: $pb | audio HAL=$(tok "$hal") audioserver=$(tok "$as")"
	if [ -z "$id" ]; then verdict audio FAIL "no SOF card 'sofhdadsp' (nor the legacy HDA 'PCH') in /proc/asound/cards"; return; fi
	if [ "$pb" = 0 ]; then verdict audio FAIL "card $id has no playback PCM device"; return; fi
	if [ "$hal" != running ] || [ "$as" != running ]; then verdict audio FAIL "card $id present but the audio HAL ($(tok "$hal")) or audioserver ($(tok "$as")) is not running"; return; fi
	verdict audio PASS "ALSA card $id (card $cn, $pb playback PCMs), audio HAL and audioserver running"
}

# ------------------------------------------------------------------------------------------------- cameras
chk_cameras() {
	ipu=-; [ -e "$PCI/0000:00:05.0" ] && ipu=$(tok "$(lnk "$PCI/0000:00:05.0/driver")" none)
	f=$(tok "$(lnk "$R/sys/bus/i2c/devices/i2c-INT33BE:00/driver")" none)
	r=$(tok "$(lnk "$R/sys/bus/i2c/devices/i2c-INT347A:00/driver")" none)
	v=$(tok "$(lnk "$R/sys/bus/i2c/devices/i2c-INT347A:00-VCM/driver")" none)
	# (review) the focus motor's runtime-PM state, readable as the shell user: 'error' is the state the 2nd boot's
	# dw9719 '-121' park write leaves (power.runtime_error set); vendor.gb.vcm = wave 10's gb_hw vcm result
	vrs=-; rd "$R/sys/bus/i2c/devices/i2c-INT347A:00-VCM/power/runtime_status" && vrs=$(tok "$V")
	vg=$(prop vendor.gb.vcm); vg=$(tok "${vg%% *}")
	ir=$(tok "$(lnk "$R/sys/bus/i2c/devices/i2c-INT347E:00/driver")" none)
	nv=0; for x in "$R"/sys/class/video4linux/*; do [ -e "$x" ] && nv=$((nv + 1)); done
	set -- $(T dumpsys media.camera | awk '/^Number of camera devices: / && n == "" { n = $5 } /^Number of normal camera devices: / && m == "" { m = $6 } END { printf "%s %s\n", (n == "" ? "-" : n), (m == "" ? "-" : m) }')
	nc=$(num "${1:-}"); nn=$(num "${2:-}")
	pv=$(prop init.svc.vendor.camera-provider-2-7-legacy-wrapper)
	ke=-; k=$(dmesg 2>/dev/null) && ke=$(printf '%s\n' "$k" | grep -E '(ov5693|ov8865|dw9719)' | grep -v -e '-517' | grep -c -E 'rror|failed|-121')
	note cameras "IPU 00:05.0 driver=$ipu | front INT33BE=$f rear INT347A=$r focus VCM=$v (runtime $vrs, vendor.gb.vcm=$vg) IR INT347E=$ir (not loaded on purpose) | video4linux nodes=$nv | Android cameras=$nc (normal $nn) provider=$(tok "$pv") | kernel sensor errors=$ke"
	if [ "$ipu" != intel-ipu6 ]; then verdict cameras FAIL "the IPU (00:05.0) is not bound to intel-ipu6 (driver=$ipu)"; return; fi
	b=
	[ "$f" = none ] && b="$b front sensor (INT33BE) has no driver;"; [ "$r" = none ] && b="$b rear sensor (INT347A) has no driver;"
	[ "$v" = none ] && b="$b rear focus motor (dw9719) has no driver;"
	[ "$vrs" = error ] && b="$b the rear focus motor (dw9719) is in the runtime-PM error state (its lens-park write failed, as the -121 of the 2nd boot: focus control fails and a suspend may abort);"
	case "$vg" in FAILED*) b="$b gb_hw vcm reported FAILED (vendor.gb.vcm);";; esac
	[ "$nc" = 0 ] && b="$b Android lists 0 cameras;"
	gt "$(num "$ke" 0)" 0 && b="$b the kernel logged $ke sensor/focus errors (e.g. dw9719 -121);"
	if [ -n "$b" ]; then verdict cameras FAIL "${b%;}"; return; fi
	if [ "$nc" = - ]; then verdict cameras UNKNOWN "sensors bound ($f, $r, $v), Android's camera list not readable (dumpsys media.camera)"; return; fi
	kn=; [ "$ke" = - ] && kn="; kernel log not readable here (sensor errors not checked; the focus motor's runtime-PM state was)"
	verdict cameras PASS "IPU bound, front $f, rear $r + $v, Android lists $nc cameras$kn"
}

# ------------------------------------------------------------------------------------------------- dgpu
dgpu_find() {       # DG = the first NVIDIA VGA/3D function (PCI address) or empty; NVF = every NVIDIA function
	DG=; NVF=
	for x in "$PCI"/*; do
		rd "$x/vendor"; [ "$V" = 0x10de ] || continue
		NVF="$NVF ${x##*/}"; rd "$x/class"; case "$V" in 0x03*) [ -n "$DG" ] || DG=${x##*/};; esac
	done
}
dg_state() { rd "$PCI/$DG/power_state"; DPS=$(tok "$V"); rd "$PCI/$DG/power/runtime_status"; DRS=$(tok "$V"); }
dg_cold() { dg_state; [ "$DPS" = D3cold ]; }
chk_dgpu() {
	base=$(prop vendor.gb.dtx.base); dgpu_find
	if [ -z "$DG" ]; then
		if [ "$base" = detached ]; then verdict dgpu UNKNOWN "the keyboard base (with the dGPU) is detached"
		else verdict dgpu FAIL "no NVIDIA function on the PCI bus with the base attached"; fi
		return
	fi
	dg_state; drv=$(tok "$(lnk "$PCI/$DG/driver")" none)
	note dgpu "$DG driver=$drv power_state=$DPS runtime=$DRS | vendor.gb.dgpu.state=$(tok "$(prop vendor.gb.dgpu.state)") holders=$(tok "$(holders)") nouveau.result=$(tok "$(prop vendor.gb.nouveau.result)")"
	w=60; [ "$QUICK" = 1 ] && w=10
	if waitfor dg_cold $w; then verdict dgpu PASS "$DG in D3cold (runtime $DRS)$([ "$WAITED" -gt 0 ] && printf ' after %s s' "$WAITED")"; return; fi
	note dgpu "after $w s: power_state=$DPS runtime=$DRS vendor.gb.dgpu.state=$(tok "$(prop vendor.gb.dgpu.state)") holders=$(tok "$(holders)")"
	if [ "$DRS" = error ]; then verdict dgpu FAIL "$DG runtime PM error (runtime_status=error)"; return; fi
	verdict dgpu FAIL "$DG stayed $DPS (runtime $DRS) for $w s: the dGPU is not powered off while idle (holders: $(tok "$(holders)"))"
}

# ------------------------------------------------------------------------------------------------- dtx / posture / rotation
ds_now() { DS=$(T dumpsys device_state | grep 'mCommittedState=' | head -n 1 | sed -n "s/.*identifier=\([0-9]*\), name='\([A-Za-z_]*\)'.*/\2/p"); DS=$(tok "$DS"); }
dtx_props() { DB=$(tok "$(prop vendor.gb.dtx.base)"); DM=$(tok "$(prop vendor.gb.dtx.mode)"); DL=$(tok "$(prop vendor.gb.dtx.latch)"); }
is_detached() { dtx_props; [ "$DB" = detached ]; }
is_attached() { dtx_props; [ "$DB" = attached ]; }
lat_open() { dtx_props; [ "$DL" = opened ] || [ "$DB" = detached ]; }
gone_all() {        # tablet: keyboard, touchpad, BAT2 and every NVIDIA function gone, device state TABLET
	! inname "$KBD" && ! inname "$TPD" && [ ! -e "$PS/BAT2" ] && { dgpu_find; [ -z "$NVF" ]; } &&
		{ ds_now; [ "$DS" = TABLET ]; }
}
back_all() {        # laptop again: keyboard, touchpad, BAT2, an NVIDIA function, device state OPEN, latch closed
	inname "$KBD" && inname "$TPD" && [ -e "$PS/BAT2" ] && { dgpu_find; [ -n "$DG" ]; } &&
		{ ds_now; [ "$DS" = OPEN ]; } && { dtx_props; [ "$DL" = closed ]; }
}
dtx_report() {      # $1 = label: one note with what is there now
	ds_now; dtx_props; dgpu_find
	note dtx "$1: base=$DB mode=$DM latch=$DL device_state=$DS keyboard=$(inp "$KBD") touchpad=$(inp "$TPD") BAT2=$([ -e "$PS/BAT2" ] && echo 1 || echo 0) nvidia_functions=$(set -- $NVF; echo $#)"
}
DTX_DONE=0; DET_DS=-; ATT_DS=-; ROT_TURN=-
chk_dtx() {
	dtx_props; gp=$(prop init.svc.gb_power)
	node=0; [ -e "$R/sys/class/misc/surface_dtx" ] && node=1
	din=$(inp "$DTXIN")
	note dtx "gb_power=$(tok "$gp") /sys/class/misc/surface_dtx=$node mode-switch input=$din | base=$DB mode=$DM latch=$DL"
	sbad=
	[ "$gp" = running ] || sbad="$sbad gb_power is not running;"
	[ "$node" = 1 ] || sbad="$sbad no surface_dtx device;"
	case "$DB" in attached|detached) ;; *) sbad="$sbad vendor.gb.dtx.base=$DB (gb_power has no DTX state);";; esac
	if [ -n "$sbad" ]; then verdict dtx FAIL "${sbad%;}"; return; fi
	if [ "$PROMPT" != 1 ]; then verdict dtx PASS "DTX ready: base $DB, mode $DM, latch $DL (detach/attach not tested: --no-prompt)"; return; fi
	f=
	if [ "$DB" = detached ]; then
		ask "attach the screen to the keyboard base (it clicks into the latch)" || true
		waitfor is_attached "$WAIT" || { verdict dtx UNKNOWN "the screen was not attached within $WAIT s"; return; }
		waitfor back_all 30 || true
	fi
	dtx_report before
	ask "press the detach key (top row, right) and lift the screen off the keyboard base when it lets go" || true
	if ! waitfor is_detached "$WAIT"; then
		dtx_report "after $WAIT s"
		if [ "$DL" != opened ]; then verdict dtx UNKNOWN "not detached within $WAIT s and the latch never opened (if the detach key was pressed: gb_power refuses while apps hold the NVIDIA GPU: holders $(tok "$(holders)"))"
		else verdict dtx UNKNOWN "the latch opened but the screen was not lifted within $WAIT s"; fi
		return
	fi
	note dtx "detached after $WAITED s"
	waitfor gone_all 20 || f="$f detached but not everything left within 20 s;"
	dtx_report detached
	DET_DS=$DS; [ "$DM" = tablet ] || f="$f mode $DM (want tablet);"
	if want_item rotation; then rot_turn; fi
	ask "attach the screen to the keyboard base again (it clicks into the latch)" || true
	if ! waitfor is_attached "$WAIT"; then verdict dtx UNKNOWN "detach OK, but not attached again within $WAIT s${f:+ (detach:$f)}"; DTX_DONE=1; return; fi
	note dtx "attached after $WAITED s"
	waitfor back_all 30 || f="$f attached but not everything came back within 30 s;"
	dtx_report attached
	ATT_DS=$DS; [ "$DM" = laptop ] || f="$f mode $DM (want laptop);"
	DTX_DONE=1
	if [ -n "$f" ]; then verdict dtx FAIL "${f%;}"; else verdict dtx PASS "detach and attach: keyboard, touchpad, BAT2 and the dGPU left and came back, device state TABLET then OPEN"; fi
}
chk_posture() {
	ds_now; dtx_props
	ov=$(T dumpsys device_state | grep 'mOverrideState=' | head -n 1 | sed -n "s/.*name='\([A-Za-z_]*\)'.*/\1/p")
	pv=$(prop vendor.gb.posture); ppv=$(prop persist.vendor.gb.posture)
	set -- $(inp_state); ke=$(tok "${1:-}"); te=$(tok "${2:-}")
	note posture "device_state=$DS override=$(tok "$ov" none) vendor.gb.posture=$(tok "$pv") persist.vendor.gb.posture=$(tok "$ppv") dtx mode=$DM | built-in keyboard enabled=$ke touchpad enabled=$te | detached: $DET_DS attached again: $ATT_DS"
	if [ "$DS" = - ] || [ "$DS" = '?' ]; then verdict posture UNKNOWN "no device state from 'dumpsys device_state'"; return; fi
	b=
	case "$DM:$DS" in
	laptop:OPEN|tablet:TABLET|studio:*) ;;
	laptop:CLOSED) b="$b device state CLOSED with the lid open in laptop mode (the built-in input is off; recovery: GB_PROP_1=vendor.gb.posture=open);" ;;
	laptop:*|tablet:*) b="$b device state $DS does not match the DTX mode $DM;" ;;
	esac
	[ "$DM" = laptop ] && { [ "$ke" = false ] || [ "$te" = false ]; } && b="$b Android disabled the built-in keyboard/touchpad in laptop mode;"
	if [ "$DTX_DONE" = 1 ]; then
		[ "$DET_DS" = TABLET ] || b="$b device state $DET_DS with the screen detached (want TABLET);"
		[ "$ATT_DS" = - ] || [ "$ATT_DS" = OPEN ] || b="$b device state $ATT_DS after attaching (want OPEN);"
	fi
	if [ -n "$b" ]; then verdict posture FAIL "${b%;}"; return; fi
	if [ "$DTX_DONE" = 1 ]; then verdict posture PASS "OPEN in laptop mode, TABLET detached${ov:+ (override $(tok "$ov"))}"
	else verdict posture PASS "$DS in DTX mode $DM, built-in input enabled (tablet posture not tested: run with dtx)"; fi
}
# rot_val, rot_display0 and rot_verdict: byte for byte the functions of C:\GB-build\native\sensors\gb_sensors.sh
# (readiness A-L3; the drift test tests\test_drift.py compares them). They only read 'dumpsys window displays'.
rot_val() {         # $1 = value, $2 = text for an empty value (default ?): $1 if it is one plain token, else ?
	case "$1" in '') echo "${2:-?}";; *[!A-Za-z0-9_.:-]*) echo '?';; *) echo "$1";; esac
}
rot_display0() {    # "support_auto fixed_to_user user_rotation_mode user_rotation rotation" of display 0 (? = not found)
	timeout 20 dumpsys window displays 2>/dev/null | awk '
		/Display: mDisplayId=/ { d = $0; sub(/.*Display: mDisplayId=/, "", d); sub(/[^0-9].*$/, "", d); on = (d == "0"); dr = 0; next }
		on && /^ *DisplayRotation *$/ { dr = 1; next }
		dr && /RotationHistory|RotationLockHistory|FoldController/ { dr = 0 }
		dr && /^ *mRotation=[0-9]/ { s = $0; sub(/^ *mRotation=/, "", s); sub(/[^0-9].*$/, "", s); rot = s }
		dr && /^ *mSupportAutoRotation=/ { s = $0; sub(/^ *mSupportAutoRotation=/, "", s); sub(/[^a-z].*$/, "", s); sup = s }
		dr && /^ *mFixedToUserRotation=/ { s = $0; sub(/^ *mFixedToUserRotation=/, "", s); sub(/[^a-z].*$/, "", s); fix = s }
		dr && /^ *mUserRotationMode=/ {
			s = $0; sub(/^ *mUserRotationMode=/, "", s); m = s; sub(/[^A-Z_].*$/, "", m); mode = m
			if (match(s, / mUserRotation=[A-Z_0-9]+/)) urot = substr(s, RSTART + 15, RLENGTH - 15)
		}
		END {
			printf "%s %s %s %s %s\n", (sup == "" ? "?" : sup), (fix == "" ? "?" : fix), (mode == "" ? "?" : mode),
				(urot == "" ? "?" : urot), (rot == "" ? "?" : rot)
		}'
}
rot_verdict() {     # $1 device state, $2 support_auto, $3 fixed_to_user, $4 user_rotation_mode, $5 DTX mode
	case "$2" in
	true)
		case "$1:$3" in
		OPEN:true) v="ok: laptop posture, rotation locked (lapis LaptopController)" ;;
		OPEN:false) v="MISMATCH: laptop posture but the display follows the accelerometer (readiness A-L3)" ;;
		TABLET:false)
			case "$4" in
			USER_ROTATION_FREE) v="ok: tablet posture, auto-rotate on" ;;
			USER_ROTATION_LOCKED) v="ok: tablet posture, auto-rotate off by the user's Auto-rotate setting" ;;
			*) v="ok: tablet posture, the display follows the user's Auto-rotate setting" ;;
			esac ;;
		TABLET:true) v="MISMATCH: tablet posture but the display is fixed to the user rotation (no auto-rotate)" ;;
		*) v="info: no rotation expectation for device state $1" ;;
		esac ;;
	false) v="WARN: config_supportAutoRotation is off (GbSb3SensorsOverlay not applied? cmd overlay list): no auto-rotate in any posture" ;;
	*) v="info: no DisplayRotation data (dumpsys window displays)" ;;
	esac
	case "$5:$1" in
	laptop:TABLET|tablet:OPEN|studio:OPEN) v="$v; note: DTX mode $5 disagrees with the device state $1" ;;
	esac
	echo "$v"
}
rot_now() {         # RV = the verdict now, RR = display 0's rotation, RS = "sup fix mode urot rot"
	ds_now; dtx_props
	set -f; set -- $(rot_display0); set +f
	RS="support_auto=$(rot_val "${1:-}") fixed_to_user=$(rot_val "${2:-}") user_rotation_mode=$(rot_val "${3:-}") rotation=$(rot_val "${5:-}")"
	RR=$(rot_val "${5:-}"); RV=$(rot_verdict "$DS" "$(rot_val "${1:-}")" "$(rot_val "${2:-}")" "$(rot_val "${3:-}")" "$DM")
}
rot_moved() { rot_now; [ "$RR" != "$R0" ]; }
rot_turn() {        # in tablet posture: ask for a quarter turn, ROT_TURN = ok | none | off | mismatch | skip | error text
	rot_now; note rotation "tablet: device_state=$DS $RS => $RV"
	case "$RV" in
	"ok: tablet posture, auto-rotate on"*) ;;
	"ok: tablet posture"*) ROT_TURN=off; return ;;
	MISMATCH*|WARN*) ROT_TURN=mismatch; ROT_TV=$RV; return ;;
	*) ROT_TURN="skip"; return ;;
	esac
	R0=$RR
	ask "turn the screen a quarter turn to portrait and hold it still" || { ROT_TURN=skip; return; }
	if waitfor rot_moved 60; then ROT_TURN=ok; note rotation "the display rotated from $R0 to $RR after $WAITED s"; else ROT_TURN=none; fi
}
chk_rotation() {
	acc=0; for x in "$R"/sys/bus/iio/devices/iio:device*/name; do rd "$x"; case "$V" in cros-ec-accel|accel_3d*) acc=1;; esac; done
	sa=$(T dumpsys sensorservice | grep -c 'android.sensor.accelerometer')
	ar=$(tok "$(T settings get system accelerometer_rotation | head -n 1)")
	rot_now
	note rotation "accelerometer IIO=$acc sensorservice accelerometer lines=$(num "$sa" 0) accelerometer_rotation=$ar | now: device_state=$DS dtx=$DM $RS => $RV | tablet turn: $ROT_TURN"
	if [ "$acc" != 1 ]; then verdict rotation FAIL "no ISH accelerometer (cros-ec-accel) in /sys/bus/iio"; return; fi
	case "$RV" in MISMATCH*|WARN*) verdict rotation FAIL "$RV"; return;; esac
	case "$ROT_TURN" in
	ok) verdict rotation PASS "$RV; detached, the display followed a quarter turn" ;;
	none) verdict rotation FAIL "tablet posture with auto-rotate on, but the display did not rotate within 60 s of a quarter turn" ;;
	mismatch) verdict rotation FAIL "with the screen detached: $ROT_TV" ;;
	off) verdict rotation UNKNOWN "$RV; detached, Auto-rotate is off in this user's settings (Quick Settings > Auto-rotate), no turn test" ;;
	*) case "$RV" in ok:*) verdict rotation PASS "$RV (tablet turn not tested: run with dtx)";; *) verdict rotation UNKNOWN "$RV";; esac ;;
	esac
}

# ------------------------------------------------------------------------------------------------- suspend / s0ix
sus_read() {        # S_* from /sys/power and the LPIT residency counters
	ss=$R/sys/power/suspend_stats
	rd "$ss/success"; S_OK=$(num "$V"); rd "$ss/fail"; S_FAIL=$(num "$V")
	rd "$ss/last_hw_sleep"; S_LHW=$(num "$V"); rd "$ss/total_hw_sleep"; S_THW=$(num "$V")
	rd "$ss/last_failed_dev"; S_LDEV=$(tok "$V"); rd "$ss/last_failed_errno"; S_LERR=$(num "$V"); rd "$ss/last_failed_step"; S_LSTEP=$(tok "$V")
	rd "$R/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us"; S_LPS=$(num "$V")
	rd "$R/sys/devices/system/cpu/cpuidle/low_power_idle_cpu_residency_us"; S_LPC=$(num "$V")
	rd "$R/sys/power/mem_sleep"; S_MEM=$(tok "$(printf '%s' "$V" | tr -d '[]' | tr ' ' ',')")
}
sus_more() { sus_read; gt "$S_OK" "$O_OK"; }
wake_top() {        # the 4 wakeup sources whose active time grew most between $1 and $2 (name:ms), plain names only
	printf '%s\n--\n%s\n' "$1" "$2" | awk '$0 == "--" { b = 1; next } !b { a[$1] = $2; next } { d = $2 - a[$1]; if (d > 0) print d, $1 }' |
		sort -rn | head -n 4 | awk '{ printf "%s:%d ", $2, $1 }'
}
wake_snap() {       # "name active_time_ms" of every /sys/class/wakeup source with a plain name
	for x in "$R"/sys/class/wakeup/wakeup*; do
		rd "$x/name" || continue; n=$V; case "$n" in ''|*[!A-Za-z0-9_.:-]*) continue;; esac
		rd "$x/active_time_ms"; printf '%s %s\n' "$n" "$(num "$V" 0)"
	done
}
chk_suspend() {
	sus_read; O_OK=$S_OK; O_FAIL=$S_FAIL; O_THW=$S_THW; O_LPS=$S_LPS
	note suspend "mem_sleep=$S_MEM since boot: success=$S_OK fail=$S_FAIL last failure: dev=$S_LDEV errno=$S_LERR step=$S_LSTEP | hw sleep last=$S_LHW us total=$S_THW us | LPIT residency system=$S_LPS us cpu=$S_LPC us"
	did=0
	if ask "close the lid, wait about 60 s, then open it again (if this adb session ends meanwhile: reconnect and run 'gb_hwcheck.sh suspend')"; then
		w0=$(wake_snap)
		trap '' HUP
		ww=$((WAIT + 60))          # 180 s with the default wait
		if waitfor sus_more "$ww"; then
			did=1; sleep 3; sus_read
			note suspend "suspended and resumed: success $O_OK -> $S_OK, fail $O_FAIL -> $S_FAIL, hw sleep last=$S_LHW us total $O_THW -> $S_THW us, LPIT system $O_LPS -> $S_LPS us"
		else
			sus_read
			note suspend "no suspend within $ww s (success still $S_OK); wakeup sources most active meanwhile: $(wake_top "$w0" "$(wake_snap)") | wake locks held: $(T dumpsys power | sed -n 's/^ *Wake Locks: size=\([0-9]*\).*/\1/p' | head -n 1)"
		fi
		trap - HUP
		# (review) k16's pm_suspend() counts suspend_stats/fail for EVERY failed attempt (kernel/power/suspend.c
		# 658-663), also one a wakeup event or an alarm due within 2 s (alarmtimer -EBUSY) aborts; Android retries.
		# A failed attempt next to a completed suspend is a note, not a FAIL.
		if [ "$did" = 1 ]; then
			if gt "$S_FAIL" "$O_FAIL"; then verdict suspend PASS "the lid close suspended (s2idle) and the laptop resumed (success $O_OK -> $S_OK); $(awk -v a="$S_FAIL" -v b="$O_FAIL" 'BEGIN { print a - b }') attempt(s) aborted meanwhile (last: $S_LDEV step $S_LSTEP errno $S_LERR; a wakeup event or an alarm due within 2 s aborts one, Android retries)"
			else verdict suspend PASS "the lid close suspended (s2idle) and the laptop resumed (success $O_OK -> $S_OK)"; fi
		elif gt "$S_FAIL" "$O_FAIL"; then verdict suspend FAIL "suspend attempts failed and none completed within $ww s (fail $O_FAIL -> $S_FAIL; last: $S_LDEV step $S_LSTEP errno $S_LERR)"
		else verdict suspend UNKNOWN "no suspend happened within $ww s (lid not closed, or Android kept the system awake: see the wakeup sources)"; fi
	else
		if gt "$S_OK" 0; then
			if gt "$S_FAIL" 0; then verdict suspend PASS "$S_OK suspend/resume cycle(s) since boot; $S_FAIL attempt(s) aborted (last: $S_LDEV step $S_LSTEP errno $S_LERR; a wakeup event or an alarm due within 2 s aborts one, Android retries)"
			else verdict suspend PASS "$S_OK suspend/resume cycle(s) since boot, none failed"; fi
		elif gt "$S_FAIL" 0; then verdict suspend FAIL "suspend failed $S_FAIL time(s) since boot (last: $S_LDEV step $S_LSTEP errno $S_LERR) and none completed"
		else verdict suspend UNKNOWN "no suspend since boot (close the lid once, then run 'gb_hwcheck.sh suspend')"; fi
	fi
	# s0ix: did the platform reach its low-power state while suspended (hardware sleep time / LPIT system residency)?
	if [ "$did" = 1 ]; then
		if gt "$S_LHW" 0 || gt "$S_THW" "$O_THW" || gt "$S_LPS" "$O_LPS"; then verdict s0ix PASS "the platform reached S0ix while suspended (hw sleep last $S_LHW us)"
		elif [ "$S_LHW$S_THW$S_LPS" = --- ]; then verdict s0ix UNKNOWN "no hardware sleep counter here (suspend_stats/last_hw_sleep, LPIT)"
		else verdict s0ix FAIL "suspended, but no hardware sleep time (last_hw_sleep=$S_LHW, LPIT system residency unchanged): the battery drains while the lid is closed"; fi
	elif gt "$S_OK" 0; then
		if gt "$S_THW" 0 || gt "$S_LPS" 0; then verdict s0ix PASS "hardware sleep time since boot: total $S_THW us, LPIT system $S_LPS us"
		elif [ "$S_THW$S_LPS" = -- ]; then verdict s0ix UNKNOWN "no hardware sleep counter here (suspend_stats/total_hw_sleep, LPIT)"
		else verdict s0ix FAIL "$S_OK suspend(s) since boot but no hardware sleep time (total_hw_sleep=$S_THW, LPIT system=$S_LPS)"; fi
	else
		verdict s0ix UNKNOWN "no suspend to judge"
	fi
}

# ------------------------------------------------------------------------------------------------- power snapshot
cpu_ticks() { awk '/^cpu / { t = 0; for (i = 2; i <= NF; i++) t += $i; print t, $5 + $6; exit }' "$R/proc/stat" 2>/dev/null; }
deep_us() {         # the summed residency (us) of every CPU's deepest idle state, and how many CPUs
	for c in "$R"/sys/devices/system/cpu/cpu[0-9]*; do
		l=; for s in "$c"/cpuidle/state*; do [ -r "$s/time" ] && l=$s; done
		[ -n "$l" ] && { rd "$l/time"; printf '%s\n' "$(num "$V" 0)"; }
	done | awk '{ t += $1; n++ } END { printf "%.0f %d\n", t, n }'
}
rapl_uj() { [ -r "$R/sys/class/powercap/$1/energy_uj" ] && { rd "$R/sys/class/powercap/$1/energy_uj"; num "$V"; } || printf -; }
proc_snap() {       # "pid comm ticks" of every process (comm reduced to a plain name, else '.')
	cat "$R"/proc/[0-9]*/stat 2>/dev/null | awk '{
		i = index($0, " ("); j = 0; for (k = length($0); k > 0; k--) if (substr($0, k, 1) == ")") { j = k; break }
		if (i == 0 || j == 0) next
		pid = substr($0, 1, i - 1); c = substr($0, i + 2, j - i - 2); rest = substr($0, j + 2)
		n = split(rest, f, " "); if (n < 13) next
		if (c !~ /^[A-Za-z0-9_:\/+-]+$/) c = "."
		print pid, c, f[12] + f[13] }'
}
proc_top() {        # $1, $2 = proc_snap before/after: the top 5 by CPU ticks (apps and odd names summed as 'apps')
	printf '%s\n--\n%s\n' "$1" "$2" | awk '$0 == "--" { b = 1; next } !b { a[$1] = $3; next }
		{ d = $3 - (($1 in a) ? a[$1] : $3); if (d <= 0) next; k = ($2 == "." ? "apps" : $2); s[k] += d }
		END { for (k in s) print s[k], k }' | sort -rn | head -n 5 | awk '{ printf "%s:%d ", $2, $1 }'
}
PW_DONE=0; PW_LABEL=; PW_W=-; PW_AC=-; PW_BUSY=-; PW_DEX=-
pw_snapshot() {     # $1 = label; a sampling window of 30 s (quick: 10 s); PW_* for the verdict
	lab=$1; win=30; [ "$QUICK" = 1 ] && win=10
	emit "note: measuring the power draw for $win s ($1): leave the laptop alone"
	psy_all; combo; ac0=$C_AC
	upt; u0=$UP; c0=$(cpu_ticks); d0=$(deep_us); p0=$(proc_snap); e0=$(rapl_uj intel-rapl:0); y0=$(rapl_uj intel-rapl:1)
	sus_read; l0=$S_LPC
	ws=$C_PNET; nw=1; k=0; pk=$C_PK      # (review) pk: every sample had every pack's rate (else no draw figure)
	while [ $k -lt "$win" ]; do sleep 5; k=$((k + 5)); psy_all; combo; [ "$C_PK" = 1 ] || pk=0; ws=$(awk -v a="$ws" -v b="$C_PNET" 'BEGIN { printf "%.0f", a + b }'); nw=$((nw + 1)); done
	upt; u1=$UP; c1=$(cpu_ticks); d1=$(deep_us); p1=$(proc_snap); e1=$(rapl_uj intel-rapl:0); y1=$(rapl_uj intel-rapl:1)
	sus_read; dt=$((u1 - u0)); [ "$dt" -gt 0 ] || dt=$win
	PW_W=$(awk -v s="$ws" -v n="$nw" 'BEGIN { printf "%.1f", s / n / 1e6 }'); PW_AC=$C_AC; [ "$ac0" = 1 ] && PW_AC=1
	[ "$pk" = 1 ] || PW_W=-
	set -- $c0 $c1; PW_BUSY=$(awk -v t0="${1:-0}" -v i0="${2:-0}" -v t1="${3:-0}" -v i1="${4:-0}" 'BEGIN { d = t1 - t0; printf "%d", (d > 0 ? 100 * (d - (i1 - i0)) / d : -1) }')
	set -- $d0 $d1; deep=$(awk -v a="${1:-0}" -v n="${2:-0}" -v b="${3:-0}" -v t="$dt" 'BEGIN { printf "%d", (n > 0 ? (b - a) * 100 / (n * t * 1e6) : -1) }')
	pkg=-; case "$e0$e1" in *-*) ;; *) pkg=$(awk -v a="$e0" -v b="$e1" -v t="$dt" 'BEGIN { if (b >= a) printf "%.1f", (b - a) / t / 1e6; else print "-" }');; esac
	psy=-; case "$y0$y1" in *-*) ;; *) psy=$(awk -v a="$y0" -v b="$y1" -v t="$dt" 'BEGIN { if (b >= a) printf "%.1f", (b - a) / t / 1e6; else print "-" }');; esac
	rd "$R/sys/class/backlight/intel_backlight/brightness"; bb=$(num "$V"); rd "$R/sys/class/backlight/intel_backlight/max_brightness"; bm=$(num "$V")
	bl=-; case "$bb$bm" in *-*) ;; *) bl=$(awk -v a="$bb" -v b="$bm" 'BEGIN { if (b > 0) printf "%d", a * 100 / b; else print "-" }');; esac
	pwr=$(T dumpsys power)
	wk=$(tok "$(printf '%s\n' "$pwr" | sed -n 's/^ *mWakefulness=\([A-Za-z]*\).*/\1/p' | head -n 1)")
	wl=$(num "$(printf '%s\n' "$pwr" | sed -n 's/^ *Wake Locks: size=\([0-9]*\).*/\1/p' | head -n 1)")
	PW_DEX=$(tok "$(prop init.svc.gb_dexopt)")
	dgpu_find; dgs=-; [ -n "$DG" ] && { dg_state; dgs="$DPS/$DRS"; }
	act=; for x in "$PCI"/*; do rd "$x/power/runtime_status"; [ "$V" = active ] || continue; act="$act ${x##*/}=$(tok "$(lnk "$x/driver")" none)"; done
	tz=-; for z in "$R"/sys/class/thermal/thermal_zone*; do rd "$z/type"; [ "$V" = x86_pkg_temp ] || continue; rd "$z/temp"; tz=$(awk -v t="$(num "$V" 0)" 'BEGIN { printf "%d", t / 1000 }'); done
	rd "$R/sys/firmware/acpi/platform_profile"; pp=$(tok "$V"); rd "$R/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"; epp=$(tok "$V")
	PW_LABEL=$lab; PW_DONE=1
	note power "$lab, ${dt} s: battery net $PW_W W (ac=$PW_AC, $nw samples) | package $pkg W, platform (psys) $psy W (RAPL; needs root) | CPU busy $PW_BUSY %, deepest C-state $deep % | LPIT cpu residency +$(awk -v a="$l0" -v b="$S_LPC" 'BEGIN { if (a == "-" || b == "-") print "-"; else printf "%.0f", b - a }') us"
	note power "screen: wakefulness=$wk backlight=$bl % | wake locks held=$wl | dGPU $dgs | x86_pkg_temp=$tz C | platform_profile=$pp epp=$epp | gb_dexopt=$PW_DEX | uptime $u1 s"
	top=$(proc_top "$p0" "$p1")
	note power "busiest (CPU ticks): ${top:-none}"
	note power "PCI functions not runtime-suspended:${act:- none}"
}
chk_power() {
	[ "$PW_DONE" = 1 ] || pw_snapshot "now"
	if [ "$PW_AC" = 1 ]; then
		if awk -v w="$PW_W" 'BEGIN { exit !(w + 0 < -2) }'; then      # a 30 s mean: a plug-in's first seconds do not count
			# (the 2nd boot's 'not-charging ac=1 ... net -20.6 W' line was 0.3 s after its plug-in, +6.9 W 2 s later)
			verdict power UNKNOWN "on the charger, yet the packs discharge $(awk -v w="$PW_W" 'BEGIN { printf "%.1f", -w }') W: the load exceeds what the charger delivers (a weak charger, or a large draw: unplug, or run 'ac', to measure it)"
		else
			verdict power UNKNOWN "on the charger: the battery rate is not the system's draw (unplug, or run 'ac' to measure on battery)"
		fi
		return
	fi
	if [ "$PW_W" = - ]; then      # (review) a pack without a rate (or none at all) would read as 0.0 W: a false PASS
		verdict power UNKNOWN "no draw figure: a discharging pack reports no power or current rate (POWER_NOW / CURRENT_NOW), or no pack is present"; return; fi
	if [ "$PW_DEX" = running ] || { [ "$PW_BUSY" != - ] && [ "$PW_BUSY" -gt 25 ]; }; then
		verdict power UNKNOWN "not idle (CPU busy $PW_BUSY %, gb_dexopt $PW_DEX): $PW_W W is not an idle figure"; return; fi
	if awk -v w="$PW_W" 'BEGIN { exit !(w + 0 <= 0 && -w <= 10) }'; then
		verdict power PASS "idle draw $(awk -v w="$PW_W" 'BEGIN { printf "%.1f", -w }') W on battery ($PW_LABEL), within Windows' typical 6-10 W"
	elif awk -v w="$PW_W" 'BEGIN { exit !(-w > 15) }'; then
		verdict power FAIL "idle draw $(awk -v w="$PW_W" 'BEGIN { printf "%.1f", -w }') W on battery ($PW_LABEL), far above Windows' typical 6-10 W"
	elif awk -v w="$PW_W" 'BEGIN { exit !(w + 0 > 0) }'; then
		verdict power UNKNOWN "the packs report charging ($PW_W W) without the charger (the base charges the clipboard?)"
	else
		verdict power UNKNOWN "idle draw $(awk -v w="$PW_W" 'BEGIN { printf "%.1f", -w }') W on battery: above 10 W, below the 15 W failure mark"
	fi
}

# ------------------------------------------------------------------------------------------------- main
usage() {
	sed -n '2,/^# Safety/p' "$0" 2>/dev/null | sed -e '$d' -e 's/^# \{0,1\}//'
	exit "${1:-2}"
}
want_item() { case " $SEL " in *" $1 "*) return 0;; esac; return 1; }
SEL=; NOREC=0
while [ $# -gt 0 ]; do
	case "$1" in
	-n|--no-prompt) PROMPT=0 ;;
	-q|--quick) PROMPT=0; QUICK=1 ;;
	-w|--wait) shift; case "${1:-}" in ''|*[!0-9]*) usage 2;; esac; WAIT=$1; [ "$WAIT" -lt 10 ] && WAIT=10; [ "$WAIT" -gt 600 ] && WAIT=600 ;;
	--no-record) NOREC=1 ;;
	-h|--help) usage 0 ;;
	-*) usage 2 ;;
	*) case " $ALL " in *" $1 "*) SEL="$SEL $1";; *) printf 'gb_hwcheck: unknown item %s (items: %s)\n' "$1" "$ALL" >&2; exit 2;; esac ;;
	esac
	shift
done
[ -n "$SEL" ] || SEL=$ALL
trap '' PIPE
SB3=0; dmi_sb3 && SB3=1
UID0=$(id -u 2>/dev/null); UID0=$(num "$UID0" -)
if [ "$UID0" = 0 ] && [ "$NOREC" = 0 ] && [ -d "$R/data/vendor/gb" ]; then
	[ -d "$RECDIR" ] || mkdir "$RECDIR" 2>/dev/null
	if [ -d "$RECDIR" ] && [ ! -L "$RECDIR" ]; then
		REC=$RECDIR/hwcheck-$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now).txt
		: > "$REC" 2>/dev/null || REC=
	fi
fi
upt
rd "$R/sys/class/dmi/id/product_name"; dmi=$(tok "$(printf '%s' "$V" | tr ' ' '_')")
emit "gb_hwcheck v$HCV: read-only self-test | dmi=$dmi build=$(tok "$(prop ro.build.id)") kernel=$(tok "$(uname -r 2>/dev/null)") uptime=${UP} s uid=$UID0 | items:$SEL"
[ "$UP" -lt 600 ] && emit "note: uptime below 10 min: boot-time work (dexopt, the dGPU report) may still run; the power figures are not idle ones"
[ "$PROMPT" = 1 ] && emit "note: physical steps follow as '>>> ACTION n:' lines; each is watched for up to $WAIT s (nothing to type)"
for it in $ALL; do
	want_item "$it" || continue
	case $it in
	sam) chk_sam ;; input) chk_input ;; battery) chk_battery ;; wifi) chk_wifi ;; bt) chk_bt ;; audio) chk_audio ;;
	cameras) chk_cameras ;; dgpu) chk_dgpu ;; ac) chk_ac ;; power) chk_power ;; dtx) chk_dtx ;; posture) chk_posture ;;
	rotation) chk_rotation ;; suspend) chk_suspend ;;
	esac
done
emit "GB_HWCHECK RESULT: PASS=$NP FAIL=$NF UNKNOWN=$NU | FAIL:${LF:- none} | UNKNOWN:${LU:- none} | v$HCV uid=$UID0 sb3=$SB3${REC:+ record=${REC#$R}}"
if [ -n "$REC" ]; then      # keep the newest 10 records (names sort by time)
	ls -1 "$RECDIR" 2>/dev/null | grep -E '^hwcheck-[0-9]{8}-[0-9]{6}\.txt$' | sort -r | awk 'NR > 10' |
		while IFS= read -r f; do rm -f "$RECDIR/$f"; done
fi
[ "$NF" = 0 ]
