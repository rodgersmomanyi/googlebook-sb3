#!/bin/sh
# Rebuild the static tinyalsa tools shipped in tree/vup/bin (run on the builder VM).
# Source: github.com/tinyalsa/tinyalsa tag v2.0.0 (1c5fb68, 2021-02-01) + tinymix-setall.patch, which makes
# "tinymix set NAME VALUE" with one value set every channel (v2.0.0 set only channel 0).
set -e
W=/build/native/audio
cd $W
[ -d tinyalsa ] || git clone https://github.com/tinyalsa/tinyalsa.git
cd tinyalsa
git checkout -q v2.0.0
git apply --check ../tinymix-setall.patch 2>/dev/null && git apply ../tinymix-setall.patch || true
CC="/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34"
LIB="src/limits.c src/mixer.c src/pcm.c src/pcm_hw.c src/mixer_hw.c src/pcm_plugin.c src/snd_card_plugin.c src/mixer_plugin.c"
mkdir -p ../bin
for t in tinymix tinyplay tinycap tinypcminfo; do
  $CC -static -O2 -s -Iinclude -Isrc $LIB utils/$t.c -o ../bin/$t -ldl
done
ls -la ../bin
