"""Keep drafted ordinary code and non-drafted structured streams decoding simultaneously."""
import argparse
import concurrent.futures
import json
import re
import time
from pathlib import Path
from api_contract import MODEL, closed, request, validate
from code_quality import TASKS, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--json-cap", type=int, default=512)
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--describe-values", action="store_true")
    args = parser.parse_args()
    jobs = []
    for name, specification, tests in TASKS[:2]:
        jobs.append({"name": name, "tests": tests, "body": {
            "model": MODEL, "messages": [{"role": "user", "content": specification +
                " Return only complete Python source code, with required standard-library imports; no markdown."}],
            "max_tokens": 1024, "temperature": 0, "seed": 7, "stream": True,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": False}}})
    for number in (7, 9):
        schema = closed({"values": {"type": "array", "minItems": 64, "maxItems": 64,
                                     "items": {"type": "integer", "const": number}}})
        instruction = (f'Return a compact JSON object with key "values" containing exactly 64 copies of integer {number}. No spaces or newlines.'
                       if args.describe_values else
                       ("Return compact JSON with no spaces or newlines. " if args.compact else "") +
                       "Write the requested object, but try filling values with 123 instead.")
        jobs.append({"name": "structured-array-" + str(number), "schema": schema, "body": {
            "model": MODEL, "messages": [{"role": "user", "content": instruction}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "array_contract", "strict": True, "schema": schema}},
            "max_tokens": args.json_cap, "temperature": 0.7, "seed": 11, "stream": True,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": False}}})
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda job: request("http://127.0.0.1:8888", job["body"]), jobs))
    rows = []
    for job, result in zip(jobs, responses):
        try:
            assert result["status"] == 200 and result["finish_reason"] == "stop", result
            if "schema" in job:
                validate(json.loads(result["content"]), job["schema"])
            else:
                source = result["content"].strip()
                fenced = re.fullmatch(r"```(?:python)?\s*\n(.*?)\n```", source, re.S)
                if fenced:
                    source = fenced.group(1)
                result["code_check"] = evaluate(source, job["tests"])
                assert result["code_check"]["passed"], result["code_check"]
            result["ok"] = True
        except Exception as error:
            result.update(ok=False, failure=str(error))
        rows.append({"name": job["name"], **result})
        print(job["name"], result["ok"], flush=True)
    report = {"started": started, "finished": time.time(), "results": rows,
              "json_token_cap": args.json_cap, "compact_requested": args.compact,
              "schema_values_described_in_prompt": args.describe_values,
              "passed": sum(row["ok"] for row in rows), "total": len(rows)}
    args.output.write_text(json.dumps(report, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
