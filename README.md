# DeepSeek-V4.1-Flash on 2x DGX Spark: original-speed hybrid with constrained decoding

A **rank-0 host-side grammar overlay** for the existing
[MiaAI-Lab DeepSeek-V4.1-Flash-1017 TensorFold recipe](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks).

It keeps the installed engine, its five Mia patches, the original EXL3 weights, the
GPU math, the tensor-parallel protocol and the ordinary sampler **exactly as they were**,
and adds one capability: **decoder-enforced structured output** (JSON Schema, regex,
choice, EBNF) on rank 0.

No new base model. No new kernels. No measurable quality or throughput change for
ordinary requests in matched testing. The goal was to add structured decoding
without taking the measured slowdown of a wholesale engine replacement.

## Fresh three-stack comparison (2026-11-04)

Same two Sparks, benchmark script, prompts, seeds and requested concurrency.
Each stack received one discarded full warm-up plus two measured runs; values
below are medians of those two runs. Stack-specific weights and serving settings
were retained, so this compares recipes, not engine code in isolation.

| Recipe | Code tok/s | Prose tok/s | Four-request aggregate tok/s | Benchmark checks | Historical quality score* |
|---|---:|---:|---:|---:|---:|
| Original installed Mia | 100.67 | 73.36 | 170.88 (170.85–170.92) | 13/14 | 97.2%; 766/807 |
| Jay standalone | 90.10 | 63.98 | 84.58 (48.60–120.55) | 14/14 | 96.7%; 763/807 |
| Hybrid | 101.84 | 74.14 | 172.87 (172.73–173.01) | 14/14 | 97.2%; 766/807 |

**Caveats matter:** Jay's aggregate result was unstable; its discarded warm-up
reached 138.03 tok/s. Do not treat 84.58 as a stable capacity estimate. A closing
hybrid repeat measured 97.30 code, 70.82 prose and 167.78 aggregate tok/s, showing
approximately 3–4.5% drift across the session. The opening hybrid's ~1.1% advantage
over Mia is smaller than that drift: **speed preserved, not a proven speedup**.
Mia's sole failed check was `json_schema`; all ordinary checks passed.

*Quality scores are the earlier persisted 807-item runs, **not fresh quality
reruns** during this TPS session. All three had zero request errors. The displayed
score is category-averaged, not raw correct / 807. Mia and hybrid had identical
paired pass/fail outcomes; Jay's raw total was three lower. These benchmark results
do not establish universal model superiority.

Raw runs, discarded warm-ups, runtime image identities, final drift check and
historical quality provenance: [evidence/fresh-comparison-20261104](evidence/fresh-comparison-20261104).
The qualified hybrid was restored and left enabled after testing.

## Earlier matched result (same weights, same prompts, same machine)

| Measurement | Original | Hybrid | Delta |
|---|---:|---:|---:|
| Code decode | 91.06 tok/s | 90.83 tok/s | -0.26% |
| Prose decode | 66.30 tok/s | 65.90 tok/s | -0.60% |
| 4-request aggregate | 154.78 tok/s | 154.45 tok/s | -0.21% |

Across every matched run the spread was **-0.60% to +0.36%**. Cold ~8K TTFT was
3.817 s vs 3.822 s.

### How the standalone recipes compared

These are measurements of the tested, pinned versions on this pair of Sparks,
not claims about every configuration or future release:

