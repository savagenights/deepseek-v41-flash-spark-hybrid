"""CPU-only ABI and schema-compiler proof using the actual checkpoint tokenizer."""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
from api_contract import cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = (args.model_dir / "tokenizer.json").read_bytes()
    tokenizer = json.loads(raw)
    config = json.loads((args.model_dir / "config.json").read_text())
    text_config = config.get("text_config", config)
    vocab = text_config["vocab_size"]
    stops = []
    for holder in (config, text_config):
        eos = holder.get("eos_token_id", [])
        for token in eos if isinstance(eos, list) else [eos]:
            if token not in stops:
                stops.append(token)
    if not stops:
        token_config = json.loads((args.model_dir / "tokenizer_config.json").read_text())
        eos = token_config["eos_token"]
        eos = eos["content"] if isinstance(eos, dict) else eos
        stops = [token["id"] for token in tokenizer["added_tokens"] if token["content"] == eos]
    assert stops and all(0 <= token < vocab for token in stops)
    lib = c.CDLL(str(args.library))
    lib.tfg_open.argtypes = [c.c_char_p, c.c_size_t, c.c_int, c.POINTER(c.c_int32), c.c_int, c.c_char_p, c.c_size_t]
    lib.tfg_open.restype = c.c_void_p
    lib.tfg_words.argtypes, lib.tfg_words.restype = [c.c_void_p], c.c_int
    lib.tfg_compile.argtypes = [c.c_void_p, c.c_int, c.c_char_p, c.c_size_t, c.c_char_p, c.c_size_t]
    lib.tfg_compile.restype = c.c_void_p
    lib.tfg_free.argtypes = [c.c_void_p]
    lib.tfg_close.argtypes = [c.c_void_p]
    error = c.create_string_buffer(512)
    compiler = lib.tfg_open(raw, len(raw), vocab, (c.c_int32 * len(stops))(*stops), len(stops), error, len(error))
    assert compiler, error.value.decode()
    report = {"library_sha256": hashlib.sha256(args.library.read_bytes()).hexdigest(),
              "tokenizer_sha256": hashlib.sha256(raw).hexdigest(), "vocab": vocab, "stops": stops,
              "words": lib.tfg_words(compiler), "schemas": []}
    try:
        assert report["words"] == (vocab + 31) // 32
        for case in cases()[::2][:6]:
            schema = json.dumps(case["schema"]).encode()
            compiled = lib.tfg_compile(compiler, 1, schema, len(schema), error, len(error))
            report["schemas"].append({"name": case["name"], "ok": bool(compiled), "error": error.value.decode()})
            assert compiled, report["schemas"][-1]
            lib.tfg_free(compiled)
        invalid = b'{"type":"not_a_json_type"}'
        compiled = lib.tfg_compile(compiler, 1, invalid, len(invalid), error, len(error))
        report["invalid_rejected"] = not compiled
        if compiled:
            lib.tfg_free(compiled)
        assert report["invalid_rejected"], "Invalid schema was silently accepted"
        report["ok"] = True
    finally:
        lib.tfg_close(compiler)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
