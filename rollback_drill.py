"""Exercise the documented rollback end-to-end, then restore the already-qualified hybrid."""
import json
import os
import signal
import subprocess
import time
from runtime import ROOT, health, run, write_json
from promote_grammar import wait_supervised
from verify_grammar import load_qualified
import docker_clone as dc
from api_contract import MODEL, closed, request


def interrupted(signum, frame):
    raise RuntimeError(f"Rollback drill interrupted: {signum}")


def main():
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    manifest, _, _ = load_qualified()
    if health().get("live", {}).get("connections", 0):
        raise RuntimeError("Live traffic detected; defer the optional rollback drill")
    started = time.time()
    directory = ROOT / "results" / f"grammar-rollback-drill-{int(started)}"
    directory.mkdir()
    done = directory / "complete.txt"
    report = {"started": started, "original_verified": False, "hybrid_restored": False}
    guardian = subprocess.Popen(["sh", "-c", f"sleep 1200; test -f '{done}' || sh '{ROOT}/grammar_rollback.sh'"],
                                stdout=(directory / "guardian.log").open("a"), stderr=subprocess.STDOUT,
                                start_new_session=True)
    try:
        run(["sh", ROOT / "grammar_rollback.sh"], 600, log=directory / "rollback.log")
        original = dc.docker("GET", "/containers/dsv41-1017-exl3-tf/json")
        if not original["State"]["Running"] or original["Image"] != manifest["provenance"]["base_image_id"]:
            raise RuntimeError("Rollback did not restore the original image")
        for unit, expected_active, expected_enabled in [("dsv41.service", "active", "enabled"),
                                                       ("dsv41-hybrid.service", "inactive", "disabled")]:
            for action, expected in [("is-active", expected_active), ("is-enabled", expected_enabled)]:
                actual = run(["systemctl", "--user", action, unit], check=False).stdout.strip()
                if actual != expected:
                    raise RuntimeError("Rollback unit state mismatch: " + unit)
        result = request("http://127.0.0.1:8888", {"model": MODEL, "messages": [{"role": "user", "content": "What is 17*19? Reply only with the integer."}],
                         "max_tokens": 16, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}})
        if result["status"] != 200 or result["content"].strip() != "323":
            raise RuntimeError("Original API smoke check failed")
        report["original_verified"] = True
        report["original_ready_seconds"] = time.time() - started
        run(["sha256sum", "-c", ROOT / "rollback/config-sha256.txt"])
        restore_started = time.time()
        run(["systemctl", "--user", "disable", "--now", "dsv41.service"], 180)
        run(["systemctl", "--user", "reset-failed", "dsv41.service"], check=False)
        run(["systemctl", "--user", "enable", "--now", "dsv41-hybrid.service"], 180)
        report["restored"] = wait_supervised(manifest, restore_started)
        schema = closed({"n": {"type": "integer", "const": 7}})
        result = request("http://127.0.0.1:8888", {"model": MODEL, "messages": [{"role": "user", "content": "Return compact JSON with n equal to 7."}],
                         "max_tokens": 128, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
                         "response_format": {"type": "json_schema", "json_schema": {"name": "restored", "strict": True, "schema": schema}}})
        if result["status"] != 200 or json.loads(result["content"]) != {"n": 7}:
            raise RuntimeError("Restored hybrid schema smoke check failed")
        report["hybrid_restored"] = True
        report["hybrid_restore_seconds"] = time.time() - restore_started
        done.write_text("ROLLBACK_PROVEN_AND_QUALIFIED_HYBRID_RESTORED\n")
    except Exception as error:
        report["error"] = str(error)
        restored = run(["sh", ROOT / "grammar_rollback.sh"], 600, False, directory / "fallback.log")
        report["left_original_serving"] = restored.returncode == 0
    finally:
        if report["hybrid_restored"] or report.get("left_original_serving"):
            os.killpg(guardian.pid, signal.SIGTERM)
        report["finished"] = time.time()
        write_json(directory / "drill.json", report)
        write_json(ROOT / "results/grammar-rollback-drill.json", report)
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
