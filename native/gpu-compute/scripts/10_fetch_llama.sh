#!/bin/bash
# Fetch llama.cpp at the pinned release tag (github.com/ggml-org/llama.cpp only) and verify the commit.
# The pin rule (INTERFACES 18.5): newest bNNNN tag that is >= 14 days old at fetch time -> LLAMA_PIN.
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
if [ ! -d "$LLAMA/.git" ]; then
  $NICE git clone --filter=blob:none --no-checkout --quiet https://github.com/ggml-org/llama.cpp "$LLAMA"
fi
cd "$LLAMA"
git rev-parse -q --verify "refs/tags/$LLAMA_TAG" >/dev/null ||
  $NICE git fetch --quiet origin "refs/tags/$LLAMA_TAG:refs/tags/$LLAMA_TAG"
got=$(git rev-parse "$LLAMA_TAG^{commit}")
[ "$got" = "$LLAMA_COMMIT" ] || { echo "tag $LLAMA_TAG is $got, expected $LLAMA_COMMIT"; exit 1; }
[ "$(git rev-parse HEAD 2>/dev/null)" = "$LLAMA_COMMIT" ] ||
  $NICE git -c advice.detachedHead=false checkout --quiet "$LLAMA_COMMIT"
[ "$(git rev-parse HEAD)" = "$LLAMA_COMMIT" ]
dirty=$(git status --porcelain --untracked-files=no)
[ -z "$dirty" ] || { echo "llama.cpp tree is modified:"; echo "$dirty"; exit 1; }
echo "llama.cpp $LLAMA_TAG = $LLAMA_COMMIT ($(git log -1 --format=%cI)) clean"
