#!/bin/sh
# Builder side: unpack every .apex / .capex payload under fs/ into apex/<partition>__<name>/ (read-only on inputs).
# usage: unpack_apex.sh /var/tmp/vnniperf
set -u
R=$1
cd "$R" || exit 1
find fs -name '*.apex' -o -name '*.capex' | sort | while read -r a; do
  part=$(echo "$a" | cut -d/ -f2)
  n=$(basename "$a"); n=${n%.*}
  d="apex/${part}__$n"; t="apex/.tmp_$n"
  rm -rf "$t" "$d"; mkdir -p "$t" "$d"
  case "$a" in
    *.capex) unzip -q -o "$a" original_apex -d "$t" && unzip -q -o "$t/original_apex" apex_payload.img apex_manifest.pb -d "$t" ;;
    *)       unzip -q -o "$a" apex_payload.img apex_manifest.pb -d "$t" ;;
  esac
  ft=$(file -b "$t/apex_payload.img" | cut -c1-12)
  case "$ft" in
    EROFS*) fsck.erofs --extract="$d" --no-preserve "$t/apex_payload.img" >/dev/null 2>&1; rc=$? ;;
    *ext*|Linux*)  debugfs -R "rdump / $d" "$t/apex_payload.img" >/dev/null 2>&1; rc=$? ;;
    *) rc=99 ;;
  esac
  echo "$a -> $d [$ft] rc=$rc files=$(find "$d" -type f | wc -l) sha256=$(sha256sum "$a" | cut -c1-16)"
  rm -rf "$t"
done
