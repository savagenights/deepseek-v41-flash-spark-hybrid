"""Wait for the guarded full-suite trial, then promote autonomously only if every gate passed."""
import fcntl
import json
import signal
import time
from runtime import ROOT, run, write_json
from verify_grammar import STAGE, load_qualified


def interrupted(signum, frame):
    raise RuntimeError(f"Finish controller interrupted: {signum}")


def main():
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    lock = (ROOT / "finish.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    report = {"started": time.time(), "promotion_attempted": False, "promoted": False}
    try:
        deadline = time.monotonic() + 5400
        while time.monotonic() < deadline:
            path = STAGE / "results/trial.json"
            trial = json.loads(path.read_text()) if path.exists() else {}
            if trial.get("finished"):
                if not trial.get("qualified") or not trial.get("restored_original"):
                    raise RuntimeError("Trial failed a required gate or did not restore the original service")
                controller = (ROOT / "controller.lock").open("a+")
                try:
                    fcntl.flock(controller, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    controller.close()
                    time.sleep(3)
                    continue
                fcntl.flock(controller, fcntl.LOCK_UN)
                controller.close()
                break
            time.sleep(10)
        else:
            raise RuntimeError("Full qualification did not finish within the bounded wait")
        manifest, trial, quality = load_qualified()
        report["quality_bench_id"] = quality["candidate_id"]
        report["image"] = manifest["image"]
        report["promotion_attempted"] = True
        run(["python3", ROOT / "promote_grammar.py"], 3300, False, ROOT / "logs/grammar-promotion-controller.log")
        deployment = json.loads((ROOT / "results/grammar-deployment.json").read_text())
        report["promoted"] = deployment["promoted"]
        report["deployment"] = "results/grammar-deployment.json"
        if not report["promoted"]:
            report["error"] = deployment.get("error", "Deployment acceptance failed")
            report["restored_original"] = deployment.get("restored_original", False)
            if not report["restored_original"]:
                raise RuntimeError("Deployment failed without confirmed rollback")
    except Exception as error:
        report["error"] = str(error)
        print("FINISH_NOT_PROMOTED", str(error), flush=True)
        if report["promotion_attempted"]:
            restored = run(["sh", ROOT / "grammar_rollback.sh"], 600, False)
            report["restored_original"] = restored.returncode == 0
    finally:
        report["finished"] = time.time()
        write_json(ROOT / "results/grammar-finish.json", report)
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
