#!/bin/sh
# CPU-only, hard-cgroup-bounded native build on the worker, not inside Docker BuildKit.
set -eu
ROOT="$HOME/spark-hybrid-20261031/grammar-build"
NAME=spark-hybrid-grammar-build
IMAGE=tensorfold-dsv41:rollback-20261031
BUILD_MEMORY=${BUILD_MEMORY:-1g}
BUILD_MEMORY_BYTES=${BUILD_MEMORY_BYTES:-1073741824}
BUILD_CPUS=${BUILD_CPUS:-1}
mkdir -p "$ROOT/out" "$ROOT/cache-user" "$ROOT/logs"
exec 9>"$ROOT/build.lock"
flock -n 9 || { echo 'Another hybrid native build is active'; exit 1; }
test -f "$ROOT/source-v1/build.zig"
AVAILABLE=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
[ "$AVAILABLE" -ge 7864320 ] || { echo 'Insufficient worker headroom for a live CPU build'; exit 1; }

# Stop our compiler, never the serving model, if unrelated traffic eats the safety margin.
watch_memory() {
    while :; do
        sleep 2
        AVAILABLE=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
        if [ "$AVAILABLE" -lt 5242880 ]; then
            echo 'Worker below 5 GiB: stopping the isolated compiler' >> "$ROOT/logs/build-memory.log"
            docker stop -t 3 "$NAME" >/dev/null 2>&1 || true
            exit
        fi
    done
}
watch_memory &
WATCHER=$!
trap 'kill "$WATCHER" 2>/dev/null || true' EXIT
trap 'docker stop -t 3 "$NAME" >/dev/null 2>&1 || true; exit 143' INT TERM

docker run --rm --name "$NAME" --runtime=runc --user "$(id -u):$(id -g)" \
    --memory="$BUILD_MEMORY" --memory-swap="$BUILD_MEMORY" --cpus="$BUILD_CPUS" --pids-limit=256 --network=none \
    --cap-drop=ALL --security-opt=no-new-privileges -e NVIDIA_VISIBLE_DEVICES=void \
    -e BUILD_MEMORY_BYTES="$BUILD_MEMORY_BYTES" -e ZIG_GLOBAL_CACHE_DIR=/cache/global \
    --entrypoint /bin/sh -v "$ROOT/source-v1:/source:ro" \
    -v "$ROOT/cache-user:/cache" -v "$ROOT/out:/out" -w /source "$IMAGE" -c '
        set -eu
        test "$(cat /sys/fs/cgroup/memory.max)" = "$BUILD_MEMORY_BYTES"
        test ! -e /dev/nvidia0
        echo "Compiler: $(/opt/zig/zig version); enforced memory: $(cat /sys/fs/cgroup/memory.max)"
        /opt/zig/zig build native -Dnvcc=/usr/local/cuda/bin/nvcc -Dsm=121 -Doptimize=ReleaseFast \
            --prefix /out -j1 --cache-dir /cache/build-local
        test -x /out/native/bin/tensorfold-native
        sha256sum /out/native/bin/tensorfold-native
    '
printf 'NATIVE_BUILD_PASSED\n' > "$ROOT/build-ready.txt"
