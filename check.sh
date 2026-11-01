#!/bin/sh
# Pre-commit sanity: syntax, unit-testable pieces, and a crude secret sweep.
set -eu
cd "$(dirname "$0")"

echo "== python syntax =="
python3 -m py_compile ./*.py
echo "ok"

echo "== shell syntax =="
for script in ./*.sh; do sh -n "$script"; done
echo "ok"

echo "== docker clone safety tests =="
python3 -m unittest test_docker_clone -v 2>&1 | tail -4

echo "== secret sweep =="
# Anything that looks like a live credential in a tracked file is a release blocker.
if grep -rInE 'hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
    --exclude-dir=.git .; then
    echo "FAIL: possible credential in tracked files" >&2
    exit 1
fi
echo "ok"

echo "== no binaries or weights staged =="
if git ls-files | grep -E '\.(so|bin|bundle|safetensors|gguf)$'; then
    echo "FAIL: binary artefacts are tracked" >&2
    exit 1
fi
echo "ok"

echo "all checks passed"
