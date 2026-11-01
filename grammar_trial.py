"""GPU-qualify the minimal backport, optionally run real SparkDash, then restore production."""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
import docker_clone as dc
from runtime import ROOT, WORKER, benchmark, compare, health, remote, run, write_json

NAME = "spark-hybrid-grammar-r0"
STAGE = ROOT / "grammar-qualification"


def interrupted(signum, frame):
    raise RuntimeError(f"Grammar trial interrupted: {signum}")


def ready(seconds=600):
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        current = dc.inspect(NAME)
        if not current or not current["State"]["Running"]:
            raise RuntimeError("Grammar head exited during startup; inspect its saved log")
        try:
            snapshot = health()
            if snapshot.get("status") == "ok" and not snapshot.get("warming"):
                return {"startup_s": time.monotonic() - started, "health": snapshot}
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("Grammar startup exceeded its deadline")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality", action="store_true")
    args = parser.parse_args()
    lock = (ROOT / "controller.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    os.umask(0o077)
    for directory in (STAGE / "results", STAGE / "logs"):
        directory.mkdir(parents=True, exist_ok=True)
    done = STAGE / "results" / f"trial-complete-{int(time.time())}.txt"
    report_file = STAGE / "results/trial.json"
    report = {"started": time.time(), "qualified": False, "restored_original": False}
    maintenance, guardian, followers = False, None, []
    try:
        run(["sha256sum", "-c", ROOT / "rollback/config-sha256.txt"])
        manifest = json.loads((ROOT / "grammar-build/image-manifest.json").read_text())
        report["image"] = manifest["image"]
        report["source_commit"] = manifest["provenance"]["source_commit"]
        info = json.loads(run(["docker", "image", "inspect", "--format", "{{json .Id}}", manifest["image"]]).stdout)
        if info != manifest["image_id"]:
            raise RuntimeError("Staged image identity changed")
        if health().get("live", {}).get("connections", 0):
            raise RuntimeError("Live traffic detected before maintenance")
        baseline = benchmark(STAGE / "results", "baseline")
        write_json(report_file, report)
        guardian = subprocess.Popen(["sh", "-c", f"sleep 7200; test -f '{done}' || sh '{ROOT}/grammar_rollback.sh'"],
                                    stdout=(STAGE / "logs/guardian.log").open("a"), stderr=subprocess.STDOUT,
                                    start_new_session=True)
        if health().get("live", {}).get("connections", 0):
            raise RuntimeError("New traffic detected before maintenance")
        maintenance = True
        run(["systemctl", "--user", "stop", "dsv41.service"], 180)
        for state in [run(["docker", "inspect", "-f", "{{.State.Running}}", "dsv41-1017-exl3-tf"], check=False),
                      remote("docker inspect -f '{{.State.Running}}' dsv41-1017-exl3-tf", check=False)]:
            if state.stdout.strip() == "true":
                raise RuntimeError("Production survived supervisor stop")
        dc.stop(NAME)
        dc.remove(NAME)
        remote(f"umask 077; mkdir -p '{STAGE}/logs'; python3 '{ROOT}/docker_clone.py' stop spark-hybrid-grammar-r1; python3 '{ROOT}/docker_clone.py' remove spark-hybrid-grammar-r1")
        remote(f"python3 '{ROOT}/docker_clone.py' start spark-hybrid-grammar-r1 --snapshot '{ROOT}/rollback/worker-container.json' --image tensorfold-dsv41:rollback-20261031")
        dc.start(ROOT / "rollback/head-container.json", NAME, manifest["image"], True)
        followers.append(subprocess.Popen(["docker", "logs", "-f", NAME],
                         stdout=(STAGE / "logs/rank0.log").open("w"), stderr=subprocess.STDOUT, start_new_session=True))
        followers.append(subprocess.Popen(["ssh", "-o", "BatchMode=yes", WORKER, "docker logs -f spark-hybrid-grammar-r1"],
                         stdout=(STAGE / "logs/rank1.log").open("w"), stderr=subprocess.STDOUT, start_new_session=True))
        run(["bash", Path.home() / "DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/scripts/memguard.sh", "start", NAME,
             "3.5", STAGE / "logs/memguard-head.log", ROOT / "grammar-memguard.pid", "2", "2.5"])
        remote(f"bash ~/.cache/dsv41-tensorfold/memguard.sh start spark-hybrid-grammar-r1 3.5 '{STAGE}/logs/memguard-worker.log' '{ROOT}/grammar-memguard.pid' 2 2.5")
        report["ready"] = ready()
        current = dc.inspect(NAME)
        assert current["Image"] == manifest["image_id"]
        original = json.loads((ROOT / "rollback/head-container.json").read_text())
        if isinstance(original, list):
            original = original[0]
        before = dict(entry.split("=", 1) for entry in original["Config"]["Env"])
        after = dict(entry.split("=", 1) for entry in current["Config"]["Env"])
        changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        report["environment_changed_keys"] = changed
        assert changed == ["TF_HYBRID_GRAMMAR"], "Unexpected runtime environment drift"
        assert current["Config"]["Cmd"] == original["Config"]["Cmd"]
        benchmark(STAGE / "results", "candidate-cold")
        warm = benchmark(STAGE / "results", "candidate-warm")
        report["performance"] = compare(baseline, warm)
        write_json(report_file, report)
        run(["python3", ROOT / "api_contract.py", STAGE / "results/api-contract.json"], 900, False,
            STAGE / "logs/api-contract.log")
        report["api"] = json.loads((STAGE / "results/api-contract.json").read_text())
        run(["python3", ROOT / "code_quality.py", STAGE / "results/coding.json"], 300, False,
            STAGE / "logs/coding.log")
        coding = json.loads((STAGE / "results/coding.json").read_text())
        report["coding"] = {"passed": coding["passed"], "total": coding["total"]}
        report["qualified"] = (not report["performance"]["reasons"]
                               and report["api"]["passed"] == report["api"]["total"]
                               and coding["passed"] == coding["total"])
        write_json(report_file, report)
        if report["qualified"] and args.quality:
            shutil.copy2(Path.home() / "spark-ab-20261031/results/sparkdash-baseline.json",
                         STAGE / "results/sparkdash-baseline.json")
            result = run(["python3", ROOT / "grammar_sparkdash_quality.py", "--stage", STAGE,
                          "--label", "Hybrid Mia EAQ 2.9bpw original kernels + grammar " + report["source_commit"][:12]],
                         3900, False, STAGE / "logs/sparkdash-quality.log")
            report["sparkdash_exit"] = result.returncode
            report["sparkdash"] = json.loads((STAGE / "results/sparkdash-comparison.json").read_text())
            report["qualified"] = not report["sparkdash"]["reasons"]
    except Exception as error:
        report["error"] = str(error)
        report["qualified"] = False
        print("TRIAL_FAILED", str(error), flush=True)
    finally:
        for follower in followers:
            try:
                os.killpg(follower.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if maintenance:
            result = run(["sh", ROOT / "grammar_rollback.sh"], 600, False,
                         STAGE / "logs/rollback.log")
            report["restored_original"] = result.returncode == 0
            if report["restored_original"]:
                report["final_health"] = health()
                run(["sha256sum", "-c", ROOT / "rollback/config-sha256.txt"])
                done.write_text("ORIGINAL_RESTORED\n")
        if guardian and (not maintenance or report["restored_original"]):
            os.killpg(guardian.pid, signal.SIGTERM)
        report["finished"] = time.time()
        write_json(report_file, report)
        print(json.dumps({key: value for key, value in report.items() if key not in ("api", "sparkdash")}), flush=True)


if __name__ == "__main__":
    main()
