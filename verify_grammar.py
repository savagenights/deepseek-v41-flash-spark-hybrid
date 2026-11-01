"""Fail-closed acceptance checks shared by promotion and the reboot-time supervisor."""
import json
from runtime import ROOT
from grammar_sparkdash_quality import compare

STAGE = ROOT / "grammar-qualification"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_qualified():
    results = STAGE / "results"
    manifest = json.loads((ROOT / "grammar-build/image-manifest.json").read_text())
    tested = json.loads((results / "qualified-image-manifest.json").read_text())
    report = json.loads((results / "trial.json").read_text())
    require(report.get("qualified") and report.get("restored_original"), "GPU qualification has not finished successfully")
    require(manifest == tested, "Runtime manifest differs from the one actually GPU-qualified")
    require(report.get("image") == manifest["image"], "Qualified image tag differs")
    require(report.get("source_commit") == manifest["provenance"]["source_commit"], "Qualified source differs")
    require(report.get("environment_changed_keys") == ["TF_HYBRID_GRAMMAR"], "Qualification changed unrelated settings")
    require(not report["performance"]["reasons"], "Performance gate failed")
    require(report["api"]["passed"] == report["api"]["total"] == 20, "API contract gate failed")
    require(report["coding"]["passed"] == report["coding"]["total"] == 5, "Coding gate failed")
    baseline = json.loads((results / "sparkdash-baseline.json").read_text())
    candidate = json.loads((results / "sparkdash-candidate.json").read_text())
    require(candidate.get("status") == "completed", "Full persisted quality suite did not complete")
    require(len(candidate["results"]["items"]) == len(baseline["results"]["items"]) == 807,
            "Full 807-item quality evidence is missing")
    comparison = compare(baseline, candidate)
    require(not comparison["reasons"], "Persisted quality comparison failed")
    require(candidate["benchId"] == report["sparkdash"]["candidate_id"], "Quality history identity differs")
    return manifest, report, comparison


if __name__ == "__main__":
    manifest, report, quality = load_qualified()
    print(json.dumps({"qualified": True, "image": manifest["image"], "image_id": manifest["image_id"],
                      "performance_ratios": report["performance"]["ratios"],
                      "quality_bench_id": quality["candidate_id"],
                      "quality_overall_pct": quality["candidate_overall_pct"]}, indent=2))
