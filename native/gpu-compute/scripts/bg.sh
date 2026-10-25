#!/bin/bash
# Run a long builder job detached: bg.sh <name> <command...>  -> logs/<name>.log, logs/<name>.done (with rc=)
. "$(dirname "$0")/00_env.sh"
name=$1
shift
mkdir -p "$W/logs"
rm -f "$W/logs/$name.done"
setsid nohup bash -c '"$@" > "'"$W/logs/$name.log"'" 2>&1; echo "rc=$?" >> "'"$W/logs/$name.log"'"; touch "'"$W/logs/$name.done"'"' \
  bg "$@" < /dev/null > /dev/null 2>&1 &
echo "started $name (pid $!)"
