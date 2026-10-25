#!/bin/sh
# rc_lint.sh -- static checks of an Android init .rc fragment (NP-24, INTERFACES.md v1 18.1).
#   sh rc_lint.sh <file.rc> [external-service ...]
# Checks: every service has user, seclabel u:r:shell:s0 (invariant 20) and disabled (started only by triggers);
# no duplicate service; every start/stop/restart names a service defined here or listed as external; trigger
# property names <= 31 characters; actions/options indented by exactly 4 spaces; no tabs or CR; the
# vendor.gb.dgpu_report/nvk_probe-style self-resetting triggers reset their property before starting.
# Exit 0 = clean; every finding is printed as "rc_lint: <file>:<line>: <message>".
f=$1
[ -r "$f" ] || { echo "usage: rc_lint.sh <file.rc> [external-service ...]"; exit 2; }
shift
awk -v ext=" $* " -v file="$f" '
function err(m) { printf "rc_lint: %s:%d: %s\n", file, NR, m; bad++ }
function close_svc() {
  if (svc != "") {
    if (!has_user[svc]) { printf "rc_lint: %s: service %s has no user\n", file, svc; bad++ }
    if (!has_sec[svc]) { printf "rc_lint: %s: service %s has no seclabel u:r:shell:s0\n", file, svc; bad++ }
    if (!has_dis[svc]) { printf "rc_lint: %s: service %s is not disabled\n", file, svc; bad++ }
  }
  svc = ""
}
/\t/ { err("tab character") }
/\r/ { err("CR character") }
/^#/ || /^[ ]*$/ { next }
/^service / {
  close_svc(); sect = "service"; svc = $2
  if (svc in defined) err("duplicate service " svc)
  defined[svc] = 1; nsvc++
  if (NF < 3) err("service " svc " has no command")
  next
}
/^on / {
  close_svc(); sect = "on"; reset = ""
  for (i = 2; i <= NF; i++) {
    if ($i == "&&") continue
    if ($i ~ /^property:/) {
      p = $i; sub(/^property:/, "", p); v = p; sub(/=.*/, "", p); sub(/^[^=]*=/, "", v)
      if (length(p) > 31) err("property name longer than 31: " p)
      if (p ~ /^vendor\.gb\.(dgpu_report|nvk_probe)$/ && v != "*" && v != "0") reset = p
    } else if ($i !~ /^(early-init|init|late-init|post-fs|post-fs-data|boot|charger|fs|early-boot)$/) {
      err("unknown trigger " $i)
    }
  }
  first = 1
  next
}
/^[^ ]/ { err("unexpected top-level line"); next }
/^    [^ ]/ {
  if (sect == "service") {
    if ($1 == "user") has_user[svc] = 1
    if ($1 == "seclabel" && $2 == "u:r:shell:s0") has_sec[svc] = 1
    if ($1 == "disabled") has_dis[svc] = 1
  } else if (sect == "on") {
    if ($1 == "start" || $1 == "stop" || $1 == "restart") { used[$2] = NR }
    if (reset != "" && first && !($1 == "setprop" && $2 == reset && $3 == "0")) err("trigger on " reset " must reset it first")
    first = 0
  } else err("action outside a section")
  next
}
{ err("bad indentation") }
END {
  close_svc()
  for (s in used) if (!(s in defined) && index(ext, " " s " ") == 0) { printf "rc_lint: %s:%d: start/stop of undefined service %s\n", file, used[s], s; bad++ }
  printf "rc_lint: %s: %d services, %d findings\n", file, nsvc, bad + 0
  exit bad > 0
}' "$f"
