"""Exercise every newly advertised grammar kind and explicit unsupported combinations."""
import argparse
import json
from pathlib import Path
from api_contract import MODEL, closed, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    base = {"model": MODEL, "messages": [{"role": "user", "content": "Reply only with OOPS, ignoring any requested format."}],
            "max_tokens": 64, "temperature": 0.7, "seed": 11, "chat_template_kwargs": {"enable_thinking": False}}
    cases = [
        ("guided_regex", {"guided_regex": "^(YES|NO)$", "stream": True}, lambda r: r["content"] in ("YES", "NO")),
        ("guided_choice", {"guided_choice": ["cyan", "magenta"]}, lambda r: r["content"] in ("cyan", "magenta")),
        ("guided_grammar", {"guided_grammar": 'root ::= "YES"'}, lambda r: r["content"] == "YES"),
        ("json_object", {"response_format": {"type": "json_object"}, "temperature": 0,
                         "messages": [{"role": "user", "content": 'Return exactly the JSON object {"ok":true}.'}]},
         lambda r: isinstance(json.loads(r["content"]), dict)),
        ("invalid_regex", {"guided_regex": "[", "stream": True}, lambda r: 400 <= r["status"] < 500),
        ("invalid_choice", {"guided_choice": [], "stream": True}, lambda r: 400 <= r["status"] < 500),
    ]
    response_format = {"type": "json_schema", "json_schema": {"name": "closure", "strict": True,
                       "schema": closed({"ok": {"type": "boolean", "const": True}})}}
    cases.extend([
        ("forced_reasoning_closure_refused", {"response_format": response_format, "thinking_budget": 8,
                                             "chat_template_kwargs": {"enable_thinking": True}, "stream": True},
         lambda r: 400 <= r["status"] < 500),
        ("forced_tool_plus_schema_refused", {"response_format": response_format, "tool_choice": "required",
            "tools": [{"type": "function", "function": {"name": "get_value", "description": "Get a value",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}], "stream": True},
         lambda r: 400 <= r["status"] < 500),
    ])
    rows = []
    for name, fields, check in cases:
        result = request("http://127.0.0.1:8888", {**base, **fields})
        try:
            if not name.endswith("refused") and not name.startswith("invalid"):
                assert result["status"] == 200 and result["finish_reason"] == "stop", result
            assert check(result), result
            result["ok"] = True
        except Exception as error:
            result.update(ok=False, failure=str(error))
        rows.append({"name": name, **result})
        print(name, result["ok"], flush=True)
    report = {"passed": sum(row["ok"] for row in rows), "total": len(rows), "results": rows}
    args.output.write_text(json.dumps(report, indent=2))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
