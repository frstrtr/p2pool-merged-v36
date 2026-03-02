# Phase 1a Asymmetric Difficulty Clamp — Test Report

**Date:** 2026-03-03  
**Test Duration:** 34.3 minutes (4 phases)  
**Network:** LTC testnet (SHARE_PERIOD=4s, CHAIN_LENGTH=400, TARGET_LOOKBEHIND=200)  
**Nodes:** node29 (.29), node31 (.31) — both running Phase 1a code  
**Data File:** `hopper_test_20260303_002015.csv`

---

## 1. Test Setup

### Miners

| Miner   | Role    | Type | Hash Rate | Node   | Address |
|---------|---------|------|-----------|--------|---------|
| bravo   | Anchor  | L1   | ~1.5 GH/s | node29 | `tltc1qw3nfd6xwv8ecwz0xq4expjgdlnsv8qmwvxqyqh` |
| alfa    | Hopper  | L1   | ~1.5 GH/s | node31 | `tltc1qaj0tyr6zckzavhq984kzz6p96djsvcv9wahfc3` |
| charlie | Hopper  | L1   | ~1.5 GH/s | node29 | `tltc1qaj0tyr6zckzavhq984kzz6p96djsvcv9wahfc3` |
| cpu     | Honest  | CPU  | ~25 kH/s  | node29 | `tltc1qktdaszj95rqhzw92jxgrpv295kzs6nct3k45x7` |

- **Hopper control method:** iptables bidirectional REJECT (INPUT+OUTPUT, `--reject-with tcp-reset`) on both p2pool nodes
- **Anchor:** Always connected throughout all phases
- **Hoppers depart:** ~67% total hash rate removed (2 of 3 L1 miners)

### Phases

| Phase           | Duration | Description |
|-----------------|----------|-------------|
| BASELINE        | 5 min    | All miners active, establish equilibrium |
| DEPARTURE       | 8 min    | Hoppers blocked via iptables |
| ARRIVAL         | 5 min    | Hoppers unblocked, reconnect |
| FINAL_DEPARTURE | 8 min    | Hoppers blocked again |

---

## 2. Phase Results Summary

### Difficulty Trajectory (node29)

| Phase           | Start Diff | End Diff | Change  | Duration |
|-----------------|-----------|----------|---------|----------|
| BASELINE        | 0.00229   | 0.00349  | +52.4%  | 297s     |
| DEPARTURE       | 0.00348   | 0.00255  | −26.7%  | 480s     |
| ARRIVAL         | 0.00247   | 0.00229  | −7.1%   | 298s     |
| FINAL_DEPARTURE | 0.00278   | 0.00231  | −16.8%  | 480s     |

### Payout Trajectory (node29, LTC)

| Phase           | Hopper Start | Hopper End | Anchor Start | Anchor End |
|-----------------|-------------|-----------|-------------|-----------|
| BASELINE        | 1.083       | 0.957     | 0.414       | 0.518     |
| DEPARTURE       | 0.921       | 0.173     | 0.554       | 1.320     |
| ARRIVAL         | 0.035       | 0.949     | 1.440       | 0.582     |
| FINAL_DEPARTURE | 0.871       | 0.104     | 0.644       | 1.427     |

---

## 3. Per-Share Difficulty Step Analysis

### DEPARTURE Phase (42 unique difficulty steps)

| Metric          | Drops (28) | Rises (14) |
|-----------------|-----------|-----------|
| Average change  | −1.22%    | +0.25%    |
| Max change      | −6.10%    | +0.81%    |
| Min change      | −0.08%    | +0.03%    |

### FINAL_DEPARTURE Phase (52 unique difficulty steps)

| Metric          | Drops (31) | Rises (21) |
|-----------------|-----------|-----------|
| Average change  | −0.90%    | +0.46%    |
| Max change      | −5.37%    | +1.58%    |

### ARRIVAL Phase (57 unique difficulty steps)

| Metric          | Drops (31) | Rises (26) |
|-----------------|-----------|-----------|
| Average change  | −0.27%    | +0.24%    |
| Max change      | −1.60%    | +0.79%    |

---

## 4. Critical Finding: The Asymmetric Clamp Was Never Triggered

### The Problem

The Phase 1a asymmetric clamp code (in `p2pool/data.py` lines 735–744) triggers when:

```python
if pre_target > previous_share.max_target * 3 // 2:
    # Extreme ratio (>1.5×): allow target up to 167% = ~40% diff drop
    clamp_hi = previous_share.max_target * 5 // 3
else:
    # Normal adjustment: ±10%
    clamp_hi = previous_share.max_target * 11 // 10
```

The asymmetric path activates when `pre_target` (the unclamped difficulty target calculated from the rolling window) exceeds 1.5× the previous share's `max_target`. This corresponds to a single share wanting to drop difficulty by more than ~33%.

### Why It Never Triggers

`pre_target` is computed from `get_pool_attempts_per_second()`, which uses a **200-share rolling window** (`TARGET_LOOKBEHIND=200`). Each new share shifts the window by only 1 position out of 200.

When 67% of hash rate departs:
- Previous share interval: ~4s
- New share interval: ~12s (3× slower)
- First new share extends window by 8s: `808s / 800s = 1.01×` target rise
- After 10 new shares: `880s / 800s = 1.10×` target rise  
- After 50 new shares: window is mostly slow shares, but `max_target` has already tracked upward

**The maximum per-share target change observed was +6.10% (= 6.10% difficulty drop), well below the 50% threshold required to activate the asymmetric branch.**

### Theoretical Analysis — Hash Departure vs. Clamp Activation