| Tested stack | Result relative to the original installed Mia deployment |
|---|---|
| [Original Mia recipe](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks) | Baseline in the table above |
| [Jay's complete recipe](https://github.com/jayleaton/deepseek-v41-tensorfold-spark) | Approximately 12% slower warmed prose and 22% lower concurrent throughput |
| Newer engine with original Mia weights | Approximately 12% slower code, 22% slower prose, 29% lower concurrent throughput |
| This hybrid | Matched ordinary throughput within 0.6% of original Mia; adds constrained decoding |

The Jay recipe and the newer-engine/original-weights experiment are **different
experiments**. Neither is the deployed hybrid. All use the same base model;
runtime, quantization and policy differences are not a model-intelligence upgrade.

### Latest TPS rerun after reboot (2026-11-04)

| Measurement | Earlier original Mia | Latest hybrid | Apparent difference |
|---|---:|---:|---:|
| Code decode | 91.06 tok/s | 102.14 tok/s | +12.2% |
| Prose decode | 66.30 tok/s | 74.29 tok/s | +12.0% |
| 4-request aggregate | 154.78 tok/s | 173.75 tok/s | +12.2% |

**This is not evidence of a 12% hybrid speedup.** The original numbers were
collected before reboot; neither standalone recipe was rerun alongside this
latest test. The reason for the higher post-reboot operating speed has not been
isolated. The matched comparison above remains the basis for the claim that
ordinary speed was preserved. A fresh same-condition baseline is required to
claim an improvement over either standalone recipe.

The latest run passed 14/14 checks; median code/prose TTFT was 139/102 ms.
These are ordinary decoding rates, not constrained-output throughput or
long-prompt prefill rates. Raw result:
[`evidence/tps-20261104-111247.json`](evidence/tps-20261104-111247.json).

Quality, on a persisted 807-item suite (766 raw correct, 97.2% displayed overall):

- **Zero request errors**, both runs.
- **Identical pass/fail on every paired item** - no category gained, none lost.
- All 12 long-context recall checks through 256K passed.

See [docs/MEASUREMENTS.md](docs/MEASUREMENTS.md) for methodology and the caveats
that matter (including what was *not* tested).

## What actually changed

One patch, 12 files, 581 lines: [patches/0001-host-side-grammar-backport.patch](patches/0001-host-side-grammar-backport.patch).

- A dynamic loader plus `Compiler`/`Constraint` wrapper for the TensorFold grammar C ABI
  (`zig/src/core/lanes/grammar.zig`), with compilation serialised behind a mutex.
- Grammar masks applied to **host logits before sampling**, so no draft token can
  bypass them. Structured requests therefore **disable speculative decoding**.
- Request-scoped constraint lifetime through `engine_api.zig`, `lane_host.zig`,
  `server.zig`, `chat.zig`, `stream.zig`, `families/dsv41/lanes.zig`.
- Fail-closed validation: malformed grammars, unknown reasoning-close tokens, and
  unsupported combinations (forced reasoning-budget closure + structured output,
  forced tool choice + schema) are rejected rather than silently ignored.

Rank 1 runs the **unmodified original image**. Only rank 0 needs the overlay.

## Layout

```
patches/        the backport patch and the no-RUN overlay Dockerfile
docs/           runbook, design rationale, measurement method
runtime.py      shared lifecycle: Docker API, health, guarded runs
grammar_runtime.py   start/stop the rank pair, readiness, memory guards
supervise_grammar.py systemd-owned supervisor (restarts on head exit)
verify_grammar.py    fail-closed acceptance gate for a built image
promote_grammar.py   extended contracts, soak, session probes, recovery drill
grammar_rollback.sh  one-command return to the untouched original deployment
docker_clone.py      clone baseline containers via the local Docker API, no secret argv
api_contract.py      20 API/stream/schema tests
extended_contract.py regex, choice, EBNF, JSON-object, rejection cases
mixed_soak.py        long/short/structured concurrency soak
mixed_decode_probe.py simultaneous ordinary code + constrained arrays
session_probe.py     prefix-cache and multi-turn continuation probes
rollback_drill.py    prove rollback works, then restore the qualified hybrid
evidence/            recorded acceptance, rollback and boundary-test results
```

Flat layout on purpose: it mirrors the directory that is actually deployed, so the
file you read is the file that ran.

## Requirements

- Two DGX Sparks cabled directly (ConnectX-7), passwordless SSH from head to worker.
- The original Mia recipe installed and working, with its `scripts/memguard.sh`.
- Zig 0.17.0 for the native build (CPU-only, 4 GiB cgroup is enough).
- A TensorFold build that provides `libtfgrammar.so`. **This repo does not redistribute
  that binary**; take it from your own TensorFold build or the image you already trust.
- The Mia EXL3 weights at revision `45ee5d0fd106c94a3d77f721e09cbb2f50e5a16b`.
  Weights are **not** included here.

Environment overrides (defaults reproduce the reference deployment):

```
SPARK_HYBRID_ROOT   ~/spark-hybrid-20261031
SPARK_WORKER        cell@10.100.40.1
SPARKDASH_QUALITY_URL  required only for the SparkDash quality runner
```

## Reproduce

1. `git clone` the Mia recipe at commit `31eea46`; confirm the five deployed patches.
2. Apply `patches/0001-host-side-grammar-backport.patch` to upstream TensorFold
   `629c524a` plus those five patches; the result is commit `487088ae`.
   Verified: applying the patch to `11737b98` (the exact five-patch baseline)
   reproduces tree `af382d6e`, byte-for-byte identical to the deployed commit.
3. `build_native.sh` (CPU-only container, real cgroup limit), then
   `assemble_grammar.py` to produce the overlay image. `grammar_library_probe.py`
   proves the ABI against the real tokenizer before anything is served.
4. `grammar_trial.py --quality` benchmarks and quality-tests the candidate, and
   **restores the original before promotion**.
5. `verify_grammar.py` refuses to bless an image unless every contract, speed ratio,
   coding check and paired quality item passes.
6. `promote_grammar.py` installs `dsv41-hybrid.service`, disables the original unit,
   and re-checks speed, mixed soak and session behaviour across an induced pair restart.

Full detail, including the exact commands and the recovery semantics:
[docs/HYBRID-RUNBOOK.md](docs/HYBRID-RUNBOOK.md).

## Limits - read these

- **Not a smarter model.** Same weights, same quality, by measurement.
- **Structured output is non-speculative**, so it is slower than ordinary decoding.
- **Token caps still bite.** Two adversarial 64-item array probes spent a 512-token
  budget on legal whitespace and returned `finish_reason: length`; both concurrent
  coding tasks in the same test passed. Compact guidance plus 4,096 tokens completed
  one array and still capped the other. A prompt that described the wanted values
  completed both within 512 tokens. Grammar masks constrain *valid* tokens, they do
  not guarantee *completion* - check the finish reason before parsing.
- **No disk sessions, no KV sharding/compaction/pruning.** Upstream RAM prefix caching
  is retained: 5.20 s cold vs 0.92 s on a 10,240-token repeat, cold again after a
  process restart. That is caching, not persistence.
- **Real OS reboot tested on both hosts.** Automatic startup reached ready 45.76 s
  after head boot, with zero retries; post-boot contracts passed 20/20. Worker booted
  ~43 s earlier. Abrupt power loss and the reverse boot ordering remain untested.
- **1M context not exercised.** 256K was.

## Rollback

```
ssh spark 'sh ~/spark-hybrid-20261031/grammar_rollback.sh'
```

Exercised after promotion: original image and unit restored and verified in 40.31 s,
qualified hybrid back in 37.28 s. Original unit file and local settings are
checksum-verified unchanged, and `dsv41-hybrid.service` declares
`Conflicts=dsv41.service` so the two supervisors cannot start together.

## Licence and provenance

Our scripts and the backport patch are Apache-2.0. The engine is a fork of
[TensorFold](https://github.com/ashhart/TensorFold) 1.0.2 (Apache-2.0) by BertholomusAI;
upstream `LICENSE`, `NOTICE` and third-party notices must travel with any redistribution.
The base recipe and weights belong to MiaAI-Lab. See [NOTICE](NOTICE).

Nothing here grants rights to the model weights or to any prebuilt image.
