"""Assemble a small no-RUN overlay on the immutable original runtime image."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path.home() / "spark-hybrid-20261031/grammar-build"
BASE = "tensorfold-dsv41:rollback-20261031"
ORIGINAL = "tensorfold-dsv41:exl3-tp2-8eb5919ff0a4"


def image(name):
    return json.loads(subprocess.check_output(["docker", "image", "inspect", name]))[0]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_commit")
    args = parser.parse_args()
    assert len(args.source_commit) == 40 and all(c in "0123456789abcdef" for c in args.source_commit)
    base = image(BASE)
    assert base["Id"] == image(ORIGINAL)["Id"], "Rollback anchor moved"
    context = ROOT / "context"
    native = context / "native/bin/tensorfold-native"
    native.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "out/native/bin/tensorfold-native", native)
    library = context / "native/lib/libtfgrammar.so"
    proof = json.loads((ROOT / "logs/library-proof.json").read_text())
    assert proof["ok"] and sha(library) == proof["library_sha256"]
    assert "All 43 tests passed" in (ROOT / "logs/core-lane-tests.log").read_text()
    provenance = {"variant": "original-Mia-numerics-plus-structured-decoding",
                  "source_commit": args.source_commit, "upstream_commit": "629c524a0ea57512a5342abb498d1b7c25353343",
                  "deployed_recipe_commit": "31eea46", "base_image_id": base["Id"],
                  "binary_sha256": sha(native), "backport_patch_sha256": sha(context / "backport.patch"),
                  "grammar_library_proof": proof, "core_lane_tests": 43,
                  "build_memory_limit_bytes": 4294967296, "build_gpu_access": False,
                  "checkpoint_revision": "45ee5d0fd106c94a3d77f721e09cbb2f50e5a16b",
                  "scope": "GPU math and ordinary decoding unchanged; structured requests use non-speculative host masks"}
    (context / "build-provenance.json").write_text(json.dumps(provenance, indent=2))
    assert sum(p.stat().st_size for p in context.rglob("*") if p.is_file()) < 100 << 20
    tag = "tensorfold-dsv41:hybrid-grammar-" + args.source_commit[:12]
    subprocess.run(["docker", "build", "--progress=plain", "--build-arg", "HYBRID_SOURCE=" + args.source_commit,
                    "-t", tag, str(context)], check=True)
    built = image(tag)
    original_layers = base["RootFS"]["Layers"]
    assert built["RootFS"]["Layers"][:len(original_layers)] == original_layers
    manifest = {"image": tag, "image_id": built["Id"], "provenance": provenance}
    (ROOT / "image-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"image": tag, "image_id": built["Id"], "binary_sha256": sha(native)}))


if __name__ == "__main__":
    main()
