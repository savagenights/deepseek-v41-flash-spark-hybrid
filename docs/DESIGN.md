# Why this shape

Three candidate stacks were built and measured against the original deployment.
Two were rejected on evidence, not taste.

## Rejected: the whole Jay stack

Same base model, different runtime, quantisation and policy. Warmed prose was ~12%
slower and concurrent throughput ~22% slower. No quality gain that justified it.

## Rejected: the new engine with the original Mia weights

~12% slower on code, ~22% on prose, ~29% on concurrent traffic. Blocking and spin
scheduling tweaks barely moved it. The engine is not the bottleneck we needed to fix.

## Accepted: original engine + narrow grammar backport

The original engine already produced tokens at the speed we wanted. The only missing
capability was constrained decoding. So the change is deliberately the smallest thing
that adds it:

- **Weights untouched.** Same EXL3 checkpoint, same revision.
- **GPU math untouched.** No kernel edits, no quantisation changes, no TP protocol
  changes. The patch adds a host-side mask path, not a new matmul.
- **Ordinary sampling untouched.** Same sampler, same speculative decoding.
- **One rank touched.** Rank 1 runs the original image unmodified.

This is why the speed and quality numbers land where they do: almost nothing that
matters for ordinary tokens changed.

## How structured decoding works

1. `server.zig` validates the request's grammar/schema before any streaming starts.
2. A `Compiler` builds a `Constraint` from the grammar library's C ABI, owned by the
   request. Compilation is mutex-protected because the library is not re-entrant.
3. `lane_host.zig` attaches the matcher to the request and **disables drafts** for it.
4. `families/dsv41/lanes.zig` applies the mask to host logits before each draw. The
   structured path requires a single row and bypasses the device greedy shortcut, so
   no fused kernel can emit an unmasked token.
5. The constraint is freed with the request, on every exit path including cancellation.

Masks are applied to *host* logits because that is the one place every token must
pass through, whatever produced it.

## Fail-closed choices

Rejected outright rather than guessed at:

- malformed grammar or schema
- unknown reasoning-close token
- forced reasoning-budget/loop closure combined with structured output
- forced tool selection combined with a schema

A wrong answer is cheaper to debug than a silently unmasked one.

## Deliberately not backported

Disk sessions, KV sharding, compaction and pruning. Those are newer Mia switches that
depend on engine internals this version does not have. Enabling them by assumption
would be the exact class of mistake this project exists to avoid. The original RAM
prefix cache is kept and works.

## Operational shape

`dsv41-hybrid.service` (user systemd, `Restart=always`, `KillMode=mixed`,
`Conflicts=dsv41.service`) runs `supervise_grammar.py`, which owns `controller.lock`,
re-verifies the qualification manifest on every start, and waits for the head rank to
exit so systemd restarts the pair together. Docker restart policy is `no`, so there is
exactly one supervisor.
