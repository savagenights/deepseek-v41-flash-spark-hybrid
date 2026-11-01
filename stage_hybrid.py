"""Stage native-engine/Mia-pack experiments without touching the live deployment."""
import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

NAME = "spark-hybrid-20261031"
OLD = "spark-ab-20261031"
REVISION = "45ee5d0fd106c94a3d77f721e09cbb2f50e5a16b"
BASE = {
    "TF_CUDA_SCHED": "auto", "TF_DSV41_POOL_TOKENS": "1048576",
    "TF_DSV41_KV_SPLIT": "0", "TF_DSV41_KV_SPLIT_COMPACT": "0",
    "TF_DSV41_PF_4K": "0", "TF_DSV41_PREFILL_CHUNK": "2048",
    "TF_DSV41_PREFILL_ROWS": "2048", "TF_DSV41_PREFILL_SHARE": "0.5",
    "TF_DSV41_SESSION_RAM_MIB": "256", "TF_DSV41_GRAPHS_MAX": "48",
    "TF_DSV41_FLOOR_GIB": "5", "TF_DSV41_FLOOR_HARD_GIB": "4",
    "CONTAINER_PREFIX": "spark-hybrid-native-mia", "HOST": "0.0.0.0", "PORT": "8888",
}
REMOVE = {"TF_DSV41_EXPERT_TOPP", "TF_DSV41_SESSION_DISK", "TF_DSV41_SESSION_DISK_GIB",
          "TF_DSV41_SESSION_DISK_MIN", "TF_DSV41_PF_4K_MEMORY_OK", "TF_DSV41_PF_WS_FLOOR_GIB"}


def transform_profile(text, root, rank):
    # Start with the already qualified native recipe; change policy, not its transport plumbing.
    text = text.replace(str(Path.home() / OLD), str(root))
    output = []
    for line in text.splitlines():
        match = re.match(r"(?:export )?([A-Z][A-Z0-9_]*)=", line)
        if match and match.group(1) in REMOVE | BASE.keys():
            continue
        if match and match.group(1) == "ENGRAM_DIR":
            line = f'ENGRAM_DIR="{Path.home() / OLD / ("engram-r" + str(rank))}"'
        output.append(line)
    output += ["", "# Hybrid: original EAQ weights, unpruned routing, original pool/2K chunks."]
    output += [f"{key}={value}" for key, value in BASE.items()]
    return "\n".join(output) + "\n"


def materialize_model(root):
    source = Path.home() / ".cache/huggingface/hub" / (
        "models--Mia-AiLab--DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw/snapshots/" + REVISION)
    target = root / "model"
    target.mkdir(exist_ok=True)
    expected = []
    for item in sorted(source.iterdir()):
        if item.suffix not in {".json", ".safetensors", ".jinja", ".txt"} or not item.is_file():
            continue
        resolved = item.resolve(strict=True)
        destination = target / item.name
        if not destination.exists():
            os.link(resolved, destination)  # Same filesystem: no extra 200-GB model copy on head.
        if not os.path.samefile(resolved, destination):
            raise RuntimeError("Existing staged file is not the original immutable HF blob: " + item.name)
        digest = resolved.name
        if not re.fullmatch("[a-f0-9]{64}", digest):
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        expected.append(f"{digest}  {item.name}")
    if len(expected) < 40:
        raise RuntimeError("Original snapshot appears incomplete")
    (root / "model/SHA256SUMS").write_text("\n".join(expected) + "\n")
    (root / "results/model-provenance.json").write_text(json.dumps({
        "repository": "Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw",
        "revision": REVISION, "source": str(source), "files": len(expected),
        "materialization": "hardlinks to existing immutable Hugging Face blobs",
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rank", type=int, choices=[0, 1])
    args = parser.parse_args()
    root = Path.home() / NAME
    old = Path.home() / OLD
    for name in ("results", "logs", "assets", "prepared", "state", "cache", "rollback", "model"):
        (root / name).mkdir(parents=True, exist_ok=True)
    recipe = root / "recipe"
    if not recipe.exists():
        shutil.copytree(old / "recipe", recipe,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", ".cache"))
    profile = transform_profile((old / "recipe/config/prod-zig.env").read_text(), root, args.rank)
    (recipe / "config/prod-zig.env").write_text(profile)
    (recipe / "config/hybrid-fast.env").write_text(profile)
    # Enforce actual cgroup limits for CPU-only enumeration (BuildKit flags were insufficient).
    launcher = recipe / "scripts/run-zig.sh"
    text = launcher.read_text()
    old_command = "docker run --rm --init --runtime=runc -e NVIDIA_VISIBLE_DEVICES=void"
    new_command = "docker run --rm --init --runtime=runc --memory=1g --memory-swap=1g --cpus=2 -e NVIDIA_VISIBLE_DEVICES=void"
    if old_command in text:
        text = text.replace(old_command, new_command)
        launcher.write_text(text)
    for item in (old / "assets").iterdir():
        target = root / "assets" / item.name
        if not target.exists():
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
    shutil.copy2(old / "recipe/config/pfdense-table.json", root / "cache/pfdense-table.json")
    if args.rank == 0:
        materialize_model(root)
    print("Staged rank", args.rank, "in", root, flush=True)


if __name__ == "__main__":
    main()
