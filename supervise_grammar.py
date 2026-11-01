"""Durable two-rank supervisor; systemd owns restart policy, Docker does not."""
import fcntl
import json
import os
import signal
import subprocess
import time
from runtime import ROOT, run, write_json
from grammar_runtime import HEAD, follow_logs, ready, start_pair, stop_followers, stop_pair
from verify_grammar import load_qualified


class StopRequested(BaseException):
    """Like KeyboardInterrupt: readiness retries must never swallow a stop request."""


def stop_requested(signum, frame):
    raise StopRequested()


def main():
    os.umask(0o077)
    lock = (ROOT / "controller.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_requested)
    manifest, report, quality = load_qualified()
    enabled = run(["systemctl", "--user", "is-enabled", "dsv41.service"], check=False).stdout.strip()
    state = run(["systemctl", "--user", "is-active", "dsv41.service"], check=False).stdout.strip()
    if enabled not in ("disabled", "masked") or state not in ("inactive", "failed"):
        raise RuntimeError("Original supervisor must be stopped and disabled before starting the hybrid")
    run(["sha256sum", "-c", ROOT / "rollback/config-sha256.txt"])
    session = ROOT / "logs" / f"grammar-service-{int(time.time())}"
    followers, waiter, started = [], None, False
    try:
        started = True
        identity = start_pair(manifest, session)
        followers = follow_logs()
        healthy = ready()
        write_json(ROOT / "results/grammar-service-state.json",
                   {"started": time.time(), "pid": os.getpid(), "identity": identity, "ready": healthy,
                    "image": manifest["image"], "quality_bench_id": quality["candidate_id"]})
        print("HYBRID_READY", json.dumps(identity), flush=True)
        waiter = subprocess.Popen(["docker", "wait", HEAD], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        while waiter.poll() is None:
            time.sleep(1)
        result = waiter.communicate()[0].strip()
        raise RuntimeError("Hybrid head exited; systemd will restart the pair (Docker status " + result + ")")
    except StopRequested:
        print("Supervisor stopping its own pair", flush=True)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_IGN)
        if waiter and waiter.poll() is None:
            waiter.terminate()
            try:
                waiter.wait(timeout=5)
            except subprocess.TimeoutExpired:
                waiter.kill()
        stop_followers(followers)
        if started:
            try:
                stop_pair()
            finally:
                write_json(ROOT / "results/grammar-service-state.json",
                           {"stopped": time.time(), "pid": os.getpid(), "image": manifest["image"]})


if __name__ == "__main__":
    main()
