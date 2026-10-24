#!/bin/bash
# Master: wait for cloud-init, mount the 120G build disk, add swap, install repo, sync the PINNED GKI tree.
set -uo pipefail
echo "=== $(date) waiting for cloud-init ==="
sudo cloud-init status --wait >/dev/null 2>&1 || true

# mount the ~120G build disk (nvme0n2) if /build isn't already a mountpoint
if ! mountpoint -q /build; then
  DISK=/dev/nvme0n2
  [ -b "$DISK" ] || DISK=/dev/sdb
  echo "=== formatting+mounting $DISK at /build ==="
  sudo mkfs.ext4 -F -q "$DISK"
  sudo mkdir -p /build
  sudo mount "$DISK" /build
fi
sudo chown -R builder:builder /build

LOG=/build/sync.log
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date) setup+sync start ==="
echo "--- disk ---"; df -h /build /
echo "--- mem ---"; free -h

# 12G swapfile on /build (RAM is only 7G)
if ! swapon --show | grep -q /build/swapfile; then
  echo "=== creating 12G swapfile ==="
  sudo fallocate -l 12G /build/swapfile
  sudo chmod 600 /build/swapfile
  sudo mkswap /build/swapfile >/dev/null
  sudo swapon /build/swapfile
fi
free -h

# repo launcher
if ! command -v repo >/dev/null 2>&1; then
  sudo curl -fsSL https://storage.googleapis.com/git-repo-downloads/repo -o /usr/local/bin/repo
  sudo chmod a+rx /usr/local/bin/repo
fi
git config --global user.email builder@local
git config --global user.name builder
git config --global color.ui false

cd /build
mkdir -p gki && cd gki
MANIFEST="$HOME/manifest_16302624.xml"
[ -f "$MANIFEST" ] && echo "pinned manifest present at $MANIFEST"

echo "=== repo init $(date) ==="
repo init -u https://android.googlesource.com/kernel/manifest -b common-android17-6.18-2026-06
if [ -f "$MANIFEST" ]; then
  cp "$MANIFEST" .repo/manifests/manifest_16302624.xml
  repo init -m manifest_16302624.xml
fi

echo "=== repo sync starting $(date) (LONG) ==="
repo sync -c -j"$(nproc)" --no-tags --optimized-fetch --fail-fast
RC=$?
echo "=== repo sync rc=$RC $(date) ==="
du -sh . 2>/dev/null
git -C common log --oneline -1 2>/dev/null
ls -la tools/bazel 2>/dev/null
if [ $RC -eq 0 ]; then echo "SYNC_DONE_OK"; else echo "SYNC_DONE_FAIL"; fi
