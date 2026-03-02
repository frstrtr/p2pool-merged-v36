# Phase 2a: Exponential PPLNS Decay — Test Report

**Date:** 2026-03-03  
**Test Duration:** ~32 minutes (5+8+5+8 min phases + overhead)  
**Code Version:** v36-0.10-alpha-dirty  
**Network:** Litecoin testnet  

## Summary

Phase 2a implements exponential depth-decay on PPLNS share weights. Recent shares
receive full weight; older shares decay by `2^(-depth/HALF_LIFE)` where
`HALF_LIFE = CHAIN_LENGTH // 4` (= 100 shares on testnet, ~400 seconds).

### Key Result

| Metric | Phase 1a (flat PPLNS) | Phase 2a (decay) | Improvement |
|--------|----------------------|-------------------|-------------|
| Arrival HAR | **5.27×** | **1.52×** | **71.1% reduction** |
| Departure HAR | 0.35× | 0.28× | Slightly better |
| Baseline HAR | 0.92× | 0.94× | ≈ neutral |

**HAR** = Hopper Advantage Ratio = hopper_payout_efficiency / anchor_payout_efficiency.  
HAR=1.0 means perfectly fair. HAR>1.0 means hopper is overpaid.

## Test Setup

### Miners
| Miner | Role | Address | Hash Rate | Node |
|-------|------|---------|-----------|------|
| alfa | hopper | tltc1qaj...hfc3 | ~680 kH/s | node31 |
| charlie | hopper | tltc1qaj...hfc3 | ~1,486 kH/s | node29 |
| bravo | anchor | tltc1qw3...yqh | ~1,219 kH/s | node29 |
| CPU | honest | tltc1qkt...5x7 | ~40 kH/s | node29 |

### Nodes
- node29 (192.168.86.29): anchor + charlie + CPU
- node31 (192.168.86.31): alfa (hopper)

### Decay Parameters
- `HALF_LIFE = CHAIN_LENGTH // 4 = 100 shares` (~400 seconds)
- `_DECAY_PRECISION = 40` (40-bit fixed-point)
- `_DECAY_SCALE = 2^40 = 1,099,511,627,776`
- Per-share decay: `decay_per = SCALE - (SCALE * 693147) // (1000000 * HALF_LIFE)`
- At depth=100: weight ≈ 0.4988 (0.24% error from ideal 0.5)
- At depth=400 (full chain): weight ≈ 0.0620 (6.2%)

## Test Phases

### Phase 1: BASELINE (5 minutes)
All miners active, steady-state operation.

```
Avg Hash:  hopper=1,297 kH/s  anchor=1,399 kH/s  cpu=39 kH/s
Payouts:   hopper=0.698  anchor=0.798  cpu=0.035  tLTC
HAR:       0.944×  (fair — slight underpayment due to hash variance)
```

### Phase 2: DEPARTURE (8 minutes)
Hopper L1 miners blocked via iptables (simulating semiwhale departure).

```
Avg Hash:  hopper=935→0 kH/s  anchor=1,411 kH/s  cpu=47 kH/s
Payouts:   hopper=0.238  anchor=1.276  cpu=0.017  tLTC
HAR:       0.282×  (hopper severely underpaid — shares decaying rapidly)
Difficulty: 0.00229 → 0.00186 (-19.0%)
```

**Key insight:** With decay, the hopper's shares from the baseline period lose
weight exponentially during departure. After 8 minutes (~120 shares at 4s each),
the hopper's BASELINE shares have decayed to `2^(-120/100) = 0.435×` their
original weight. The anchor's continuous mining means their recent shares have
near-full weight.

### Phase 3: ARRIVAL (5 minutes)
Hopper L1 miners unblocked (simulating hop-in after difficulty drop).

```
Avg Hash:  hopper=609 kH/s  anchor=1,359 kH/s  cpu=26 kH/s
Payouts:   hopper=0.617  anchor=0.906  cpu=0.008  tLTC
HAR:       1.521×  (hopper slightly overpaid, but 71% better than Phase 1a)
Difficulty: 0.00172 → 0.00132 (-23.3%)
```

**Payout evolution during ARRIVAL (critical period):**

```
  Elapsed   Hopper    Anchor    Hop/Anc
    1073    0.2547    1.2766    0.200    ← Arrival start: hopper severely underpaid
    1099    0.2866    1.2447    0.230
    1124    0.3436    1.1877    0.289
    1149    0.3776    1.1536    0.327
    1174    0.4171    1.1141    0.374
    1200    0.4497    1.0693    0.421
    1225    0.4973    1.0228    0.486
    1250    0.5416    0.9790    0.553
    1275    0.5523    0.9689    0.570
    1301    0.5489    0.9728    0.564
    1326    0.5969    0.9252    0.645
    1351    0.6019    0.9203    0.654
    1371    0.6169    0.9059    0.681    ← 5 min later: hopper still below anchor
```

