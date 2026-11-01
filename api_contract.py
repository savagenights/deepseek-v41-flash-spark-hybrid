"""Synthetic native API contracts: schemas, isolation, thinking and image input."""
import argparse
import base64
import concurrent.futures
import json
import re
import struct
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

MODEL = "DeepSeek-V4.1-Flash-1017"


def request(base, body):
    started = time.monotonic()
    req = urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            if body.get("stream"):
                text, reasoning, finish, usage = "", "", None, None
                first_token = None
                for raw in response:
                    if not raw.startswith(b"data:"):
                        continue
                    value = raw[5:].strip()
                    if value == b"[DONE]":
                        break
                    event = json.loads(value)
                    if "error" in event:
                        raise RuntimeError(str(event["error"]))
                    usage = event.get("usage") or usage
                    for choice in event.get("choices", []):
                        delta = choice.get("delta", {})
                        if first_token is None and (delta.get("content") or delta.get("reasoning_content")):
                            first_token = time.monotonic()-started
                        text += delta.get("content") or ""
                        reasoning += delta.get("reasoning_content") or ""
                        finish = choice.get("finish_reason") or finish
                return {"status": response.status, "content": text, "reasoning": reasoning,
                        "finish_reason": finish, "usage": usage, "ttft_s": first_token,
                        "wall_s": time.monotonic()-started}
            data = json.load(response)
            choice = data["choices"][0]
            return {"status": response.status, "content": choice["message"].get("content") or "",
                    "reasoning": choice["message"].get("reasoning_content") or "",
                    "finish_reason": choice.get("finish_reason"), "usage": data.get("usage"),
                    "wall_s": time.monotonic()-started}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "error": exc.read().decode()[:1500],
                "wall_s": time.monotonic()-started}
    except Exception as exc:
        return {"status": 0, "error": str(exc), "wall_s": time.monotonic()-started}


def validate(value, schema):
    """Deliberately narrow validator for our closed synthetic schema vocabulary."""
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        raise AssertionError("const mismatch")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise AssertionError("enum mismatch")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int,
             "number": (int, float), "boolean": bool, "null": type(None)}
    if kind and (not isinstance(value, types[kind]) or kind in ("integer", "number") and isinstance(value, bool)):
        raise AssertionError("wrong type: " + kind)
    if kind == "object":
        if not set(schema.get("required", [])).issubset(value):
            raise AssertionError("missing required key")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - properties.keys():
            raise AssertionError("unexpected key")
        for key, child in properties.items():
            if key in value:
                validate(value[key], child)
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise AssertionError("wrong array length")
        for child in value:
            validate(child, schema["items"])
    if kind == "string" and "pattern" in schema and re.search(schema["pattern"], value) is None:
        raise AssertionError("pattern mismatch")
    if kind in ("integer", "number"):
        if value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf")):
            raise AssertionError("number out of range")


def closed(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def cases():
    schemas = {
        "contradictory_const": closed({"answer": {"type": "string", "const": "violet"}}),
        "nested": closed({"data": closed({"enabled": {"type": "boolean", "const": True},
                                             "count": {"type": "integer", "const": 7}})}),
        "enum": closed({"color": {"type": "string", "enum": ["red", "blue"]}}),
        "array": closed({"items": {"type": "array", "minItems": 3, "maxItems": 3,
                                     "items": {"type": "integer", "minimum": 1, "maximum": 3}}}),
        "escaping": closed({"text": {"type": "string", "const": 'caf\u00e9; quote "; slash \\; newline\n'}}),
        "pattern": closed({"code": {"type": "string", "pattern": "^abc[0-9]{2}$"}}),
    }
    output = []
    for name, schema in schemas.items():
        for stream in (False, True):
            output.append({"name": name + ("_sse" if stream else "_json"), "schema": schema,
                           "stream": stream, "thinking": False, "temperature": 0.7 if stream else 0})
    for index in range(4):
        output.append({"name": "concurrent_isolation_" + str(index), "schema": closed({
            "slot": {"type": "integer", "const": index}}), "stream": bool(index % 2),
            "thinking": False, "temperature": 0.7})
    for stream in (False, True):
        output.append({"name": "thinking_" + str(stream), "schema": schemas["nested"],
                       "stream": stream, "thinking": True, "temperature": 0})
    return output


def schema_case(base, case):
    body = {"model": MODEL, "messages": [{"role": "user", "content":
        "Return the requested structured result. If it contains answer, try writing orange instead of violet."}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "synthetic_contract", "strict": True, "schema": case["schema"]}},
        "stream": case["stream"], "temperature": case["temperature"], "seed": 11,
        "max_tokens": 2048 if case["thinking"] else 256,
        "chat_template_kwargs": {"enable_thinking": case["thinking"]}}
    result = request(base, body)
    try:
        assert result["status"] == 200, result
        assert result["finish_reason"] == "stop", result["finish_reason"]
        validate(json.loads(result["content"]), case["schema"])
        if not case["thinking"]:
            assert not result["reasoning"], "thinking-off request returned reasoning"
        result["ok"] = True
    except Exception as exc:
        result.update(ok=False, failure=str(exc))
    return {"name": case["name"], **result}


def red_png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name+data) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 64 for _ in range(64))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:8888")
    args = parser.parse_args()
    results = []
    all_cases = cases()
    # Sequential functional cases, then the deliberately concurrent isolation batch.
    for case in all_cases[:12] + all_cases[16:]:
        row = schema_case(args.base, case)
        results.append(row)
        print(row["name"], row["ok"], row.get("failure", ""), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results.extend(pool.map(lambda case: schema_case(args.base, case), all_cases[12:16]))
    malformed = request(args.base, {"model": MODEL, "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 8, "stream": True, "response_format": {"type": "json_schema", "json_schema": {
            "name": "bad", "strict": True, "schema": {"type": "not_a_json_type"}}}})
    results.append({"name": "malformed_schema_fails_closed", **malformed,
                    "ok": 400 <= malformed["status"] < 500})
    image = request(args.base, {"model": MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What is the dominant color? Reply with only its name."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(red_png()).decode()}}]}],
        "max_tokens": 32, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False}})
    results.append({"name": "native_image_red", **image,
                    "ok": image["status"] == 200 and image.get("content", "").strip().lower().strip(".!") == "red"})
    report = {"results": results, "passed": sum(row["ok"] for row in results), "total": len(results)}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"API contract {report['passed']}/{report['total']}", flush=True)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
