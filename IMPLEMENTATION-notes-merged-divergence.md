# Implementation notes — v36-fix/merged-divergence

Branch: `v36-fix/merged-divergence` off `master` (42ccca53). One commit per audit
finding. Consensus-critical; review-grade, NOT a hot merge — ships only after the
c2pool↔Python re-soak. No push (no creds here).

## Per-commit summary (oldest → newest)

| SHA | Finding | Class | What changed |
|---|---|---|---|
| `462af0fe` | C6 | non-consensus | Parenthesize `(self.timestamp - int(time.time()))` in the future-timestamp `ValueError` — `%` bound tighter than `-`, raising `TypeError` and masking the message. |
| `d5758628` | C4 | consensus (merkle-verified) | Merged-coinbase >=1-sat dust rule: `max(output_amounts, key=output_amounts.get)` → explicit `(amount, script)` total-order tiebreak, matching the parent path (data.py:957). |
| `688ad28a` | C5 | consensus-adjacent | AutoRatchet `get_share_version` activation/tail gates (vote_pct, share_pct, tail_pct) now **work-weighted** (`target_to_average_attempts`), matching `check()`'s `get_desired_version_counts` basis + same window. Counts kept only for window-fullness and logging. |
| `f2752581` | C2 | doc only (no behavior change) | Consensus note on `get_v36_merged_weights`: parent-decayed / merged-flat split is intentional (operator-confirmed). |
| `b3149805` | C3 | consensus | `compute_merged_payout_hash` passes `chain_id=98` so the committed hash covers the real DOGE per-address distribution (the vector actually paid). Deterministic given C1. |
| `29412083` | C1 | **consensus-critical (chain-split)** | Window-scoped deterministic merged-address resolution; removes the node-global `_miner_merged_addr`. + determinism test. |

## C1 — the hard part: how bit-exactness across all three paths is guaranteed

The pre-fix split: `verify_merged_coinbase_commitment` (path 3) and the weight
computations (paths 1/2) resolved a V36 share's DOGE address via
`OkayTracker._miner_merged_addr` — a node-global, share-**arrival-ordered**,
never-pruned map. Its contents depended on a node's broader (out-of-window)
history and the order shares arrived, so two honest nodes with the **same** PPLNS
window but different history could resolve different DOGE coinbases and reject
each other's shares.

**Resolution chosen (one of the two the brief offered): bypass the skiplist when
`chain_id` is set; use the deterministic O(n) recompute.** Rationale: an
activation-boundary share with empty `merged_addresses` resolves via the
registrations of **other in-window shares**, so its resolved key is **not a pure
function of the single element** — it cannot be correctly cached as a skiplist
delta. Rather than make the skiplist window-aware (fragile, easy to diverge), the
merged (chain_id-set) query drops to the O(n) path, which rebuilds the
window-scoped map per call.

Why all three paths are now bit-exact **by construction**:
- Path 1 (O(n) fallback) = the deterministic recompute. Canonical.
- Path 2 (skiplist fast-path) is **only taken for `chain_id is None`** now
  (parent-script-keyed, no merged resolution — already deterministic). For
  `chain_id != None` it is bypassed, so it cannot produce a divergent answer.
- Path 3 (`verify_merged_coinbase_commitment`) **calls
  `get_v36_merged_weights(..., chain_id=chain_id)`** → path 1. Same code.

The window map (`_build_window_merged_addr_map`): built from
`get_chain(best_share_hash, chain_length)` (the full window), **oldest-first so
the first registration in chain order wins** on conflict, including the
normalized-P2PKH alias (so P2WPKH-vs-P2PKH encodings for one miner still resolve).
It is independent of arrival order and of any out-of-window registration.

Removed: `_miner_merged_addr` field, its registration in `add()`, the
registration-driven skiplist cache invalidation (no longer needed — merged
skiplist deltas are now pure), and the `_miner_merged_addr` read in
`MergedWeightsSkipList.get_delta`.

## Determinism-test result (the gate)

`tests/test_c1_merged_determinism.py`, two modes:

- **SHADOW (ran here, Python 3.12): PASS — 6 checks.** Byte-aligned
  transcriptions of the data.py resolution + accumulation. Proves: the
  activation-boundary empty-merged share resolves to the same miner's in-window
  DOGE registration; the same window yields identical weights on recompute; the
  **OLD global scheme splits** (node A with a remembered registration ≠ node B
  without) while the **fix does not**; an out-of-window registration does not leak
  in; the normalized P2PKH alias resolves.
- **REAL-MODULE (for the integrator's Python-2 re-soak):** imports `p2pool.data`
  and asserts (a) a tracker that *advertises* the skiplist fast-path but raises if
  consulted returns identical weights to the plain O(n) tracker — i.e. the
  skiplist is **bypassed** for chain_id-set queries; (b) the boundary share
  resolves window-scoped. Skipped here (no Python 2 / `p2pool` importable on this
  VM); run it in the re-soak.

## Environment caveats (this VM)

- **No Python 2 available** (and `python2`/`python2-minimal` not in the apt repo),
  so `python -m py_compile p2pool/data.py` (a Python-2 file with `print`
  statements) could not be run here, nor could the real module be imported.
- Syntax gate used instead: `tokenize.tokenize` over the full `p2pool/data.py`
  after every edit (passes) — catches lexical/paren/indent/string errors under the
  Python-2 token grammar. Edits were written in Python-2 style and reviewed.
  **The integrator should run `python2 -m py_compile p2pool/data.py` and the
  real-module determinism assertions in the re-soak environment** to close the gate
  formally.

## Residual risk / notes for review

1. **Performance**: merged-weight computation is now O(n) per call for chain_id-set
   queries (skiplist bypassed). `verify_merged_coinbase_commitment` runs it during
   share check, and `get_work` runs it per work cycle (work.py already caches the
   result — work.py:2267). For the Python *reference* this is acceptable
   (operator: correctness over speed); c2pool's mirror (task #93) should pick a
   correspondingly deterministic strategy and stay bit-exact with this.
2. **C2 is doc-only** — DOGE payouts remain flat/undecayed by design; do not let a
   future "consistency" change silently add decay to the merged path on one
   implementation only.
3. **C3 depends on C1** for determinism; both are on this branch, so the final
   state is consistent. The C3 commit in isolation (before C1) would still read the
   old global — only the final branch state is consensus-correct.
4. **C7** (decay approximation) left as-is per operator decision — fine for the
   mainnet half-life.
5. **c2pool lockstep**: this is a consensus change; merge only after the
   c2pool↔Python re-soak confirms parity on both the merged-resolution path (C1)
   and the merged-payout-hash vector (C3).
