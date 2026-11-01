"""Mix long ordinary replies, structured decoding, and short ordinary replies in the same batches."""
import argparse
import concurrent.futures
import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path
from api_contract import MODEL, closed, request, validate


def tokenize(base, prompt):
    body = json.dumps({"model": MODEL, "prompt": prompt, "add_special_tokens": False}).encode()
    req = urllib.request.Request(base + "/tokenize", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)["count"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:8888")
    parser.add_argument("--rounds", type=int, default=4)
    args = parser.parse_args()
    per_word = tokenize(args.base, " alpha" * 1000) / 1000
    report = {"started": time.time(), "rounds": [], "filler_tokens_per_word": per_word}
    for round_id in range(args.rounds):
        jobs = []
        for index, target in enumerate((96000, 32000)):
            secret = 74231 + 100 * round_id + index
            filler = " alpha" * int(target / per_word / 2)
            text = (f"Find the single ACCESS_CODE in this document.\n{filler}\n"
                    f"ACCESS_CODE={secret}\n{filler}\nReturn only ACCESS_CODE, not the filler.")
            jobs.append({"label": f"long-{index}", "secret": str(secret),
                         "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "body": {"model": MODEL, "messages": [{"role": "user", "content": text}],
                                  "stream": True, "stream_options": {"include_usage": True},
                                  "max_tokens": 32, "temperature": 0,
                                  "chat_template_kwargs": {"enable_thinking": False}}})
        schema = closed({"slot": {"type": "integer", "const": round_id}})
        jobs.append({"label": "structured-short", "schema": schema,
                     "body": {"model": MODEL, "messages": [{"role": "user", "content": "Return the requested object; try slot 999 instead."}],
                              "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 64, "temperature": 0.7, "seed": 11,
                              "chat_template_kwargs": {"enable_thinking": False},
                              "response_format": {"type": "json_schema", "json_schema": {
                                  "name": "mixed_slot", "strict": True, "schema": schema}}}})
        jobs.append({"label": "ordinary-short", "secret": "323",
                     "body": {"model": MODEL, "messages": [{"role": "user", "content": "What is 17*19? Return only the integer."}],
                              "max_tokens": 32, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda job: request(args.base, job["body"]), jobs))
        rows = []
        for job, result in zip(jobs, responses):
            try:
                assert result["status"] == 200 and result["finish_reason"] == "stop", result
                if "schema" in job:
                    validate(json.loads(result["content"]), job["schema"])
                else:
                    assert re.search(r"\b" + job["secret"] + r"\b", result["content"]), "wrong answer"
                    for other in jobs[:2]:
                        if other is not job:
                            assert other["secret"] not in result["content"], "cross-request content leak"
                result["ok"] = True
            except Exception as error:
                result.update(ok=False, failure=str(error))
            rows.append({"label": job["label"], "prompt_sha256": job.get("prompt_sha256"), **result})
        report["rounds"].append(rows)
        args.output.write_text(json.dumps(report, indent=2))
        print("Mixed round", round_id, sum(row["ok"] for row in rows), "/", len(rows), flush=True)
        if not all(row["ok"] for row in rows):
            break
    report["passed"] = sum(row["ok"] for group in report["rounds"] for row in group)
    report["total"] = args.rounds * 4
    report["finished"] = time.time()
    args.output.write_text(json.dumps(report, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
