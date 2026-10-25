#!/bin/bash
# Sequential NVK builds for nvk-gfxdriver Wave 1; each one starts only when the builder is idle (etiquette:
# 1-min load < 2 and no gbiso job). Log: logs/queue.log
W=/build/native/nvk-android
Q=$W/logs/queue.log
wait_idle() {
  while :; do
    l=$(cut -d" " -f1 /proc/loadavg)
    if ! pgrep -f "gbiso" >/dev/null && awk -v l="$l" "BEGIN{exit !(l < 2.0)}"; then return; fi
    sleep 60
  done
}
step() { wait_idle; echo "$(date -Is) start $* (load $(cut -d" " -f1-3 /proc/loadavg))" >> $Q; "$@"; echo "$(date -Is) end $* rc=$?" >> $Q; }
step $W/run_build.sh 8a-1 8a
[ "$(cat $W/results/8a-1.done)" = 0 ] || { echo "$(date -Is) 8a-1 failed, queue stopped" >> $Q; exit 1; }
step $W/run_build.sh 8a-2 8a -r
step $W/run_build.sh 8b 8b
step $W/run_build.sh base-new base
wait_idle
echo "$(date -Is) restore tree to set 8a + drm-shim" >> $Q
cd $W && python3 -I scripts/apply_patches.py --set 8a src/mesa patches >> $Q 2>&1
PATCHSET=8a nice -n 19 ionice -c3 bash scripts/35_build_drm_shim_test.sh >> $W/logs/drm_shim_8a.log 2>&1; echo "$(date -Is) drm-shim rc=$?" >> $Q
rm -rf $W/b-nvk-8a-r $W/stage-8a-r $W/b-nvk-base $W/stage-base
echo "$(date -Is) queue done" >> $Q
touch $W/results/queue.done
