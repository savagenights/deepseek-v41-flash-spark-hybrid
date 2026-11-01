"""Run SparkDash's real persisted Quality suite and compare its paired baseline."""
import argparse
import json
import math
import os
import signal
import time
import urllib.request
from pathlib import Path

DEFAULT_STAGE = Path.home() / "spark-hybrid-20261031/grammar-qualification"
# SparkDash is site-specific: point this at your own Quality-suite endpoint.
BASE = os.environ.get("SPARKDASH_QUALITY_URL", "")


def api(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def compare(old, new):
    reasons = []
    for key in ("suiteVersion", "scoringVersion", "categories", "longSizes", "longItems", "concurrency"):
        if old["config"].get(key) != new["config"].get(key):
            reasons.append("SparkDash configuration mismatch: " + key)
    old_items = {row["id"]: row for row in old["results"]["items"]}
    new_items = {row["id"]: row for row in new["results"]["items"]}
    if old_items.keys() != new_items.keys():
        reasons.append("SparkDash item sets differ")
    pairs = {}
    for key in old_items.keys() & new_items.keys():
        before, after = old_items[key], new_items[key]
        category = before["category"]
        row = pairs.setdefault(category, {"only_baseline": 0, "only_candidate": 0,
                                          "both_pass": 0, "both_fail": 0, "excluded": 0})
        if before.get("error") or after.get("error"):
            row["excluded"] += 1
        elif before["ok"] and after["ok"]:
            row["both_pass"] += 1
        elif before["ok"]:
            row["only_baseline"] += 1
        elif after["ok"]:
            row["only_candidate"] += 1
        else:
            row["both_fail"] += 1
    for category, row in pairs.items():
        losses, gains = row["only_baseline"], row["only_candidate"]
        count = losses + gains
        p_value = min(1.0, 2 * sum(math.comb(count, i) for i in range(min(losses, gains) + 1)) / 2 ** count)
        row["mcnemar_exact_p"] = p_value
        if losses > gains and p_value < 0.05:
            reasons.append(f"Significant SparkDash regression in {category}: {losses} losses, {gains} gains, p={p_value:.4f}")
    old_pct = old["results"].get("overallPct")
    new_pct = new["results"].get("overallPct")
    if old_pct is None or new_pct is None or new_pct < old_pct - 1.0:
        reasons.append(f"SparkDash overall score >1 percentage point lower or missing: {new_pct} vs {old_pct}")
    old_errors = sum(bool(row.get("error")) for row in old_items.values())
    new_errors = sum(bool(row.get("error")) for row in new_items.values())
    if new_errors > old_errors:
        reasons.append(f"SparkDash request errors increased: {new_errors} vs {old_errors}")
    if new.get("status") != "completed":
        reasons.append("SparkDash did not complete: " + str(new.get("status")))
    return {"reasons": reasons, "paired": pairs, "baseline_overall_pct": old_pct,
            "candidate_overall_pct": new_pct, "baseline_errors": old_errors,
            "candidate_errors": new_errors, "baseline_id": old["benchId"],
            "candidate_id": new["benchId"]}


def interrupted(signum, frame):
    raise RuntimeError(f"Interrupted: {signum}")


def main():
    global BASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--label", default="Hybrid Mia original kernels plus grammar 487088ae")
    parser.add_argument("--base", default=BASE, help="Quality-suite endpoint; defaults to $SPARKDASH_QUALITY_URL")
    args = parser.parse_args()
    if not args.base:
        parser.error("set SPARKDASH_QUALITY_URL or pass --base to your Quality-suite endpoint")
    BASE = args.base
    stage = args.stage
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    old = json.loads((stage / "results/sparkdash-baseline.json").read_text())
    settings = {key: old["config"][key] for key in
                ("port", "modelId", "categories", "longSizes", "longItems", "concurrency")}
    settings["label"] = args.label
    print("Starting SparkDash Quality suite:", settings, flush=True)
    job = None
    try:
        listing = api(BASE + "?port=8888")
        if listing.get("active"):
            raise RuntimeError("Another SparkDash quality run is active; refusing to interfere")
        job = api(BASE, "POST", settings)
        (stage / "results/sparkdash-candidate-start.json").write_text(json.dumps(job, indent=2))
        print("SPARKDASH_BENCH_ID", job["benchId"], flush=True)
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            job = api(BASE + "/" + job["benchId"])
            progress = job.get("progress", {})
            status = {"status": job.get("status"), "progress": progress,
                      "benchId": job["benchId"], "polled_at": time.time()}
            (stage / "results/sparkdash-progress.json").write_text(json.dumps(status, indent=2))
            print(json.dumps(status), flush=True)
            if job.get("status") != "running":
                break
            time.sleep(15)
        else:
            raise RuntimeError("SparkDash exceeded the one-hour quality deadline")
        (stage / "results/sparkdash-candidate.json").write_text(json.dumps(job, indent=2))
        comparison = compare(old, job)
        (stage / "results/sparkdash-comparison.json").write_text(json.dumps(comparison, indent=2))
        print(json.dumps(comparison, indent=2), flush=True)
        return 0 if not comparison["reasons"] else 1
    finally:
        if job and job.get("status") == "running":
            # DELETE this specific job cancels it; it does not delete history.
            cancelled = api(BASE + "/" + job["benchId"], "DELETE")
            (stage / "results/sparkdash-candidate-cancelled.json").write_text(json.dumps(cancelled, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