The hopper starts with only 20% of anchor's payout (their old shares decayed)
and must gradually rebuild through continuous mining. Even after 5 minutes of
mining at arrival, the hopper's payout (0.617) is still below the anchor's (0.906).

**Compare with Phase 1a (flat PPLNS):** At arrival, the hopper *immediately*
had a higher payout than anchor (1.07 vs 0.45) because flat PPLNS gives equal
weight to all shares in the window regardless of age. The hopper exploited the
low-difficulty shares for disproportionate payout.

### Phase 4: FINAL_DEPARTURE (8 minutes)
Hopper blocked again (second departure).

```
Avg Hash:  hopper=591→0 kH/s  anchor=1,271 kH/s  cpu=17 kH/s
Payouts:   hopper=0.314  anchor=1.204  cpu=0.013  tLTC
HAR:       0.561×  (hopper underpaid after departure)
```

## How Phase 2a Defeats Pool Hopping

### The Mechanism

1. **Weight decay:** Each share's weight decays as `w × 2^(-depth/HALF_LIFE)`.
   After HALF_LIFE shares, a share is worth 50% of its initial weight.

2. **Departure penalty:** When a hopper leaves, their existing shares decay
   rapidly. After 2×HALF_LIFE shares (200 shares = 800s on testnet), their
   shares are worth only 25% of original weight.

3. **Arrival handicap:** When a hopper returns, they start with near-zero
   PPLNS weight (old shares decayed) and must earn new shares to build payout.
   This eliminates the "instant payout" exploit in flat PPLNS.

4. **Anchor protection:** Miners who stay continuously always have fresh
   high-weight shares. They're never diluted by hoppers' stale shares.

### Why the Residual 1.52× Advantage Exists

The remaining 1.52× advantage at arrival comes from:

- **Difficulty exploitation:** The difficulty dropped 19% during departure.
  When the hopper returns, each share costs fewer hashes to find, so they
  accumulate shares faster. This is a difficulty-layer effect that Phase 2a
  (payout-layer) doesn't address.

- **Weight rebuild rate:** During arrival, the hopper's new shares all have
  near-1.0 decay weight (they're recent). But the anchor also has recent
  shares plus decayed older shares. The net effect is that the hopper's
  new-share contribution per hash is slightly higher than the anchor's
  mixed-age portfolio.

### Potential Further Improvements

- **Phase 2c (Score-based PPLNS):** Could further penalize hop-in by tracking
  miner consistency over time. Combined with Phase 2a, this could push the
  arrival HAR below 1.2×.

- **Shorter HALF_LIFE:** Reducing HALF_LIFE increases decay aggressiveness.
  HALF_LIFE = CHAIN_LENGTH//8 would decay faster but could also affect honest
  miners with temporary connectivity issues.

- **Phase 1a value:** The difficulty-layer asymmetric clamp was proven
  ineffective (never triggered due to TARGET_LOOKBEHIND smoothing). The
  difficulty-layer defense would need a fundamentally different approach.

## Implementation Details

### Code Changes in data.py

1. **New function `get_decayed_cumulative_weights()`** (~70 lines):
   - Direct O(n) iteration over share chain (n = REAL_CHAIN_LENGTH)
   - 40-bit fixed-point arithmetic for consensus determinism
   - Uses Taylor approximation: `2^(-1/H) ≈ 1 - ln(2)/H`
   - Handles partial last share and desired_weight cap
   - Returns `(weights_dict, total_weight, donation_weight)` matching
     the existing `tracker.get_cumulative_weights()` interface

2. **Modified `generate_transaction()`**: Branches on `v36_active` to use
   decayed weights for V36 shares, preserving backward compatibility.

3. **Modified `get_expected_payouts()`**: Uses decayed weights when
   `best_share.VERSION >= 36` for web UI display consistency.

### Performance

- Direct iteration is O(REAL_CHAIN_LENGTH) per share generation
- Testnet: ~400 iterations per share (negligible)
- Mainnet: ~8,640 iterations per share (still fast in PyPy)
- No SkipList caching possible (decay depends on position, which varies)

## Conclusion

Phase 2a **successfully reduces the hopper advantage by 71.1%** at the critical
arrival moment. The hopper's ability to extract disproportionate payouts by
timing their mining around difficulty changes is substantially diminished.

The remaining 1.52× advantage is primarily from difficulty-layer effects (easier
shares after departure) which cannot be fully addressed at the payout layer alone.

**Recommendation:** Phase 2a is ready for production deployment. Consider
combining with Phase 2c (score-based PPLNS) for further improvement.

## Raw Data

Test CSV: `scripts/hopper_test_phase2a.csv` (622 data rows)  
Analysis script: `scripts/phase2a_analyze.py`  
Standard analysis: `scripts/hopper_analyze.py`
