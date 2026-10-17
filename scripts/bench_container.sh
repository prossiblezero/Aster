#!/usr/bin/env bash
# Run bench_harness_overhead.py for a git ref inside a task container image (same mounts and
# limits as the evaluation). Usage: bench_container.sh <git-ref> <image-id> [bench args...]
set -euo pipefail
ref=$1; image=$2; shift 2
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d /tmp/aster-bench-XXXX)
mkdir -p "$work/bin" "$work/opt"
cp "$repo/scripts/bench_harness_overhead.py" "$work/bin/"
git -C "$repo" archive "$ref" src/aster | tar -x -C "$work/opt" --strip-components=1
runtime="$repo/artifacts/private/general-coding-50-20261012/runtime"
docker run --rm --cpus 4 -m 8g --network none \
  -v "$runtime/tiktoken-cache:/opt/tiktoken-cache:ro" \
  -v "$work/opt:/opt/agent-code:ro" -v "$work/bin:/bench:ro" \
  -v /home/dzh/.conda/envs/aster:/home/dzh/.conda/envs/aster:ro \
  -v "$(readlink -f /usr/lib/x86_64-linux-gnu/libseccomp.so.2):/usr/lib/x86_64-linux-gnu/libseccomp.so.2:ro" \
  -e PYTHONPATH=/opt/agent-code -e TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache -w /tmp "$image" \
  /home/dzh/.conda/envs/aster/bin/python /bench/bench_harness_overhead.py "$@"
