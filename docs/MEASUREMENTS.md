# How these numbers were produced

The rule for this project: only compare runs that share weights, prompts, machine,
concurrency and scoring. Anything else is a vibe, not a measurement.

## Speed

Same 807-item workload, same prompts, same seeds, same 4-way concurrency, run against
the original deployment and the candidate on the same two Sparks.

| Run | Code tok/s | Prose tok/s | Aggregate tok/s |
|---|---:|---:|---:|
| Original | 91.064 | 66.300 | 154.784 |
| Hybrid, qualified | 90.912 | 66.145 | 155.347 |
| Hybrid, after recovery | 90.831 | 65.904 | 154.453 |

Ratios recorded by the acceptance gate: code 0.9983, prose 0.9977, mixed 1.0036.
Post-recovery: -0.26%, -0.60%, -0.21%.

Cold ~8K TTFT: 3.817 s original vs 3.822 s hybrid.

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

- Physical power cycle / full machine reboot. Boot wiring and induced pair recovery
  were tested; a real reboot was not.
- 1M context. 256K was exercised.
- Disk-backed sessions, KV sharding, compaction, pruning - not implemented here.
- Imported JPEG/WebP image stack beyond the red PNG API contract test.

## Reproducing

`grammar_trial.py --quality` performs the paired benchmark and full suite and restores
the original before promotion. `verify_grammar.py` refuses to qualify an image unless
every gate passes; its output is the `qualified` block quoted above.
