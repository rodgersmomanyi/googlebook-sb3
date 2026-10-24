#!/bin/bash
# Host test for tree/gb_hw.sh under mksh (Android's /system/bin/sh) with a fake sysfs tree and a fake MSR file.
# Usage: test_gb_hw.sh <path to gb_hw.sh>
set -u
GB=$1; T=$(mktemp -d); pass=0; fail=0
ok() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1"; else fail=$((fail+1)); echo "FAIL $1: got '$2' want '$3'"; fi; }
mkbin() {
  mkdir -p $T/bin
  printf '#!/bin/sh\ncase "$1" in\n persist.vendor.gb.rapl.mode) cat %s/prop_mode 2>/dev/null;;\n persist.vendor.gb.rapl.pl1_uw) cat %s/prop_pl1 2>/dev/null;;\n persist.vendor.gb.rapl.pl2_uw) cat %s/prop_pl2 2>/dev/null;;\nesac\n' $T $T $T > $T/bin/getprop
  printf '#!/bin/sh\necho "$1=$2" >> %s/setprop.log\n' $T > $T/bin/setprop
  chmod +x $T/bin/*
}
# MSR file: 0x606 = 03 0e 0a 00 ..., 0x610 = PL1 raw $1 (enabled), PL2 raw $2 (enabled), lock bit $3
mkmsr() {
  python3 - "$T/msr" "$1" "$2" "$3" <<'PY'
import sys,struct
f,pl1,pl2,lock=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4])
b=bytearray(4096)
b[0x606:0x60e]=bytes([0x03,0x0e,0x0a,0,0,0,0,0])
lo=pl1|(1<<15)|(1<<16)|(0x6e<<17); hi=pl2|(1<<15)|(lock<<31)
b[0x610:0x618]=struct.pack('<II',lo,hi)
open(f,'wb').write(b)
PY
}
mktree() {
  rm -rf $T/root; Z=$T/root/sys/class/powercap/intel-rapl:0; mkdir -p $Z $T/root/dev $T/root/sys/class/backlight/intel_backlight
  echo package-0 > $Z/name; echo long_term > $Z/constraint_0_name; echo short_term > $Z/constraint_1_name
  echo 25000000 > $Z/constraint_0_power_limit_uw; echo 55000000 > $Z/constraint_1_power_limit_uw
  echo 400 > $T/root/sys/class/backlight/intel_backlight/brightness; echo 7500 > $T/root/sys/class/backlight/intel_backlight/max_brightness
  echo raw > $T/root/sys/class/backlight/intel_backlight/type
  rm -f $T/prop_* $T/setprop.log
}
run() { PATH=$T/bin:$PATH GB_HW_ROOT=$T/root GB_HW_LOG=- GB_HW_MSR=$T/msr mksh "$GB" "$@"; }
pl() { echo "$(cat $T/root/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw)/$(cat $T/root/sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw)"; }
mkbin
mksh -n "$GB" && ok "mksh -n syntax" 0 0 || ok "mksh -n syntax" 1 0
# 1 firmware restore (PL1 15 W = raw 120, PL2 25 W = raw 200)
mktree; mkmsr 120 200 0; run rapl-snapshot; ok "snapshot file" "$(cat $T/root/dev/gb_hw/rapl_fw)" "pl1_uw=15000000 pl2_uw=25000000 pl1_en=1 pl2_en=1 locked=0 pu=3"
ok "setprop vendor.gb.rapl.fw" "$(cat $T/setprop.log)" "vendor.gb.rapl.fw=pl1_uw=15000000 pl2_uw=25000000 pl1_en=1 pl2_en=1 locked=0 pu=3"
run boot >/dev/null; ok "fw mode restores firmware limits" "$(pl)" "15000000/25000000"
# 2 lapis mode leaves values
mktree; mkmsr 120 200 0; run rapl-snapshot >/dev/null; echo lapis > $T/prop_mode; run boot >/dev/null; ok "lapis mode untouched" "$(pl)" "25000000/55000000"
# 3 custom
mktree; echo custom > $T/prop_mode; echo 20000000 > $T/prop_pl1; echo 30000000 > $T/prop_pl2; run boot >/dev/null; ok "custom applied" "$(pl)" "20000000/30000000"
# 4 custom out of range / garbage
mktree; echo custom > $T/prop_mode; echo 90000000 > $T/prop_pl1; echo 95000000 > $T/prop_pl2; run boot >/dev/null; ok "custom out of range rejected" "$(pl)" "25000000/55000000"
mktree; echo custom > $T/prop_mode; echo 20W > $T/prop_pl1; echo 30000000 > $T/prop_pl2; run boot >/dev/null; ok "custom garbage rejected" "$(pl)" "25000000/55000000"
mktree; echo custom > $T/prop_mode; echo 20000000 > $T/prop_pl1; echo 10000000 > $T/prop_pl2; run boot >/dev/null; ok "custom PL2<PL1 rejected" "$(pl)" "25000000/55000000"
# 5 locked firmware
mktree; mkmsr 120 200 1; run rapl-snapshot >/dev/null; run boot >/dev/null; ok "locked: nothing restored" "$(pl)" "25000000/55000000"
ok "locked flag recorded" "$(grep -o 'locked=[01]' $T/root/dev/gb_hw/rapl_fw)" "locked=1"
# 6 no snapshot
mktree; run boot >/dev/null; ok "no snapshot: untouched" "$(pl)" "25000000/55000000"
# 7 implausible firmware value (PL1 2 W) rejected by the range check
mktree; mkmsr 16 200 0; run rapl-snapshot >/dev/null; run boot >/dev/null; ok "implausible fw PL1 rejected" "$(pl)" "25000000/55000000"
# 8 no package-0 zone
mktree; mkmsr 120 200 0; run rapl-snapshot >/dev/null; echo psys > $T/root/sys/class/powercap/intel-rapl:0/name; run boot >/dev/null; ok "no package zone: untouched" "$(pl)" "25000000/55000000"
# 9 xxd fallback (dd without iflag support)
mktree; mkmsr 120 200 0; mkdir -p $T/bin2
printf '#!/bin/sh\nfor a; do case "$a" in iflag=*) echo "dd: unknown operand" >&2; exit 1;; esac; done\nexec /bin/dd "$@"\n' > $T/bin2/dd; chmod +x $T/bin2/dd
if command -v xxd >/dev/null; then
  PATH=$T/bin2:$T/bin:$PATH GB_HW_ROOT=$T/root GB_HW_LOG=- GB_HW_MSR=$T/msr mksh "$GB" rapl-snapshot >/dev/null
  ok "xxd fallback reads 0x606" "$(grep -o 'pu=[0-9]*' $T/root/dev/gb_hw/rapl_fw)" "pu=3"
else echo "SKIP xxd fallback (no xxd)"; fi
# 10 0x606 unreadable at all -> default unit (dd and xxd both fail for 0x606: shim refuses iflag, no xxd in PATH)
mktree; mkmsr 120 200 0; mkdir -p $T/bin3; cp $T/bin2/dd $T/bin3/dd; printf '#!/bin/sh\nexit 1\n' > $T/bin3/xxd; chmod +x $T/bin3/*
PATH=$T/bin3:$T/bin:$PATH GB_HW_ROOT=$T/root GB_HW_LOG=- GB_HW_MSR=$T/msr mksh "$GB" rapl-snapshot | grep -q "assuming the Intel default" && ok "0x606 fallback message" 1 1 || ok "0x606 fallback message" 0 1
ok "0x606 fallback still snapshots" "$(grep -o 'pl1_uw=[0-9]*' $T/root/dev/gb_hw/rapl_fw)" "pl1_uw=15000000"
# 11 diag runs without errors on the fake tree
mktree; mkmsr 120 200 0; run rapl-snapshot >/dev/null; out=$(run diag 2>&1); echo "$out" | grep -q "backlight: intel_backlight max=7500" && ok "diag backlight line" 1 1 || ok "diag backlight line" 0 1
echo "$out" | grep -q "powercap intel-rapl:0 name=package-0 pl1=15000000" && ok "diag powercap line after restore" 1 1 || ok "diag powercap line after restore" 0 1
echo "== $pass passed, $fail failed"; rm -rf $T; [ $fail -eq 0 ]