| Departure % | Remaining Hash | Max Per-Share Target Ratio | Triggers 1.5×? |
|-------------|---------------|---------------------------|-----------------|
| 50%         | 50%           | 1.005×                    | **NO**          |
| 67%         | 33%           | 1.010×                    | **NO**          |
| 75%         | 25%           | 1.015×                    | **NO**          |
| 80%         | 20%           | 1.020×                    | **NO**          |
| 90%         | 10%           | 1.045×                    | **NO**          |
| 95%         | 5%            | 1.095×                    | **NO**          |
| **99%**     | **1%**        | **2.545×**                | **YES** (share 2) |

**Only when 99%+ of hash rate departs simultaneously does the asymmetric clamp activate.** This is an extreme edge case that virtually never occurs in practice.

### Root Cause

The `TARGET_LOOKBEHIND=200` rolling window acts as a massive low-pass filter. Each share can only move the difficulty by approximately `1/200 = 0.5%` of the intended change. The symmetric ±10% clamp is never even reached for typical hash rate fluctuations — the natural per-share change is always well within ±10%.

**The Phase 1a asymmetric clamp is effectively dead code for any realistic scenario.**

---

## 5. Hopper Exploitation Analysis

Despite the asymmetric clamp being inert, the test reveals the fundamental hop-on vulnerability:

### Payout Efficiency (LTC per kH/s per minute)

| Phase           | Hopper Eff | Anchor Eff | Hopper/Anchor Ratio |
|-----------------|-----------|-----------|-------------------|
| BASELINE        | 277.96    | 96.98     | 2.87× (has 2 miners) |
| DEPARTURE       | 34.31     | 125.18    | 0.27× (blocked)   |
| **ARRIVAL**     | **434.49**| **82.51** | **5.27×**          |
| FINAL_DEPARTURE | 30.65     | 115.13    | 0.27× (blocked)   |

### Key Observations

1. **ARRIVAL payout efficiency is 5.27× higher for hoppers than anchor.** Hoppers reconnecting to a pool with depressed difficulty earn disproportionately more per kH/s.

2. **Difficulty continues to DROP during ARRIVAL** (−7.1% over 5 min). The +10% up-clamp is too slow to respond — the 200-share rolling window means the returning hash rate only gradually shifts the average upward.

3. **Hopper payout surges from 0.035 LTC to 0.949 LTC** during the 5-minute ARRIVAL phase, while anchor payout drops from 1.440 to 0.582 LTC. Hoppers rapidly dilute the anchor miner's accumulated share of the reward pool.

4. **Expected payout in BASELINE with equal hash:** With 2 of 3 L1s mining to the hopper address, you'd expect a 2:1 payout ratio. The actual BASELINE ratio of 2.87× reflects some residual hopper advantage from prior pool state.

---

## 6. Conclusions

### Phase 1a Assessment: INEFFECTIVE

The asymmetric difficulty clamp as implemented does not provide meaningful protection against hop-on mining because:

1. **The clamp never activates.** With `TARGET_LOOKBEHIND=200`, per-share difficulty adjustments are naturally within ±2%, far below the 50% threshold needed to trigger the asymmetric branch.

2. **The difficulty adjustment mechanism is inherently slow.** The 200-share rolling window means difficulty takes ~80+ shares (~10+ minutes at reduced hash rate) to adjust by even 27% after a 67% hash departure.

3. **Returning hoppers benefit from the depressed difficulty.** The slow up-adjustment means hoppers arrive to cheap shares and earn 5.27× more per kH/s than the anchor miner during the arrival phase.

4. **The problem is NOT the down-clamp being too tight — it's the rolling window being too wide.** Even completely unclamped, per-share changes would only be ~1-2% because the 200-share average dominates.

### What Would Work Instead

The fundamental issue is that the 200-share rolling window makes difficulty inherently slow to respond. Three alternative approaches:

#### Option A: Reduce TARGET_LOOKBEHIND
Reducing from 200 to e.g. 20 shares would make difficulty 10× more responsive. But this also makes difficulty 10× more volatile during normal operation, potentially causing oscillation.

#### Option B: Asymmetric Up-Clamp (Opposite Direction)  
Instead of allowing faster difficulty DROPS, **restrict how fast difficulty can RISE** when new hash arrives. Returning miners would face the depressed difficulty for longer, making hop-on less profitable. However, this also penalizes legitimate miners joining the pool.

#### Option C: Per-Miner Difficulty (Phase 2)
Track difficulty and payout weighting per-miner (by address). New or returning miners start at a higher personal difficulty that gradually relaxes toward pool difficulty. This is the only approach that specifically targets hoppers without affecting honest miners.

#### Option D: Payout Smoothing / PPLNS Window  
Weight share payouts by the difficulty at which they were found relative to a moving average. Shares found at below-average difficulty receive proportionally less payout weight.

---

## 7. Recommendations

1. **Phase 1a should be reverted or left as-is** — it has no measurable effect but also causes no harm.

2. **Phase 2 (per-miner difficulty) should be prioritized** as the primary anti-hopping mechanism.

3. **Consider Option D (payout smoothing)** as a complementary defense — it's simpler than per-miner difficulty and directly addresses the payout advantage.

4. **The test infrastructure is validated and working.** The iptables-based miner control, automated phase orchestration, and data collection pipeline are solid for future tests.

---

## Appendix: Test Infrastructure

- **Miner control:** `scripts/l1_control.py` — bidirectional iptables REJECT (`--reject-with tcp-reset`)
- **Test orchestrator:** `scripts/hopper_test.py` — automated 4-phase testing
- **Analysis:** `scripts/hopper_analyze.py` — CSV parsing and summary generation
- **Data:** `hopper_test_20260303_002015.csv` — 312+ rows, 5s sampling interval, dual-node
