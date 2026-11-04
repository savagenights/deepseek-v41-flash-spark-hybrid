# How these numbers were produced

For an engine-only comparison, hold weights, prompts, machine, concurrency and
scoring fixed. Recipe-level comparisons deliberately retain each recipe's weights
and policy, so they cannot isolate the engine as the cause of a difference.

## Speed

The bounded 14-check `ab_benchmark.py` harness measures speed, including three
code requests, three prose requests and a four-request simultaneous batch. Same
prompts, seeds and harness were used on the same two Sparks. This speed harness
is separate from the 807-item SparkDash quality suite.

| Run | Code tok/s | Prose tok/s | Aggregate tok/s |
|---|---:|---:|---:|
| Original | 91.064 | 66.300 | 154.784 |
| Hybrid, qualified | 90.912 | 66.145 | 155.347 |
| Hybrid, after recovery | 90.831 | 65.904 | 154.453 |

Ratios recorded by the acceptance gate: code 0.9983, prose 0.9977, mixed 1.0036.
Post-recovery: -0.26%, -0.60%, -0.21%.

Cold ~8K TTFT: 3.817 s original vs 3.822 s hybrid.

### Fresh three-recipe rerun, 2026-11-04

Order: hybrid, original installed Mia, Jay standalone, then hybrid again.
Each stack received one discarded full-harness warm-up and two measured runs;
the closing hybrid check received one warm-up and one measured run. The script
SHA-256 and exact image identities are in `evidence/fresh-comparison-20261104/report.json`.
Recipe weights/settings were retained. No engine, checkpoint or safety-floor changes
were made to improve a recipe's result. Raw failures and warm-ups are retained.

| Stack | Median code | Median prose | Median aggregate | Aggregate measured range |
|---|---:|---:|---:|---:|
| Mia | 100.67 | 73.36 | 170.88 | 170.85–170.92 |
| Jay | 90.10 | 63.98 | 84.58 | 48.60–120.55 |
| Hybrid opening | 101.84 | 74.14 | 172.87 | 172.73–173.01 |
| Hybrid closing (one run) | 97.30 | 70.82 | 167.78 | — |

Jay's warm-up aggregate was 138.03 tok/s. Measured single-stream rates were stable,
but concurrent TTFT/wall times varied sharply; the cause was not isolated. Report
this as unstable concurrency, not a robust 84.58 tok/s capacity ceiling. The closing
hybrid check shows session drift; the ~1.1% opening hybrid/Mia difference is within
that drift and is not a proven speedup. No temperature/power-state equivalence or
randomized/counterbalanced experimental order is claimed.

Mia passed 13/14 (`json_schema` unsupported); Jay and hybrid passed 14/14 each.
Fresh full quality was not rerun: historical displayed scores are Mia 97.2%
(766/807), Jay 96.7% (763/807), hybrid 97.2% (766/807), all zero errors. Dates,
category scores and benchmark IDs are preserved in `historical-quality-summary.json`.
Hybrid was restored active/enabled; original settings checksums remained unchanged.

### One honest correction

A warm hybrid prefill repeat measured 1.134 s, but it **restored 6,144 cached prompt
tokens**. That is a cache hit, not a faster kernel. Do not quote it as a prefill
speedup.

## Quality

A persisted SparkDash suite: 807 items, 766 raw correct, 97.2% displayed overall,
concurrency 4, 12 long-context recall items up to 256K.

- Original: 766/807, 0 request errors.
- Hybrid: 766/807, 0 request errors.
- **Every paired item had the same pass/fail outcome.** No category moved.

That is the claim being made: no regression. It is not a claim of improvement.

## Contracts and soak

- 43/43 host lane tests, 6/6 Docker safety tests.
- 6/6 grammar ABI compilations against the real tokenizer, invalid input rejected.
- 20/20 API contracts, 8/8 extended contracts.
- 5/5 generated-code checks.
- 16/16 mixed long/short/structured soak across four rounds (~96K and ~32K prompts).
- 3/3 prefix and multi-turn probes before an induced head exit, 3/3 after.
- Both ranks replaced automatically after that exit; healthy again in 43.47 s.

## The boundary test that did not pass, and why it is kept

`mixed_decode_probe.py` runs ordinary code generation for two tasks concurrently with
two constrained 64-element JSON arrays.

| Run | Result | Detail |
|---|---|---|
| 512-token cap, adversarial prompt | 2/4 | both coding passed; both arrays hit `finish_reason: length` on legal whitespace |
| 4,096-token cap, compact guidance | 3/4 | one array completed; one still capped |
| 512-token cap, prompt describes wanted values | 4/4 | both arrays completed |

The engine behaved correctly: the grammar allowed whitespace, the model emitted a lot
of it, and the cap was reached. Nothing was unmasked, nothing leaked between requests.
The lesson is a client-side one - **check `finish_reason` before parsing**, describe the
desired content, and leave output headroom.

The failed records are in `evidence/` on purpose. A benchmark suite that only keeps its
passing runs is decoration.

## What was not tested

- Abrupt power loss and reverse boot ordering. A real OS reboot of both hosts was
  tested on 2026-11-04: automatic ready 45.76 s after head boot, zero retries,
  worker booted ~43 s earlier. Post-boot 20/20 contracts, 3/3 session checks and
  14/14 benchmark checks passed. Observed code/prose/aggregate rates were
  102.04/74.09/172.24 tok/s; no fresh original baseline was run, so these are not
  claimed as a hybrid speedup.
- 1M context. 256K was exercised.
- Disk-backed sessions, KV sharding, compaction, pruning - not implemented here.
- Imported JPEG/WebP image stack beyond the red PNG API contract test.

## Reproducing

`grammar_trial.py --quality` performs the paired benchmark and full suite and restores
the original before promotion. `verify_grammar.py` refuses to qualify an image unless
every gate passes; its output is the `qualified` block quoted above.
