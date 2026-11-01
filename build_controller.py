"""Build the host-only backport during bounded maintenance; always restore production."""
import fcntl
import os
import shlex
import signal
import subprocess
import time
from runtime import ROOT, WORKER, health, remote, run, write_json


def interrupted(signum, frame):
    raise RuntimeError(f"Build controller interrupted by signal {signum}")


def main():
    lock = (ROOT / "controller.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    attempt = str(int(time.time()))
    done = ROOT / "results" / f"grammar-build-{attempt}.complete"
    report_file = ROOT / "results" / f"grammar-build-{attempt}.json"
    report = {"started": time.time(), "built": False, "restored_original": False}
    guardian, maintenance = None, False
    try:
        run(["sha256sum", "-c", ROOT / "rollback/config-sha256.txt"])
        current = health()
        if current.get("status") != "ok" or current.get("live", {}).get("connections", 0):
            raise RuntimeError("Production is unhealthy or has live traffic; refusing maintenance")
        stop_compiler = f"timeout 30 ssh -o BatchMode=yes -o ConnectTimeout=10 {shlex.quote(WORKER)} 'docker stop -t 5 spark-hybrid-grammar-build >/dev/null 2>&1 || true'"
        recovery = f"{stop_compiler}; sh {shlex.quote(str(ROOT / 'rollback.sh'))}"
        guardian_command = f"sleep 1500; test -f {shlex.quote(str(done))} || {{ {recovery}; }}"
        guardian = subprocess.Popen(["sh", "-c", guardian_command], start_new_session=True,
                                    stdout=(ROOT / "logs/grammar-build-guardian.log").open("a"),
                                    stderr=subprocess.STDOUT)
        maintenance = True
        run(["systemctl", "--user", "stop", "dsv41.service"], 180)
        for result in [run(["docker", "inspect", "-f", "{{.State.Running}}", "dsv41-1017-exl3-tf"], check=False),
                       remote("docker inspect -f '{{.State.Running}}' dsv41-1017-exl3-tf", check=False)]:
            if result.stdout.strip() == "true":
                raise RuntimeError("Original model survived supervisor stop")
        # A real cgroup limit, with GPUs absent; the 1 GiB live-build attempt was correctly OOM-contained.
        command = ("root=$HOME/spark-hybrid-20261031/grammar-build; "
                   "BUILD_MEMORY=4g BUILD_MEMORY_BYTES=4294967296 BUILD_CPUS=2 "
                   "sh \"$root/build_native.sh\" > \"$root/logs/native-build.log\" 2>&1; "
                   "status=$?; cat \"$root/logs/native-build.log\"; exit \"$status\"")
        result = remote(command, timeout=1200)
        (ROOT / "logs" / f"grammar-build-{attempt}.log").write_text(result.stdout)
        report["built"] = True
        report["artifact_sha256"] = remote("sha256sum ~/spark-hybrid-20261031/grammar-build/out/native/bin/tensorfold-native").stdout.strip()
    except Exception as error:
        report["error"] = str(error)
        print("BUILD_FAILED", str(error), flush=True)
    finally:
        if maintenance:
            try:
                remote("docker stop -t 5 spark-hybrid-grammar-build >/dev/null 2>&1 || true", 60, False)
            except Exception as error:
                report["compiler_stop_error"] = str(error)
            restored = run(["sh", ROOT / "rollback.sh"], 480, False)
            report["restored_original"] = restored.returncode == 0
            (ROOT / "logs" / f"grammar-build-{attempt}-restore.log").write_text(restored.stdout)
            if report["restored_original"]:
                report["final_health"] = health()
                run(["sha256sum", "-c", ROOT / "rollback/config-sha256.txt"])
                done.write_text("ORIGINAL_RESTORED\n")
                if guardian:
                    os.killpg(guardian.pid, signal.SIGTERM)
        report["finished"] = time.time()
        write_json(report_file, report)
        print(report, flush=True)


if __name__ == "__main__":
    main()
