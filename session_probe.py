"""Synthetic long-prefix reuse probe, repeatable across a controlled engine restart."""
import argparse
import hashlib
import json
import uuid
from pathlib import Path
from api_contract import MODEL, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--phase", choices=["before", "after"], default="before")
    parser.add_argument("--base", default="http://127.0.0.1:8888")
    args = parser.parse_args()
    args.directory.mkdir(exist_ok=True)
    specification = args.directory/"body.json"
    if args.phase == "before":
        if specification.exists():
            raise RuntimeError("Use a fresh directory for a fresh probe; after reuses the existing body")
        prompt = ("Synthetic cache probe " + uuid.uuid4().hex + ". The secret_code is 742.\n" +
                  "amber cedar river stone cloud meadow\n"*1700 +
                  "\nReturn only the secret_code, with no explanation.")
        body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 16,
                "stream": True, "stream_options": {"include_usage": True},
                "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}
        specification.write_text(json.dumps(body))
    else:
        body = json.loads(specification.read_text())
    rows = []
    for attempt in range(2):
        result = request(args.base, body)
        result["ok"] = result["status"] == 200 and result.get("content", "").strip() == "742"
        rows.append(result)
        print(args.phase, attempt, result["ok"], "ttft", result.get("ttft_s"),
              "usage", result.get("usage"), flush=True)
    # Normal multi-turn continuation must preserve the same reference answer.
    continued = {**body, "messages": body["messages"] + [
        {"role": "assistant", "content": "742"},
        {"role": "user", "content": "Repeat the secret_code only."}]}
    result = request(args.base, continued)
    result["ok"] = result["status"] == 200 and result.get("content", "").strip() == "742"
    rows.append(result)
    report = {"phase": args.phase, "body_sha256": hashlib.sha256(specification.read_bytes()).hexdigest(),
              "results": rows, "passed": sum(row["ok"] for row in rows)}
    (args.directory/(args.phase+".json")).write_text(json.dumps(report, indent=2))
    return 0 if report["passed"] == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
