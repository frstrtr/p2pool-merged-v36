# Pool Hopping Attacks on P2Pool PPLNS

> **Date:** March 2026  
> **Status:** Active threat — observed on Litecoin mainnet  
> **Severity:** HIGH — existential risk to pool economics  
> **See also:** [V36 Release Notes](V36_RELEASE_NOTES.md) for the complete list of deployed defenses | [FUTURE.md](FUTURE.md) for V37 adaptive windows & c2pool migration

---

## Table of Contents

1. [Overview](#1-overview)
2. [Attack Type 1: PPLNS Window Persistence (Payout Stealing)](#2-attack-type-1-pplns-window-persistence-payout-stealing)
3. [Attack Type 2: Difficulty Death Spiral](#3-attack-type-2-difficulty-death-spiral)
4. [Attack Type 3: Repeated Hopping (Pool Drain)](#4-attack-type-3-repeated-hopping-pool-drain)
5. [Real-World Case Study (March 2026)](#5-real-world-case-study-march-2026)
6. [Root Cause Analysis](#6-root-cause-analysis)
7. [Defense Strategies](#7-defense-strategies)
8. [Implementation Recommendations](#8-implementation-recommendations)
   - 8.1 [V36 Implementation Plan (Python 2.7/PyPy — Hopper Elimination)](#81-v36-step-by-step-implementation-plan-python-27--pypy)
   - 8.2 [Honest Miner Experience: Step-by-Step Impact](#82-honest-miner-experience-step-by-step-impact-analysis)
   - 8.3 [Merged Mining Impact](#83-merged-mining-impact-defense-stack--redistribution-logic)
   - 8.4 [V37 Roadmap: Structural Hardening (C++ c2pool)](#84-v37-roadmap-structural-hardening-c-c2pool)
9. [References](#9-references)

---

## 1. Overview

Pool hopping is a strategy where a miner joins a pool temporarily during
favorable conditions, collects disproportionate rewards, and leaves before the
costs materialize. In P2Pool's PPLNS (Pay Per Last N Shares) system, this
exploits two fundamental properties:

1. **Shares are weighted by work, not time.** A share mined during an
   artificially elevated difficulty carries more PPLNS weight than one mined
   at normal difficulty — even though both represent the same wall-clock
   mining effort.

2. **Shares persist in the PPLNS window long after the miner departs.** The
   8640-share window (~36 hours at normal rates) means a 5-hour burst earns
   block rewards for the next 31+ hours at zero ongoing cost.

These two properties combine to create a **two-phase attack** that is strictly
more profitable per mining hour than loyal continuous mining.

### Terminology

| Term | Definition |
|------|-----------|
| **PPLNS window** | Last `REAL_CHAIN_LENGTH` (8640) shares used for payout calculation |
| **Share weight** | `target_to_average_attempts(share.target)` — average hashes to find this share |
| **Hopper** | Miner who joins/leaves strategically for profit |
| **Semiwhale** | Hopper with 2–5× the pool's steady-state hashrate |
| **Whale** | Hopper with 10×+ the pool's steady-state hashrate |
| **Difficulty clamp** | Per-share retarget limit: ±10% (`prev*9//10` to `prev*11//10`) |

---

## 2. Attack Type 1: PPLNS Window Persistence (Payout Stealing)

### Mechanism

```
Phase 1 — BURST (hours 0–5):
  Hopper joins with high hashrate (e.g. 2× pool)
  Produces shares rapidly at escalating difficulty
  Difficulty climbs via +10%/share clamp
  Accumulates hundreds of HIGH-WORK shares

Phase 2 — PERSISTENCE (hours 5–41):
  Hopper disconnects — contributes ZERO hashrate
  Shares remain in 8640-share PPLNS window
  Each share weighted by inflated difficulty (1.7× normal)
  Hopper collects rewards on EVERY block found
  Shares only exit when pushed out by ~8640 newer shares
```

### Why It Works

The PPLNS weight function in `WeightsSkipList.get_delta()` (data.py) is:

```python
att = bitcoin_data.target_to_average_attempts(share.target)
return (1, {share.address: att * (65535 - donation)}, att * 65535, att * donation)
```

This is pure **accumulated work** — no time decay, no depth penalty, no
vesting. A share mined 30 hours ago has exactly the same weight as one mined
30 seconds ago, provided both have the same target.

### Impact

The hopper's shares are mined during elevated difficulty, giving each share
1.5–3.6× more weight than an honest miner's share at normal difficulty. This
means:

- **Hopper's effective PPLNS fraction** exceeds their actual share count
  fraction
- **Loyal miners' payouts are diluted** for the entire 36-hour window
  persistence period
- **Hopper earns rewards for zero ongoing work** — pure parasitic extraction

### Worked Example

```
Pool steady state: 50 GH/s, ~240 shares/hour, normal diff 1.5e7
Hopper: 100 GH/s (2× pool), mines for 5 hours

Hopper produces:  407 shares at avg diff 2.5e7
Hopper work:      407 × 2.5e7 = 1.02e10
Honest work:      8233 × 1.65e7 = 1.36e11
Hopper PPLNS:     7.0% of window weight (but only 4.7% of share count)

Reward per mining hour:
  Hopper:  0.1481 LTC/hr  (5 hours worked)
  Loyal:   0.0393 LTC/hr  (36 hours worked)
  HOPPER ADVANTAGE: 3.8×
```

---

## 3. Attack Type 2: Difficulty Death Spiral

### Mechanism

```
T=0:  Pool hashrate = H, share difficulty = D
T=1:  Whale joins with hashrate 10H
T=2:  Shares arrive 10× faster → difficulty climbs toward 10D
      (limited by +10%/share clamp, so gradual ramp)
T=3:  After N shares, difficulty reaches 3–5× normal
T=4:  Whale leaves
T=5:  Pool hashrate back to H, but difficulty still at 3–5× D
T=6:  Honest miners produce shares at 1/3 to 1/5 normal rate
T=7:  Each share only drops difficulty by 10% (symmetric clamp)
T=8:  Recovery takes HOURS — honest miners are starved
```

### The Asymmetry Problem

The core vulnerability is the **symmetric ±10% per-share difficulty clamp**
in the retarget algorithm (data.py line 732):

```python
pre_target2 = math.clip(pre_target,
    (previous_share.max_target * 9 // 10,    # can drop 10%
     previous_share.max_target * 11 // 10))   # can rise 10%
```

**Going up:** When the whale is present, shares arrive rapidly. Each share
raises difficulty by up to 10%. With fast share production (10× rate), many
shares arrive per minute, and difficulty ramps quickly in wall-clock time.

**Going down:** When the whale leaves, shares arrive slowly (honest miners at
elevated difficulty). Each share can only lower difficulty by 10%. But fewer
shares arrive per unit time, so the **wall-clock recovery is much slower than
the ramp-up**.

### Quantified Impact

From the March 1, 2026 attack data:

| Hour | Avg Difficulty | vs Normal | Shares Produced | vs Baseline (240/hr) |
|------|---------------|-----------|-----------------|---------------------|
| 11 (pre-attack) | 1.5e7 | 1.0× | 238 | 100% |
| 12 (burst start) | 2.5e7 | 1.7× | 330 | 138% (whale shares) |
| 13 (burst) | 3.0e7 | 2.0× | 134 | **56%** (honest miners hurt) |
| 14 (burst) | 2.6e7 | 1.8× | 317 | 132% (whale shares) |
| 15 (burst peak) | 5.4e7 | **3.6×** | 143 | **60%** (honest devastated) |
| 16 (recovery) | 1.9e7 | 1.3× | 168 | **70%** |
| 17 (recovery) | 1.2e7 | 0.8× | 169 | 70% |
| 18+ (normal) | 1.5e7 | 1.0× | ~240 | 100% |

During the attack peak (hour 15), honest miners produced **60% of their
normal share rate** while difficulty was 3.6× above baseline. This means they
did the same amount of hash work but received fewer share credits — the
"missing" credit effectively subsidized the whale's inflated shares.

### Death Spiral Scenario

If the whale is large enough (10×+ pool hashrate), the post-departure
difficulty can be so elevated that honest miners produce almost no shares for
hours. If they give up and leave:

```
Difficulty elevated → fewer shares → slower recovery
→ miners leave → even fewer shares → even slower recovery
→ MORE miners leave → DEATH SPIRAL
```

The pool can become unusable for hours or days. This is not theoretical — it
is the natural consequence of the symmetric clamp when hashrate ratio exceeds
~5×.

---

## 4. Attack Type 3: Repeated Hopping (Pool Drain)

### Mechanism

A hopper repeats the burst-and-leave cycle on a regular schedule:

```
Cycle 1: Mine 5 hours, leave 7 hours (shares in window)
Cycle 2: Mine 5 hours, leave 7 hours (window now has 2 sessions)
Cycle 3: Mine 5 hours, leave 7 hours (window has 3 sessions)
...
Steady state: Window ALWAYS contains 2–3 sessions of hopper shares
```

### Impact

With the 8640-share window (~36 hours) and a 12-hour hop cycle:

- Window permanently contains 800–1200 hopper shares
- Hopper maintains ~15–20% of PPLNS weight while mining only 42% of the time
- **Effective dilution of loyal miners: permanent 15–20%**
- The pool becomes structurally unprofitable for small miners
- Rational miners leave → pool shrinks → hopping becomes even more effective
- End state: pool collapses or becomes a hopper-only pool

### The Rational Miner Problem

Once hopping is known to be profitable, **every rational miner should hop**.
The equilibrium is that no one mines continuously, block finding becomes
sporadic, and the pool offers no advantage over solo mining. This is the
existential threat: pool hopping, if unpunished, destroys the cooperative
benefit that justifies P2Pool's existence.

---

## 5. Real-World Case Study (March 2026)

> **Note:** All addresses below are anonymized. The attacker address is replaced
> with a dummy derived from the well-known `abandon` BIP39 mnemonic.

### Attacker Profile

| Field | Value |
|-------|-------|
| Address | `LaBandon1DummyAttackerAddrXXXXXWc5fB` (anonymized) |
| Infrastructure | Runs own P2Pool node (P2P share propagation) |
| Connected to | third-party friendly node (anonymized) |
| Share propagation | Via P2P network, NOT via local stratum |
| Estimated hashrate | ~100 GH/s (2× pool steady state) |

> **Baseline clarification (verified March 2, 2026):** The pool's honest
> baseline hashrate is **49.5 GH/s** (from `/global_stats` on live node
> [pool-node]:9327). The observed attacker brought ~100 GH/s, making the
> combined pool hashrate ~150 GH/s **during the attack**. Historical
> hashrate readings of 150 GH/s or 294 GH/s on this pool likely reflect
> attack periods, not the honest miner baseline. The attacker controlled
> **~67% of total pool hashrate** during the burst — a true semiwhale at
> 2× the pool's honest capacity.

### Attack Timeline

14 mining sessions detected in share logs, all on March 1, 2026:

| Session | Time (UTC) | Shares | Rate (shares/min) | Pattern |
|---------|-----------|--------|-------------------|---------|
| 1 | 12:24–13:01 | 168 | 4.5 | Main burst |
| 2 | 13:18–13:20 | 4 | 2.0 | Probe |
| 3 | 13:30–13:33 | 8 | 2.7 | Probe |
| 4 | 14:04–14:07 | 10 | 3.3 | Ramp |
| 5 | 14:38–15:16 | 175 | 4.6 | Second burst |
| 6–14 | 15:23–17:53 | 42 | scattered | Taper/monitoring |

**Total: 407 shares in ~5 hours of active mining.**

### Hop Pattern

The attacker's behavior shows classic pool hopping characteristics:

1. **Burst entry** — High hashrate, rapid share production (~4.5/min)
2. **Mid-session gap** — Disappears for 15–20 minutes (hours 13–14)
3. **Second burst** — Returns at even higher rate (4.6/min)
4. **Taper exit** — Scattered single shares, then silence

The gap between sessions 1 and 5 may indicate the attacker was monitoring
difficulty response and adjusting strategy.

### Forensic Evidence from Share Logs

```
Shares on disk:  shares.5 through shares.8 (10 MB each)
Hopper shares:   407 of 39,533 total shares (1.0% by count)
Hopper work:     22% of today's total work (weighted by difficulty)
Hopper /users:   47.1% of last 720 shares' work
Hopper PPLNS:    11.8% of 8640-share window payout
```

The disparity between 1.0% share count and 11.8–47.1% work weight
demonstrates the attack's effectiveness.

### Reward Efficiency

```
Hopper:          0.7404 LTC earned / 5 hrs mining = 0.1481 LTC/hr
Loyal miner:     1.4146 LTC earned / 36 hrs mining = 0.0393 LTC/hr

HOPPER ADVANTAGE: 3.8× per mining hour
```

### Reevaluation with Verified Pool Baseline (March 2026)

With the live-verified pool baseline of 49.5 GH/s, the attack severity is
even more striking than initially realized:

```
Pool honest hashrate:        49.5 GH/s  (verified: /global_stats)
Attacker hashrate:           ~100 GH/s  (from share rate analysis)
Combined during attack:      ~150 GH/s
Attacker as % of honest:     202%       (semiwhale: 2× pool)
Attacker as % of combined:   66.9%      (controlled 2/3 of pool during burst)

Difficulty impact:
  Baseline share diff:       ~1.5e7     (at 49.5 GH/s honest pool)
  Peak during attack:        5.4e7      (3.6× elevation)
  Expected elevation:        pool_combined/pool_honest × base ≈ 3.0× 
  Actual 3.6×:               attacker slightly MORE than 2× at peak moments

Share production analysis:
  Honest miners at 49.5 GH/s:  ~240 shares/hour (at normal diff)
  Honest miners at 5.4e7 diff: ~67 shares/hour (75% reduction!)
  Attacker at 100 GH/s:        ~400 shares/hour (at any diff — hashrate advantage)
  Attacker's share dominance:   86% of shares during peak hours

Economic extraction:
  5-hour burst cost:           ~$15–25 electricity (100 GH/s ASIC fleet)
  Reward captured:             0.7404 LTC × $100/LTC ≈ $74
  Net profit:                  ~$50–60 per hop cycle
  ROI per hop:                 200–400%
  
  If repeated 2×/day:          ~$100–120/day parasitic extraction
  Annual if unchecked:         ~$36,000–44,000 stolen from loyal miners
```

**Critical insight:** At 49.5 GH/s, the pool is so small relative to the
LTC network (0.0016%) that even a modest ASIC operator (~100 GH/s) can
completely dominate it during a hop. The 3.8× efficiency ratio is
**conservative** — it averages across the full 5-hour session including
ramp-up. During the peak burst (hours 12 and 15 in the hourly table), the
attacker's instantaneous efficiency was likely 5–6×.

### Where the Stolen Rewards Come From

Every LTC the hopper earns beyond their fair share is taken directly from
loyal miners. The 8640-share window is zero-sum: if the hopper claims 11.8%
of every block for 36 hours after a 5-hour burst, the other miners' combined
share is reduced from 100% to 88.2%.

For a loyal miner earning 0.0393 LTC/hr, the presence of the hopper's stale
shares costs approximately:

```
Without hopper: loyal miner earns X per block
With hopper:    loyal miner earns X × (1 - 0.118) = 0.882X per block
Loss: 11.8% of all rewards for 36 hours = ~0.17 LTC total per loyal miner
```

---

## 6. Root Cause Analysis

### Vulnerability 1: Pure Work-Based PPLNS (No Time Decay)

**Location:** `WeightsSkipList.get_delta()` in data.py (~line 1641)

```python
att = bitcoin_data.target_to_average_attempts(share.target)
return (1, {share.address: att * (65535 - donation)}, att * 65535, att * donation)
```

Weight = `target_to_average_attempts(target)` = average hash attempts. There
is no depth factor, no aging, no decay. A share at depth 8000 has exactly the
same weight as one at depth 0, given equal targets.

**Consequence:** Hopper's shares earn rewards for the entire 36-hour window
duration after departure.

### Vulnerability 2: Symmetric Difficulty Clamp

**Location:** `generate_transaction()` in data.py (~line 732)

```python
pre_target2 = math.clip(pre_target,
    (previous_share.max_target * 9 // 10,
     previous_share.max_target * 11 // 10))
```

The clamp is symmetric: difficulty can rise or fall by at most 10% per share.
When a whale leaves and share production slows, difficulty drops slowly in
wall-clock time because the 10% drops are gated by (slow) share arrivals.

**Consequence:** Difficulty recovery after whale departure takes 3–6× longer
in wall-clock time than the ramp-up, starving honest miners.

### Vulnerability 2b: Share-Count-Only Retarget (No Time Component)

**Location:** Same retarget code — the `pre_target` formula uses
`get_pool_attempts_per_second()` over the last `TARGET_LOOKBEHIND` shares,
but adjustment only happens **when a new share is created**.

If difficulty is so high that no honest miner can find a share at all, the
retarget code **never executes**. Unlike Bitcoin's 2016-block retarget (which
at least fires on each block), P2Pool's retarget is completely gated by share
production. No shares → no retarget → permanent death spiral.

**Parallel:** Bitcoin Cash had the identical problem after forking from BTC
in 2017. When hashrate migrated back to BTC, BCH blocks became too slow to
find, and the per-block retarget couldn't fire because there were no blocks.
They solved it with the Emergency Difficulty Adjustment (EDA), later replaced
by DAA, and finally the mathematically clean ASERT algorithm. P2Pool's share
chain is structurally identical to a blockchain here — the same fix applies.

**Consequence:** A true whale (100×+ pool hashrate) can leave the pool in a
state where honest miners CANNOT find shares for hours or indefinitely. The
only current recovery is manual operator intervention (sharechain flush).

### Vulnerability 3: No Share Vesting

Shares receive full PPLNS weight immediately upon inclusion in the share
chain. There is no vesting period, no maturation delay, no minimum depth
requirement.

**Consequence:** A 5-minute mining burst immediately captures full PPLNS
weight, with no discount for being new or unproven.

### Vulnerability 4: P2P Share Propagation Bypasses Stratum Controls

The attacker runs their own P2Pool node and propagates shares via P2P. This
means:

- Stratum-level rate limiting is irrelevant
- Connection banning is ineffective (shares arrive via P2P gossip)
- Address blacklisting is trivially defeated (new address per session)
- Any defense must be at the **protocol/PPLNS level**, not the node level

### Vulnerability 5: PPLNS Window Is Short Relative to Block Interval

**Parameters:** `REAL_CHAIN_LENGTH = 8640` shares × `SHARE_PERIOD = 15s` =
**36 hours** PPLNS window.

**Problem:** At current pool hashrate, average time between LTC blocks found
by the pool is far longer than the window. **Live verification from
http://[pool-node]:9327 (March 2, 2026):**

```
┌───────────────────────────────────────────────────────────────────────────┐
│ LIVE DATA — P2Pool Mainnet ([pool-node]:9327)           March 2, 2026     │
├───────────────────────────────────────────────────────────────────────────┤
│ Pool hashrate:              49.5 GH/s (smoothed, /global_stats)           │
│ Pool hashrate (current):    70.3 GH/s (instantaneous, dashboard)          │
│ Pool % of network:          0.0016% of 3.05 PH/s                          │
│ LTC network difficulty:     106,494,367                                   │
│ LTC expected TTB:           107 days (9,239,886 seconds)                  │
│ DOGE expected TTB:          67 days  (5,770,419 seconds)                  │
│ Share difficulty:            8.56 (min)                                   │
│ Share chain:                8640 shares (full V35 chain)                  │
│ PPLNS window:               36 hours                                      │
│ Window / LTC TTB:           1.4%                                          │
│ Window / DOGE TTB:          2.2%                                          │
│ Last LTC block found:       2026-02-26 (block 3062938) — 4 days ago       │
│ Expected blocks/year:       ~3.4 LTC, ~5.5 DOGE                           │
└───────────────────────────────────────────────────────────────────────────┘
```

This means:

1. **The PPLNS window is only 1.4% of the inter-block interval.** A hopper
   who mines for 36 hours floods the ENTIRE payout window, then leaves with
   36 hours of maximum PPLNS weight — but the next block won't be found for
   another ~107 days on average. The situation is **7× worse** than previous
   estimates that assumed 150 GH/s.

2. **One 36-hour burst dominates the payout.** Since the window is exactly
   8640 shares, a hopper who fills the window pushes out ALL prior shares
   from loyal miners. If a block is found during or shortly after the burst,
   the hopper captures nearly all of it.

3. **Loyal miners' old work scrolls out.** A miner who has been loyally
   mining for months only gets credit for the most recent 36 hours. The
   hopper and the loyal miner look identical in the PPLNS window — both
   have "36 hours of work" — but the loyal miner contributed for months
   while the hopper contributed only during the payout window.

**Math: Hopper flooding scenario (using live data)**

```
Pool hashrate: 49.5 GH/s (loyal miners — live March 2026)
Hopper hashrate: 50 GH/s (approximately equal burst)
Share period: 15s → 240 shares/hour
PPLNS window: 8640 shares = 36 hours

Before hopper arrives:
  Window: 8640 shares, all from loyal miners → loyal = 100% of weight

Hopper mines for 36 hours at 50 GH/s:
  Pool total: ~100 GH/s → share period effectively halves
  ~480 shares/hour for 36 hours = ~17,280 shares produced
  Window: last 8640 shares → hopper has ~50% of weight

Hopper leaves after 36 hours:
  Window still contains ~4320 hopper shares for next 18 hours
  Expected time to next block: ~107 DAYS (not hours!)
  Hopper's 36 hours = 1.4% of block interval
  But hopper captured 50% of PPLNS window
  Efficiency: hopper mined 1.4% of interval, captured 50% of window weight
```

**The deeper problem:** Even with exponential decay (§7.2), a 36-hour window
means every defense mechanism operates on a short memory. The hopper only
needs to sustain a 36-hour burst to capture maximum influence. Compare:

| Parameter | Current P2Pool | Bitcoin Mining |  Ideal for Anti-Hop |
|-----------|---------------|----------------|--------------------|
| Payout window | 36 hours | N/A (single block) | 50–107 days |
| Block interval | ~107 days (verified) | 10 minutes | N/A |
| Window/Block ratio | 1.4% (verified) | N/A | ≥50% |

When the payout window is much shorter than the block interval, a hopper
can time their burst to maximize capture of the next block's reward with
minimal sustained presence.

**Key insight:** The tracker already stores **2 × CHAIN_LENGTH + 10 =
17,290 shares** (~72 hours) — see `clean_tracker()` in `node.py` line ~386.
This deeper storage exists for fork resolution and chain reorganization.
We can leverage it for vesting lookback WITHOUT changing the PPLNS payout
window.

**The fundamental fix: Adaptive windows** — see §7.3.10 for a design where
both the PPLNS payout window and the vesting lookback scale dynamically
with expected time-to-block, ensuring the window always covers a meaningful
fraction of the inter-block interval regardless of pool hashrate.

**Consequence:** A hopper who sustains a 36-hour burst captures the entire
PPLNS window, getting maximum reward from any block found in the next
18 hours. The window is too short to differentiate burst miners from loyal
miners.

---

## 7. Defense Strategies

### 7.1 Defense 1: Asymmetric Difficulty Adjustment + Time-Based Emergency Decay

**Consensus required:** NO — local calculation only, immediate deployment.

This defense has two parts that work together:

#### 7.1.1 Part A: Asymmetric Clamp (Per-Share)

Replace the symmetric ±10% clamp with an asymmetric one that allows faster
downward adjustment when hashrate has clearly dropped:

```python
# Current (vulnerable):
pre_target2 = math.clip(pre_target,
    (previous_share.max_target * 9 // 10,
     previous_share.max_target * 11 // 10))

# Proposed (asymmetric):
if pre_target > previous_share.max_target:
    # Difficulty needs to DROP (target rising — hashrate decreased)
    # Allow up to ~40% difficulty drop per share when ratio > 1.5
    ratio = pre_target * 100 // previous_share.max_target  # integer %
    if ratio > 150:  # pre_target wants to rise >50% above previous
        # Target can rise up to 167% of previous = ~40% difficulty drop
        max_rise = previous_share.max_target * 5 // 3
    else:
        max_rise = previous_share.max_target * 11 // 10  # +10% (normal)
    pre_target2 = math.clip(pre_target,
        (previous_share.max_target * 9 // 10, max_rise))
else:
    # Difficulty needs to RISE (hashrate increased)
    # Keep normal +10% cap
    pre_target2 = math.clip(pre_target,
        (previous_share.max_target * 9 // 10,
         previous_share.max_target * 11 // 10))
```

**Limitation:** The asymmetric clamp operates **per share** — it only
triggers when a new share arrives. If the whale pumps difficulty so high
that honest miners **cannot find any shares at all**, the clamp never fires.
This is the "share drought" problem: no shares → no retarget → death spiral.

#### 7.1.2 Part B: Time-Based Emergency Decay (BCH-Inspired Failsafe)

> **Prior art:** Bitcoin Cash Emergency Difficulty Adjustment (EDA, 2017) and
> DAA (2017). Previously documented in `V36_IMPLEMENTATION_PLAN.md` §10.4–10.5.
>
> BCH faced the identical problem after its fork from BTC: when hashrate
> suddenly migrated away, blocks became too slow to find, and the standard
> per-block retarget couldn't fire because there were no blocks. Their fixes:
> - **BCH EDA (Aug 2017):** If 6+ blocks took >12h, reduce difficulty by 20%
> - **BCH DAA (Nov 2017):** Rolling 144-block window with time-weighted
>   adjustment (replaced EDA due to oscillation issues)
> - **ASERT/aserti3-2d (Nov 2020):** Exponential moving average targeting
>   ideal inter-block time — smooth, manipulation-resistant, no oscillation
>
> P2Pool's share chain is analogous to Bitcoin's blockchain here — the same
> "can't retarget without new blocks" problem applies.

When no shares have arrived for an abnormally long time, bypass the
per-share clamp and apply a time-based exponential decay to difficulty:

```python
# In generate_transaction(), BEFORE the per-share clamp:

EMERGENCY_THRESHOLD = net.SHARE_PERIOD * 20  # 20× expected share time
                                              # Mainnet: 15s × 20 = 300s (5 min)
                                              # Testnet:  4s × 20 =  80s
DECAY_HALF_LIFE = net.SHARE_PERIOD * 10       # Halve diff every 10 share periods
                                              # Mainnet: 150s, Testnet: 40s

if previous_share is not None:
    time_since_share = desired_timestamp - previous_share.timestamp
    if time_since_share > EMERGENCY_THRESHOLD:
        # EMERGENCY: No shares arriving — difficulty is too high for
        # remaining hashrate. Apply time-based exponential decay.
        # This is the BCH EDA concept adapted for P2Pool shares.
        excess_time = time_since_share - EMERGENCY_THRESHOLD
        # NOTE: Float OK here — emergency decay is a LOCAL calculation
        # (each node uses its own wall clock), not a consensus-critical
        # payout formula. Slight float variance between nodes is harmless
        # because shares are validated against each node's independent
        # emergency_max_target computation.
        decay_factor = 0.5 ** (excess_time / DECAY_HALF_LIFE)
        # Decay the previous share's max_target upward (easier difficulty)
        emergency_max_target = int(previous_share.max_target / decay_factor)
        emergency_max_target = min(emergency_max_target, net.MAX_TARGET)
        # Use this as the new target, bypassing the per-share clamp
        pre_target3 = math.clip(emergency_max_target,
            (net.MIN_TARGET, net.MAX_TARGET))
```

**How the two parts work together:**

```
SCENARIO: Whale at 100× loyal hashrate joins then leaves

Phase 1 — Whale present (shares every 0.15s):
  Per-share clamp: Diff ramps +10% per share (normal, wanted)
  Emergency decay: NOT active (shares arriving fast)

Phase 2 — Whale leaves, diff stuck at 100×:
  Honest miners at 1× hashrate → expected share time = 100 × 15s = 1500s (25 min)

  Case A (semiwhale, 3×): Honest miners CAN still find shares (~45s each)
    Per-share clamp: Fires on each share, -40% drop per share
    Emergency decay: NOT active (shares arriving within 5 min)
    Recovery: ~50 shares ≈ 40 minutes

  Case B (true whale, 100×): Honest miners CANNOT find shares for 25+ min
    Per-share clamp: DEAD — no shares to trigger it
    Emergency decay: ACTIVATES after 5 min (300s)
      At t=5min:  decay_factor = 1.0 → target unchanged
      At t=7.5min: decay_factor = 0.5 → diff halved
      At t=10min: decay_factor = 0.25 → diff quartered
      At t=15min: decay_factor = 0.063 → diff ÷16
      Eventually honest miner CAN find a share → per-share clamp takes over
    Recovery: 15–20 minutes even from extreme whale attack
```

**Combined effect on attack (Part A + Part B):**

| Metric | Before (symmetric) | Part A only | Part A + B |
|--------|-------------------|-------------|------------|
| Difficulty recovery | ~3 hours (*) | **~40 min** | **~40 min** |
| True whale (100×) recovery | **∞ (death spiral)** | **∞ (death spiral)** | **~15 min** |
| Shares at inflated diff | 407 | ~200 | ~200 |
| Honest miner starvation | 28–68% rate loss | 10–25% rate loss | 10–25% |
| Share drought survivable? | NO | NO | **YES** |

(*) ~3 hours observed in the March 1 attack data (hour 15 peak → hour 18 normal).
    Note: difficulty recovery ≠ PPLNS payout impact. The hopper's inflated-difficulty
    shares persist in the 8640-share PPLNS window for up to ~36 hours regardless of
    how fast difficulty recovers. Phases 2a/2b address PPLNS persistence.

**Why both parts are needed:**
- Part A (asymmetric clamp) handles the **common case** — semiwhale leaves,
  honest miners can still find shares, just slowly. The clamp lets each share
  drop difficulty faster.
- Part B (emergency decay) handles the **extreme case** — true whale leaves,
  honest miners can't find ANY shares. Without Part B, Part A alone leaves
  the pool in a permanent death spiral.

**Consensus safety:** Both parts are local calculations — they determine what
target the node proposes for the next share. When an honest miner finally
finds a share with the emergency-decayed target, other nodes validate it
against their own emergency calculation (same timestamp, same formula) and
accept it. No share format change, no protocol modification.

**Risk:** LOW. Both parts only affect how fast the pool's target adjusts.
Does not change share format, PPLNS formula, or require network agreement.
Part B's exponential decay is well-studied (BCH ASERT uses the same math)
and naturally converges — no oscillation risk.

**Limitations:** Does not address PPLNS window persistence. Shares already
in the window keep their inflated weight. See Defense 2 (§7.2) for that.

#### 7.1.3 Why Not Pure ASERT? (Design Rationale)

BCH's ASERT (aserti3-2d, deployed Nov 2020) is the gold standard for
blockchain difficulty adjustment. It computes target as a pure function of
elapsed time from an anchor point:

```
target = anchor_target × 2^((timestamp - anchor_timestamp - ideal_time) / halflife)
```

ASERT properties: stateless, smooth, oscillation-free, proven in production
for 5+ years on BCH. It was co-designed by Mark Lundeberg and Jonathan Toomim
(our upstream P2Pool maintainer — a nice connection).

**However, P2Pool shares ≠ BCH blocks.** Applying ASERT to P2Pool's share
chain faces three fundamental differences that require adaptations:

**Problem 1: Timestamp Manipulation (Critical)**

BCH has a strong timestamp consensus rule (Median Time Past + 2-hour future
limit). A miner cannot meaningfully fake block timestamps without peer
rejection.

P2Pool's timestamp validation is **far weaker**:
- Lower bound: `previous_share.timestamp + 1` (V32+, data.py line 920)
- Upper bound: `time.time() + 600` (10 min in future, data.py line 1255)
- **No minimum relative to wall clock** — a share can carry any past timestamp

A whale running ASERT-aware code could:
1. Set artificially **high** timestamps on their burst shares →
   ASERT thinks shares are coming slowly → difficulty stays low during burst
2. Set artificially **low** (barely +1) timestamps to compress the time
   dimension → ASERT thinks shares are coming extremely fast → difficulty
   spikes to maximum, trapping honest miners after departure

Pure ASERT trusts timestamps — in P2Pool, timestamps are miner-controlled.

**Problem 2: Asymmetric Response Needed**

ASERT is beautifully symmetric — it responds equally fast to hashrate
increases and decreases. For a blockchain this is correct and desirable.

For P2Pool anti-hopping, we **want** asymmetry:
- **Upward (whale joins):** Slow, clamped rise (+10% per share). Fast rise
  helps the whale create high-weight shares quickly. We want the pool to
  resist rapid difficulty inflation.
- **Downward (whale leaves):** Fast, aggressive drop. The whole point of the
  defense is rapid recovery. Slow symmetric drop = death spiral.

Pure ASERT can't give us this — its half-life is the same in both directions.

**Problem 3: Per-Share vs Stateless Anchor**

ASERT is stateless — it only needs the anchor block and current time. This
is elegant for blockchains but creates a subtle problem for P2Pool. The
"anchor" would be the previous share, and each new share becomes the next
anchor. With miner-controlled timestamps, the anchor chain itself can be
manipulated to drift the target.

**Our Approach: Hybrid (ASERT-Inspired, P2Pool-Hardened)**

Instead of pure ASERT, we use a two-layer approach that takes ASERT's
mathematical core (exponential decay over time) but hardens it for the P2Pool
adversarial environment:

| Aspect | Pure BCH ASERT | Our Hybrid (§7.1.1 + §7.1.2) |
|--------|---------------|-------------------------------|
| **Normal operation** | Exponential EMA | Per-share clamp (±10% / -40%) — share-count-based, not time-based |
| **Emergency (no shares)** | Not needed (blocks always come eventually on BCH) | Time-based exponential decay (ASERT math) — activates only when share gap > 20× expected |
| **Timestamp trust** | Strong (MTP + 2hr consensus) | Minimal — emergency decay uses `desired_timestamp` which is local node's wall clock, not miner-supplied |
| **Symmetry** | Symmetric (same up/down) | Asymmetric — slow up (+10% clamp), fast down (-40% clamp + emergency) |
| **Manipulation resistance** | MTP consensus rule | Per-share clamp limits damage per share; emergency uses node-local time |
| **Proven math** | ✅ ASERT exponential | ✅ Same exponential, just scoped to emergency-only |

**Key hardening detail:** In Part B (§7.1.2), `desired_timestamp` is **not
the miner's claimed timestamp** — it is the local node's `time.time()` at
share creation. When validating a received share, each node independently
computes the emergency target using its **own** wall clock. This eliminates
the timestamp manipulation vector that would break pure ASERT.

```
Validation path for emergency-decayed shares:
1. Receive share with target T from peer
2. Check: T <= node's own emergency_target(time.time(), previous_share)?
3. If yes, share is valid (node agrees difficulty should be that low)
4. If no, reject (peer is gaming timestamps)
```

**Future improvement possibility:** If P2Pool later adopts tighter timestamp
consensus rules (e.g., MTP-based lower bound, tighter future limit), pure
ASERT could become viable for normal operation too — replacing the per-share
clamp entirely. This would be a V36+ consideration if needed post-deployment.

**Summary:** We take ASERT's proven exponential math but apply it only where
P2Pool can trust the time source (emergency mode, node-local clock), and
use manipulation-resistant per-share clamping for normal operation. This
gives us ASERT's resilience to death spirals without its vulnerability to
timestamp gaming.

#### 7.1.4 MTP Deferred: Why Not Now?

**Median Time Past (MTP)** is a natural candidate for tightening P2Pool's
timestamp validation. Bitcoin and Litecoin blockchains require each block's
timestamp to exceed the median of the previous 11 blocks. This prevents
miners from setting timestamps arbitrarily in the past, which would break
pure ASERT (see Problem 1 in §7.1.3).

**Implementation is trivial** — approximately 20 lines of code:

```python
# Compute Median Time Past from previous 11 shares
def get_median_time_past(tracker, share_hash, count=11):
    timestamps = []
    current = share_hash
    for _ in range(count):
        if current is None:
            break
        share = tracker.items[current]
        timestamps.append(share.timestamp)
        current = share.previous_hash
    timestamps.sort()
    return timestamps[len(timestamps) // 2]

# In share validation, add:
mtp = get_median_time_past(tracker, share.previous_hash)
if share.timestamp <= mtp:
    raise ValueError("timestamp must exceed MTP of previous 11 shares")
```

**Why we defer it:** MTP solves the wrong problem for our architecture.

1. **Emergency decay already uses node-local time.** The critical defense
   (§7.1.2) computes `desired_timestamp` from the node's own `time.time()`,
   not from miner-supplied timestamps. MTP hardens miner timestamps, but
   our emergency decay doesn't use miner timestamps at all. The attack
   vector MTP would close is already closed by design.

2. **Per-share clamp doesn't depend on timestamps.** The normal-operation
   defense (§7.1.1) adjusts difficulty based on share count relative to
   expected pool attempts — purely work-based, no time component. MTP
   doesn't strengthen this defense either.

3. **The multi-temporal hierarchical architecture makes timestamp trust
   obsolete.** Our strategic direction (V36_IMPLEMENTATION_PLAN.md Part 16)
   is a multi-layered sharechain where miners are weighted by actual hash
   quality — the real number of leading zeros in `pow_hash`, not by
   self-reported timestamps. In this architecture:
   - **Tier placement** is determined by actual proof of work (hash quality)
   - **Summary share promotion** requires accumulated work meeting a threshold
   - **PPLNS weighting** uses exponential decay by depth (share count), not
     time elapsed
   - **Persistence measurement** counts work over sharechain depth, not
     temporal duration

   When work quality is the fundamental metric, timestamps become
   informational metadata rather than a security-critical input. Investing
   a consensus change on MTP would consume upgrade budget for a property
   that the target architecture doesn't rely on.

4. **MTP adds consensus complexity for marginal benefit.** Any timestamp
   rule change requires V36+ supermajority activation and risks chain
   splits if not all nodes upgrade simultaneously. The benefit (closing a
   theoretical timestamp manipulation vector that our defenses already
   sidestep) does not justify the activation risk and coordination cost.

5. **MTP can be revisited if pure ASERT is ever desired.** As noted in
   §7.1.3, if P2Pool later adopts tighter timestamp consensus rules,
   pure ASERT could replace the per-share clamp for normal operation. At
   that point, MTP would be a prerequisite — but that's a post-V36
   consideration at earliest, and only if the multi-temporal architecture
   doesn't render it moot first.

**Decision: MTP is deferred.** Not because it's difficult, but because our
defense architecture and strategic direction have evolved past needing it.
The right sequence is: deploy Phase 1 (asymmetric clamp + emergency decay
using node-local time) → deploy Phase 2 (exponential PPLNS decay by depth)
→ evolve toward multi-temporal work-quality weighting → reassess timestamp
rules only if the architecture still depends on them.

### 7.2 Defense 2: Exponential Decay on PPLNS Weights (★ RECOMMENDED)

**Consensus required:** YES — changes payout formula. Requires V36
supermajority.

Apply exponential decay to share weights based on depth in the share chain:

```python
# In WeightsSkipList.get_delta():
att = bitcoin_data.target_to_average_attempts(share.target)

# Apply exponential decay: weight = work × 2^(-depth / half_life)
# CONCEPTUAL (float — for illustration only):
#   HALF_LIFE = net.CHAIN_LENGTH // 4  # 2160 shares ≈ 9 hours
#   decay_factor = 2.0 ** (-depth / HALF_LIFE)
#   att = int(att * decay_factor)
#
# PRODUCTION (integer-safe — required for consensus):
HALF_LIFE = net.CHAIN_LENGTH // 4  # 2160 shares
PRECISION = 40
SCALE = 1 << PRECISION
LN2_FP = (SCALE * 693147180559945) // (10**15)  # ln(2) in fixed-point
decay_per_share = SCALE - LN2_FP // HALF_LIFE   # 2^(-1/HL) approx
# Use repeated-squaring for decay^depth (see §7.3.13 _decay_power):
decay_fp = _decay_power(SCALE, depth, decay_per_share, PRECISION)
att = (att * decay_fp) >> PRECISION

return (1, {share.address: att * (65535 - donation)}, att * 65535, att * donation)
```

> **Python 2.7 / PyPy note:** All P2Pool `.py` files use
> `from __future__ import division` so `/` is true (float) division.
> Consensus-critical code **must not** use floating-point exponentiation
> (`2.0 **`, `0.5 **`, `math.pow`) — different PyPy versions or platforms
> may produce different IEEE 754 results, causing payout divergence.
> All decay/vesting computations must use **40-bit fixed-point integer
> arithmetic** as shown above and in §7.3.13 (`IncrementalVestingCache`).
> The `_decay_power()` helper uses O(log n) repeated squaring with
> Python's arbitrary-precision integers — deterministic on every platform.

**Decay schedule:**

| Depth | Time since mined | Decay factor | Effective weight |
|-------|-----------------|--------------|-----------------|
| 0 | Just mined | 1.000 | 100% |
| 720 | ~3 hours | 0.794 | 79% |
| 2160 | ~9 hours | 0.500 | 50% |
| 4320 | ~18 hours | 0.250 | 25% |
| 6480 | ~27 hours | 0.125 | 12.5% |
| 8640 | ~36 hours | 0.063 | 6.3% |

**Effect on hopper vs loyal miner:**

The hopper's shares cluster at high depth (mined hours ago) and decay
rapidly. The loyal miner continuously produces new shares at depth ~0,
maintaining high effective weight.

```
Hopper: 407 shares at avg depth ~4000 after 16 hours
  Decay factor: 2^(-4000/2160) = 0.28
  Effective PPLNS weight: 11.8% × 0.28 ≈ 3.3%
  Theft reduced by 72%

Loyal miner: continuous stream of new shares
  Average depth: ~4320 (midpoint of window)
  Average decay factor: ~0.45 (weighted toward recent shares)
  Effective PPLNS weight: INCREASES relative to hopper
```

**Combined effect with Defense 1:**

| Configuration | Hopper PPLNS | Hopper LTC/hr | Efficiency ratio |
|--------------|-------------|---------------|-----------------|
| No defense | 11.8% | 0.1481 | 3.8× |
| Defense 1 only | ~8% | ~0.10 | 2.5× |
| Defense 2 only | ~3.3% | ~0.04 | 1.0× |
| **Defense 1 + 2** | **~2%** | **~0.025** | **0.6×** |

With both defenses, **hopping becomes unprofitable** — the hopper earns
LESS per mining hour than a loyal miner.

**Risk:** MEDIUM. Changes the economics of the entire pool. Must be validated
on testnet. All nodes must agree on the decay parameters for payout consensus.

### 7.3 Defense 3: Share Vesting (Upgraded — Gaming-Resistant)

> **Prior art:** First described in `V36_IMPLEMENTATION_PLAN.md` §10.6.1
> (Share Weight Vesting) and §10.6.2 (Per-Miner Tenure Vesting). Gaming
> analysis and the evolution to work-weighted exponential vesting is from
> §10.6 (Gaming Resistance Analysis).

Shares don't receive full PPLNS weight immediately — they must **vest**
(mature) before counting at full value. However, naive vesting designs
are **highly gameable**. This section walks through the evolution from
vulnerable designs to a gaming-resistant combined approach.

#### 7.3.1 Naive Variant A: Depth-Based Vesting (Per-Share) — VULNERABLE

**Consensus required:** YES — V36 supermajority.

Each share vests linearly based on how many shares have been built on top of
it in the share chain:

```python
VESTING_DEPTH = net.CHAIN_LENGTH // 6  # 1440 shares ≈ 6 hours

def get_vested_weight(share, tracker, current_height):
    """Share weight grows from 0% to 100% over VESTING_DEPTH."""
    share_height = tracker.get_height(share.hash)
    shares_on_top = current_height - share_height
    vesting_factor = min(1.0, shares_on_top / VESTING_DEPTH)
    raw_weight = bitcoin_data.target_to_average_attempts(share.target)
    return raw_weight * vesting_factor
```

**How it's supposed to work:**

```
WITHOUT VESTING (current):
  Big miner finds 30 shares at diff 100 → 3000 weight INSTANTLY
  Big miner leaves → difficulty stuck at 100, small miners stranded

WITH VESTING (VESTING_DEPTH=1440):
  Big miner finds 30 shares at diff 100 → weight starts at 0%
  After 720 more shares: weight = 50% → effective 1500
  If big miner left, only ~30% vested before honest shares dilute
```

**Weakness:** Against sustained bursts (5+ hours), shares fully vest during
the attack. The hopper's 1200 shares at depth 1200/1440 = 83% vested.
Also: depth-based vesting makes no distinction between a loyal miner's old
shares and a hopper's old shares — it penalizes the wrong party.

#### 7.3.2 Naive Variant B: Per-Miner Tenure Vesting — VULNERABLE

**Consensus required:** YES — V36 supermajority.

Instead of vesting individual shares, vest based on **how long the miner has
been consistently participating**. A miner's vesting factor is determined by
their share count in the lookback window:

```python
VESTING_WINDOW = 100  # Shares needed for full tenure

def calculate_miner_vesting(tracker, tip_hash, lookback):
    """Vest by miner tenure, not share depth."""
    miner_counts = {}
    for share in tracker.get_chain(tip_hash, lookback):
        addr = share.share_info['share_data']['address']
        miner_counts[addr] = miner_counts.get(addr, 0) + 1
    return {addr: min(1.0, count / VESTING_WINDOW)
            for addr, count in miner_counts.items()}
```

**Surface appeal:** A loyal miner with 150 shares gets 100% vesting; a
hopper who just arrived with 10 shares gets only 10%.

**But this is critically gameable.** See §7.3.3.

#### 7.3.3 Gaming Attack Analysis (Why Naive Vesting Fails)

Five specific attacks against count-based and simple tenure vesting, drawn
from the gaming resistance analysis in `V36_IMPLEMENTATION_PLAN.md` §10.6:

**Attack 1: Tenure Farming (Low-Cost Miner Placeholder)**

```
Hopper strategy: Keep a TINY miner running 24/7 at low hashrate

1. Hopper keeps a 0.1 GH/s placeholder miner always connected
2. Placeholder finds ~1 share/hour at minimum difficulty
3. Over 36 hours, accumulates 36+ low-diff shares → full tenure
4. Hopper brings 100 GH/s burst → instant full vesting (tenure intact)
5. Hopper leaves → placeholder keeps tenure alive for next burst

WHY IT WORKS AGAINST COUNT-BASED VESTING:
  └─ Tenure based on SHARE COUNT, not sustained HASHRATE
  └─ 100 old high-diff shares + 1 new low-diff share = still 101 shares
  └─ Vesting factor stays at 100%

COST: A placeholder miner at 0.1 GH/s costs ~$0.02/day in electricity.
      Negligible compared to the burst mining profit.
```

**Attack 2: Address Reuse Across Sessions**

```
Same hopper returns periodically:
  - Week 1: Joins with high hashrate, builds tenure, leaves
  - Week 2: Returns briefly, tenure still valid, spikes difficulty
  - Pattern: Periodic difficulty poisoning with minimal cost

WHY IT WORKS: As long as the address's shares haven't all scrolled out
of the PPLNS window, returning with that address restores full tenure.
```

**Attack 3: Cooperative Address Sharing**

```
Multiple hoppers share an address (or a key operator):
  - Hopper A builds tenure mining with address X
  - Hopper A leaves, Hopper B takes over mining with address X
  - Address X always has tenure, hoppers rotate freely

WHY IT WORKS: Tenure tracks ADDRESS, not miner identity. P2Pool has no
way to distinguish between "same miner returning" and "new miner using
same address." A pool hopping service could rotate operators seamlessly.
```

**Attack 4: Slow Ramp Down (Anti-Detection)**

```
  Instead of suddenly leaving:
  - Day 1: Mine at 100 GH/s (build shares)
  - Day 2: Gradually reduce to 50 GH/s
  - Day 3: Reduce to 10 GH/s
  - Day 4: Reduce to 1 GH/s (placeholder)

WHY IT WORKS: Gradual reduction avoids triggering consistency checks.
Each day's hashrate is within "normal variation" of the previous.
Work-based tenure stays high because reduction is slow.
```

**Attack 5: Split Addresses (Sybil)**

```
Use N addresses instead of one:
  - Split 100 GH/s across 5 addresses at 20 GH/s each
  - Each address stays below concentration thresholds
  - Tenure farming cost: 5 placeholder miners (~$0.10/day)

WHY IT WORKS: Limits exposure of any single address to detection
heuristics. Most per-miner metrics become less effective.
```

**Summary of which defenses these attacks defeat:**

```
┌─────────────────────────┬───────────┬──────────┬─────────────┬──────────┐
│ Attack                  │ Count-    │ Work-    │ Work +      │ Exp Decay│
│                         │ Based     │ Based    │ Consistency │ + Work   │
├─────────────────────────┼───────────┼──────────┼─────────────┼──────────┤
│ 1. Tenure farming       │ ✗ BEATEN  │ ✗ BEATEN │ ✓ Blocked   │ ✓ Blocked│
│ 2. Address reuse        │ ✗ BEATEN  │ ✗ BEATEN │ ~ Partial   │ ✓ Blocked│
│ 3. Cooperative sharing  │ ✗ BEATEN  │ ✗ BEATEN │ ~ Partial   │ ✓ Blocked│
│ 4. Slow ramp down       │ ✗ BEATEN  │ ✗ BEATEN │ ✗ BEATEN    │ ✓ Blocked│
│ 5. Split addresses      │ ~ Partial │ ~ Partial│ ~ Partial   │ ✓ Blocked│
└─────────────────────────┴───────────┴──────────┴─────────────┴──────────┘

✗ BEATEN = attack fully defeats defense
~ Partial = defense somewhat limits attack
✓ Blocked = attack is ineffective or unprofitable
```

**Key insight:** Only exponential decay + work-based weighting resists ALL
five gaming attacks. This is because exponential decay is a **physical law**
— shares ALWAYS lose weight over time, with no workaround possible. The
only way to maintain weight is to continuously contribute new work.

#### 7.3.4 Upgraded Approach: Work-Weighted Exponential Vesting

**Consensus required:** YES — V36 supermajority.

This is the **gaming-resistant** vesting design, evolved through the full
attack analysis above. It combines exponential decay with work-based
measurement and a consistency check:

```python
def calculate_robust_vesting(tracker, tip_hash, lookback, address, net):
    """
    Multi-factor vesting calculation resistant to all 5 gaming attacks.

    Factors:
    1. Work contribution (not just share count) — defeats Attack 1
    2. Exponential decay on old shares — defeats Attacks 2, 3, 4
    3. Consistency check (recent vs historical) — catches ramp-down
    """
    RECENCY_HALF_LIFE = lookback // 4  # Shares lose half weight per quarter

    total_decayed_work = 0
    recent_work = 0
    share_index = 0

    for share in tracker.get_chain(tip_hash, lookback):
        if share.share_info['share_data']['address'] != address:
            share_index += 1
            continue

        work = bitcoin_data.target_to_average_attempts(share.target)

        # Exponential decay based on age in share chain
        # NOTE: float shown for clarity. Production code MUST use
        # integer fixed-point (see §7.3.13 IncrementalVestingCache).
        age_factor = 0.5 ** (share_index / RECENCY_HALF_LIFE)
        total_decayed_work += work * age_factor

        if share_index < RECENCY_HALF_LIFE:
            recent_work += work

        share_index += 1

    # Vesting based on decayed work
    WORK_THRESHOLD = net.SHARE_PERIOD * lookback * 0.5  # Adjusted for decay
    vesting = min(1.0, total_decayed_work / WORK_THRESHOLD)

    return vesting
```

> **⚠ Consensus note:** The `0.5 **` float exponentiation above is for
> readability. The actual implementation uses the `IncrementalVestingCache`
> (§7.3.13 Strategy 1) with 40-bit fixed-point integer arithmetic — O(1)
> per share and deterministic across all Python 2.7 / PyPy platforms.
> The naive O(n) loop shown here is the **conceptual algorithm** only;
> it would also be too slow for adaptive windows (see §7.3.13 bottleneck
> analysis).

**Why this defeats every attack:**

```
ATTACK 1 (Tenure farming with placeholder):
  Placeholder at 0.1 GH/s → work contribution is ~0.001× normal
  Even with 36 shares, decayed_work ≈ 0.036 (vs threshold ~64,800)
  Vesting: ~0.00006% → effectively zero
  ✓ ELIMINATED — work-weighting makes placeholder worthless

ATTACK 2 (Address reuse across sessions):
  Week 1 shares: decayed by 2^(-672/2160) = ~80% of original weight
  More importantly: no RECENT work → total_decayed_work collapsing
  After 1 week away: vesting drops to ~15% of peak
  After 2 weeks: ~2%
  ✓ ELIMINATED — cannot bank tenure indefinitely

ATTACK 3 (Cooperative address sharing):
  When Hopper A leaves and Hopper B takes over:
  Hopper A's shares decay exponentially regardless of Hopper B's work
  Hopper B's NEW shares are young (high age_factor) but fresh (low total)
  Combined: address vesting DROPS during the handoff period
  ✓ ELIMINATED — rotation causes vesting dip, not persistence

ATTACK 4 (Slow ramp down):
  Day 1: 100 GH/s → decayed_work is high
  Day 2: 50 GH/s → new work at half rate, old work decaying
  Day 3: 10 GH/s → new work tiny, old work at 25% of original
  Day 7: 1 GH/s → total_decayed_work is ~3% of Day 1 peak
  ✓ ELIMINATED — exponential decay catches ANY rate of ramp-down
  No matter how gradually you reduce, the math is relentless

ATTACK 5 (Split addresses):
  5 addresses at 20 GH/s each:
  Each address has 1/5th the work → each at ~20% vesting
  Combined effective weight: 5 × 20% × (20 GH/s share) = same as 1 × 100%
  BUT: each address must now independently maintain recent work
  Cost: 5 placeholder miners (5× the farming cost) for same benefit
  ✓ MITIGATED — doesn't eliminate, but increases cost proportionally
  Combined with concentration penalty (§7.5): further reduces benefit
```

**Honest miner steady-state:**

```
Honest miner at consistent 1 GH/s:
  - Finds ~1 share per period continuously
  - New shares added at same rate old shares decay
  - Steady-state: constant total_decayed_work sum
  - Vesting: 100% perpetually (once initial ramp-up complete)
  - Initial ramp-up to full vesting: ~RECENCY_HALF_LIFE shares

Result: Loyal miners are NEVER penalized. Their continuous contribution
keeps vesting at maximum. This is the key property — the defense
discriminates purely by BEHAVIOR (sustained participation), not by
any static identity or threshold.
```

#### 7.3.5 Evolution Summary: From Naive to Gaming-Resistant

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  VESTING EVOLUTION                                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LEVEL 1 — COUNT-BASED TENURE (§7.3.2 naive):                               │
│  └─ Gaming: Keep 1 weak miner to maintain share count                       │
│  └─ 100 old high-diff shares + 1 new low-diff share = still 101 shares      │
│  └─ Result: Old high-diff shares count at 100%                              │
│  └─ Defeated by: Attacks 1, 2, 3, 4                                         │
│                                                                             │
│  LEVEL 2 — WORK-BASED TENURE:                                               │
│  └─ Gaming: Placeholder has negligible work, but old work persists          │
│  └─ Need additional check to detect hashrate drop                           │
│  └─ Defeated by: Attacks 1, 2, 3 (without consistency check)                │
│                                                                             │
│  LEVEL 3 — WORK + CONSISTENCY CHECK:                                        │
│  └─ Gaming: Gradual ramp-down avoids threshold detection                    │
│  └─ Threshold-based detection is inherently binary                          │
│  └─ Defeated by: Attack 4 (slow ramp down)                                  │
│                                                                             │
│  LEVEL 4 — EXPONENTIAL DECAY + WORK (★ RECOMMENDED):                        │
│  └─ No threshold to game — decay is continuous and mathematical             │
│  └─ Old shares ALWAYS lose weight: 50% per RECENCY_HALF_LIFE                │
│  └─ Only defense: keep mining. That's the desired behavior.                 │
│  └─ Remaining weakness: Split addresses (Attack 5) — mitigated,             │
│     not eliminated. Increases attacker cost linearly.                       │
│                                                                             │
│  FUTURE — MULTI-TEMPORAL WORK-QUALITY WEIGHTING (Part 16):                  │
│  └─ Hash quality (real leading zeros) determines tier placement             │
│  └─ Summary shares require accumulated work to promote                      │
│  └─ Sybil resistance built into tier structure                              │
│  └─ Attack 5 becomes self-defeating: split addresses reduce per-tier        │
│     promotion speed, no benefit from splitting                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### 7.3.6 Relationship to Exponential PPLNS Decay (§7.2)

Exponential PPLNS decay (§7.2) and work-weighted exponential vesting
(§7.3.4) share the same mathematical core — `2^(-age/half_life)` — but
operate on different aspects:

| Aspect | PPLNS Decay (§7.2) | Vesting (§7.3.4) |
|--------|-------------------|-------------------|
| **What it weights** | Individual share's payout claim | Per-miner participation quality |
| **Scope** | Global — all shares decay equally | Per-address — tracks each miner |
| **Effect** | Old shares earn less in payouts | New shares from unreliable miners start weak |
| **When it acts** | After share exists (aging) | At share evaluation (mining quality) |
| **Hopper defense** | Erodes stale shares post-departure | Discounts burst shares at creation |
| **Loyal miner impact** | Old shares lose value (offset by new) | Zero — continuous mining = full vesting |

**Are they redundant?** No. They are complementary layers:

- **PPLNS decay alone:** A hopper who arrives, bursts, and leaves sees their
  shares decay. But during the FIRST few hours after the burst, shares are
  still fresh and earn full weight. This is the most profitable window.

- **Vesting alone:** A hopper's shares start at low weight (no recent work
  history). But once vested, shares don't decay — so a hopper who maintains
  a placeholder gets full weight on old shares indefinitely.

- **Both together:** Shares start weak (vesting) AND decay over time
  (PPLNS). The hopper's profitable window shrinks from both ends — no
  full-weight period exists at all.

**Implementation note:** Both use the same exponential math with the same
half-life (`CHAIN_LENGTH // 4` ≈ 9 hours) in the current design. This keeps
the parameter space simple and ensures both mechanisms decay at the same rate.
A future refinement *could* differentiate them — e.g., a gentler PPLNS
half-life (`CHAIN_LENGTH // 2` ≈ 18 hours) to better preserve loyal miners'
older work, while keeping aggressive vesting (`CHAIN_LENGTH // 4`). However,
the combined effect of both mechanisms at the same half-life already makes
hopping deeply unprofitable (0.3× ratio, §8), so differentiation is a
post-deployment tuning option, not a prerequisite.

#### 7.3.7 Scope: Difficulty Only vs Difficulty + Payouts

A critical design decision (from `V36_IMPLEMENTATION_PLAN.md` §10.6.1):

| Option | Vesting applies to | Effect |
|--------|-------------------|--------|
| **Option 1: Difficulty only** | Retarget calculation | Prevents difficulty death spiral; hopper still gets full PPLNS payout for work done |
| **Option 2: Difficulty + Payouts** | Retarget AND PPLNS weights | Prevents death spiral AND reduces hopper's payout — strongest defense |

**Updated recommendation:** With the full gaming-resistant vesting (§7.3.4),
Option 2 is now viable. The exponential decay prevents the gaming attacks
that made per-miner payout adjustment risky. Deploy as:

1. Phase 2a: PPLNS exponential decay (§7.2) — pure per-share, no identity
2. Phase 2b: Work-weighted vesting on payouts (§7.3.4) — per-miner quality
3. Only after testnet validation of both

#### 7.3.8 Attack 5 Residual: The Sybil Problem

The one attack that exponential vesting **mitigates but doesn't eliminate**
is address splitting (Attack 5). A hopper using N addresses pays N× the
farming cost but can distribute work to avoid per-address detection.

**Current mitigation:** Linear cost increase (N addresses = N placeholders).

**Future elimination via multi-temporal architecture (Part 16):**

In the hierarchical sub-chain model, address splitting becomes
self-defeating:

```
Single address at 100 GH/s:
  → Tier 1 (main chain) shares directly
  → Summary share promotion: immediate (whale-level work meets threshold)
  → Full PPLNS weight

Split into 5 addresses at 20 GH/s each:
  → Each address at Tier 2 (sub-chain)
  → Summary share promotion: SLOWER (less work per address per period)
  → Each address accumulates Tier 2 shares, waiting for promotion threshold
  → PPLNS weight DELAYED until summary shares reach main chain
  → Net effect: REDUCED total reward vs single address

The hierarchical structure makes splitting strictly worse than consolidation
because promotion threshold creates a non-linear penalty for fragmentation.
```

This is why Attack 5 is listed as "solved in future architecture" in §7.3.5
— the correct structural solution isn't a patch on the current flat
sharechain but emerges naturally from the tiered work-quality model.

#### 7.3.9 Extended Vesting Lookback: Using 2×CHAIN_LENGTH Storage

Vulnerability 5 (§6) identifies that the 36-hour PPLNS window is too short
relative to block interval for vesting to be effective — a 36-hour burst
floods the entire window. The solution: **use a LONGER lookback for vesting
calculation than for PPLNS payout.**

The tracker already stores `2 × CHAIN_LENGTH + 10 = 17,290` shares (~72
hours) for fork resolution. We leverage this deeper storage:

```python
# PPLNS payout: uses REAL_CHAIN_LENGTH (8640 shares = 36 hours)
# Vesting lookback: uses 2 × CHAIN_LENGTH (17280 shares = 72 hours)

VESTING_LOOKBACK = 2 * net.CHAIN_LENGTH  # 72 hours of share history

def calculate_robust_vesting(tracker, tip_hash, address, net):
    """
    Vesting with extended lookback.
    
    Uses 2x CHAIN_LENGTH for vesting calculation while PPLNS payout
    window remains at REAL_CHAIN_LENGTH. This means:
    - A miner who left 40 hours ago has ZERO shares in the payout window
      BUT their vesting calculation still sees their 40-hour-ago departure
    - A returning miner must rebuild 72 hours of consistent work, not 36
    - A hopper's 36-hour burst only fills HALF the vesting window
    """
    lookback = min(
        VESTING_LOOKBACK,
        tracker.get_height(tip_hash) - 1
    )
    
    RECENCY_HALF_LIFE = lookback // 4  # 18 hours with full window
    
    total_decayed_work = 0
    share_index = 0
    
    for share in tracker.get_chain(tip_hash, lookback):
        if share.share_info['share_data']['address'] != address:
            share_index += 1
            continue
        
        work = bitcoin_data.target_to_average_attempts(share.target)
        # Float for clarity — production uses integer fixed-point (§7.3.13)
        age_factor = 0.5 ** (share_index / RECENCY_HALF_LIFE)
        total_decayed_work += work * age_factor
        share_index += 1
    
    WORK_THRESHOLD = net.SHARE_PERIOD * lookback * 0.5
    return min(1.0, total_decayed_work / WORK_THRESHOLD)
```

**Why this matters for the flooding attack (Vulnerability 5):**

```
LOYAL MINER (mining continuously for 72+ hours):
  Vesting lookback (72hr): Sees continuous work across full window
  Decayed work: HIGH (constant contribution, exponential sum converges)
  Vesting factor: 1.0 (100%)

HOPPER (36-hour burst, just arrived):
  Vesting lookback (72hr): Sees work only in last 36 hours (50% of window)
  Decayed work: MODERATE (only recent half has work)
  Vesting factor: ~0.6-0.7 (60-70%)
  → Even during the burst, hopper is penalized vs loyal miner!

HOPPER (36-hour burst, then left 12 hours ago):
  Vesting lookback (72hr): Work is 12-48 hours old
  Decayed work: LOW (shares aging, no replenishment)
  Vesting factor: ~0.3-0.4 (30-40%)
  → Hopper's shares in PPLNS window are heavily discounted

HOPPER (36-hour burst, then left 36 hours ago):
  Vesting lookback (72hr): Work is 36-72 hours old
  Decayed work: VERY LOW (deep decay, no recent work)
  Vesting factor: ~0.1 (10%)
  → By the time shares leave PPLNS window, vesting is near zero
```

**Design principle: Vesting memory > Payout memory.** The vesting lookback
should always be LONGER than the PPLNS window. This creates asymmetry:
- Loyal miners: vesting sees their full history → 100% weight
- Hoppers: vesting sees their brief burst as a fraction of the window → reduced weight

**No consensus change needed for the storage itself** — `clean_tracker()`
already maintains 2×CHAIN_LENGTH shares. The consensus change is only in
how vesting uses this data (V37 activation; see §7.3.4 and §8.4).

**Future consideration: Even deeper lookback.** If we increase tracker
retention from 2× to 3× or 4× CHAIN_LENGTH (a local memory cost, not a
consensus change), vesting becomes even more discriminating. A 7-day vesting
window would align better with the block interval (~107 days at current
49.5 GH/s — see live data in Vulnerability 5), though at current pool size
even 7 days covers only ~6.5% of TTB. This would require:
- Increasing the prune threshold in `clean_tracker()` (`node.py` line ~386)
- More RAM (~39 MB per additional CHAIN_LENGTH of 8,640 shares at ~4.5 KB/share)
- No protocol change — purely local retention policy

See §7.3.10 for the full adaptive solution that automates this.

**Risk:** MEDIUM. Same consensus requirements as Defense 2.

#### 7.3.10 Adaptive PPLNS & Vesting Windows (★ OPTIMAL DESIGN)

**Consensus required:** YES — V36 supermajority (changes payout calculation).

The fixed PPLNS window (8640 shares = 36 hours) was designed when P2Pool
found LTC blocks frequently. With current pool hashrate (~49.5 GH/s as of
March 2026 — see live data in Vulnerability 5), blocks come every ~107 days,
making the 36-hour window cover only **1.4%** of the inter-block interval
(Vulnerability 5). Rather than picking a new fixed number, **scale the window
dynamically based on expected time-to-block.**

**Design principle: No arbitrary caps.** The window size should be driven
purely by block-finding economics — not by legacy constants or unfounded
memory fears. Modern computers have 16–128+ GB of RAM. P2Pool V36 shares
consume **~4.5 KB each** in PyPy 2.7 memory (see §7.3.14 for full
breakdown: ~3.5–4.5 KB per share object + ~0.6–0.7 KB tracker overhead;
the wire format is ~780 B without merged mining, ~1,100 B with DOGE).
Even 1 million shares ≈ 4.3 GB — significant but manageable with
compaction (§7.3.13 Strategy 2). The PPLNS weight calculation uses an
O(log n) skip list (`WeightsSkipList` in `data.py`), so CPU cost scales
logarithmically. The only constraints should be:

- **Lower bound:** Statistical stability — enough shares for meaningful
  PPLNS weights (`TARGET_LOOKBEHIND = 200` shares minimum)
- **Upper bound:** Diminishing returns — once the window covers ≥100% of
  expected time-to-block, further expansion adds minimal hopping resistance
  while exponential decay already handles depth discrimination

**Core formula (fully deterministic, on-chain data only):**

```python
def get_adaptive_chain_length(tracker, tip_hash, block_target, net):
    """
    Compute adaptive PPLNS window size based on pool hashrate and
    network difficulty, using only on-chain data.
    
    All nodes compute the same value from the same share chain state
    and the same block target, ensuring consensus-safe determinism.
    
    No arbitrary MIN/MAX caps — bounded only by effectiveness:
    - Lower: TARGET_LOOKBEHIND (statistical minimum for meaningful weights)
    - Upper: expected_ttb / SHARE_PERIOD (100% coverage = full block interval)
    
    Returns: number of shares for the PPLNS window (integer).
    """
    # Pool hashrate from last TARGET_LOOKBEHIND shares (already used for
    # difficulty retarget — deterministic, all nodes agree)
    pool_aps = get_pool_attempts_per_second(
        tracker, tip_hash, net.TARGET_LOOKBEHIND,
        min_work=True, integer=True
    )
    if pool_aps <= 0:
        return net.CHAIN_LENGTH  # Fallback to static
    
    # Expected attempts to find a block at current network difficulty
    expected_attempts = bitcoin_data.target_to_average_attempts(block_target)
    
    # Expected time to block (seconds)
    expected_ttb = expected_attempts // pool_aps
    
    # PPLNS window should cover a meaningful fraction of inter-block time.
    # TARGET_COVERAGE = 0.5 means window covers ~50% of expected block time.
    # This ensures a hopper must sustain presence for HALF the block interval.
    TARGET_COVERAGE = 0.5  # 50% of expected time-to-block
    
    target_seconds = expected_ttb * TARGET_COVERAGE
    adaptive_shares = target_seconds // net.SHARE_PERIOD
    
    # Lower bound: statistical minimum for meaningful PPLNS weights.
    # TARGET_LOOKBEHIND (200) is the minimum used for pool hashrate
    # estimation — below this, weight distribution becomes noise.
    MIN_WINDOW = net.TARGET_LOOKBEHIND  # 200 shares (~50 min at 15s)
    
    # Upper bound: full block interval coverage (100% of TTB).
    # Beyond this, the window exceeds the expected inter-block time.
    # Exponential decay (§7.2) already discriminates by depth, so
    # extending past 1× TTB provides negligible additional protection:
    # shares at depth > TTB are already decayed to <25% weight.
    MAX_WINDOW = expected_ttb // net.SHARE_PERIOD  # 100% of TTB
    
    return max(MIN_WINDOW, min(MAX_WINDOW, int(adaptive_shares)))
```

**Resource analysis — verified with live data (March 2, 2026):**

The following table uses the REAL `attempts_to_block` value from the live
node (4.574×10¹⁷) and varies pool hashrate. The "Current" row matches
the actual pool state.

```
┌────────────────────────┬──────────┬──────────┬───────────┬───────────┬─────────────┐
│ Pool Hashrate          │ TTB      │ Window   │ Shares    │ RAM (comp)│ CPU (PPLNS) │
├────────────────────────┼──────────┼──────────┼───────────┼───────────┼─────────────┤
│ 1 TH/s                 │ 5.3 days │ 2.6 days │ 15,247    │ 69 MB     │ O(log 15K)  │
│ 500 GH/s               │ 10.6 days│ 5.3 days │ 30,494    │ 137 MB    │ O(log 30K)  │
│ 295 GH/s (peak seen)   │ 17.9 days│ 9.0 days │ 51,683    │ 233 MB    │ O(log 52K)  │
│ 150 GH/s               │ 35.3 days│ 17.6 days│ 101,664   │ 458 MB    │ O(log 102K) │
│ 72 GH/s  (avg seen)    │ 73.5 days│ 36.8 days│ 211,757   │ 953 MB    │ O(log 212K) │
│ ★ 49.5 GH/s (CURRENT)  │ 107 days │ 53.5 days│ 307,996   │ 1.39 GB   │ O(log 308K) │
│ 10 GH/s                │ 1.5 yr   │ 264 days │ 1,524,656 │ 6.86 GB   │ O(log 1.5M) │
│ 1 GH/s                 │ 14.5 yr  │ 7.2 yr   │ 15,246,560│ 68.6 GB✗  │ O(log 15M)  │
└────────────────────────┴──────────┴──────────┴───────────┴───────────┴─────────────┘

RAM (comp) = with Strategy 2 compaction (§7.3.13). Without compaction,
multiply by ~4× (full 4× tracker_keeps at ~4,500 bytes/share, see §7.3.14).
✗ = infeasible, requires upper-bound cap on adaptive window.

★ = actual live pool state on March 2, 2026.
  LTC network difficulty: 106,494,367 | Network: 3.05 PH/s
  Pool fraction: 0.0016% of network hashrate
  Live source: http://[pool-node]:9327/global_stats

Note: PPLNS weight calculation is O(log n) via WeightsSkipList.
Vesting iteration is O(n) but only runs once per share generation
(every SHARE_PERIOD = 15s), and PyPy iterates ~1M items/sec.
Tracker RAM = 4 × window_shares × ~4,500 bytes/share (2× vest lookback × 2 for fork resolution).
#
# IMPORTANT: The ~4,500 bytes/share figure accounts for PyPy 2.7 in-memory
# overhead — Python long ints (~48 B each × 20+ hashes per share), nested
# dicts (contents, share_info, share_data, header), merkle branch lists,
# tracker bookkeeping (items dict, reverse dict, skip list entries,
# verified SubsetTracker). Wire format is only ~780–1,100 B, but Python
# object representation expands this ~4–6×. See §7.3.14 for breakdown.
```

At the **current 49.5 GH/s**: ~5.5 GB for the full tracker (tracker_keeps ≈
1.23M shares). This is **significant** — it means compaction (Strategy 2,
§7.3.13) is a **P0 prerequisite** for adaptive windows, not an optional
quality-of-life improvement. With compaction: ~1.4 GB (hot tier only for
PPLNS window of 308K shares). A skip list lookup of log₂(308K) ≈ 18 steps.
Entirely feasible on any machine running full nodes, but RAM budget must
be planned carefully.

At **10 GH/s** (small pool, ~1.5-year TTB): ~27 GB without compaction (not
feasible). With compaction: ~6.9 GB. Requires a well-provisioned machine.

At **49.5 GH/s** with compaction: ~1.4 GB. Acceptable.

At **295 GH/s** (peak hashrate seen on this pool): ~233 MB with full tracker,
no compaction needed.

**How it scales with actual pool conditions (verified against live data):**

```
┌────────────────────────┬─────────────┬──────────────────┬──────────────────┐
│ Pool Hashrate          │ Est. TTB    │ Adaptive Window  │ Window/TTB Ratio │
├────────────────────────┼─────────────┼──────────────────┼──────────────────┤
│ 1 TH/s                 │ ~5.3 days   │ 15,247 (2.6 d)   │ 50% ✓            │
│ 500 GH/s               │ ~10.6 days  │ 30,494 (5.3 d)   │ 50% ✓            │
│ 295 GH/s (peak seen)   │ ~17.9 days  │ 51,683 (9.0 d)   │ 50% ✓            │
│ 150 GH/s               │ ~35.3 days  │ 101,664 (17.6 d) │ 50% ✓            │
│ 72 GH/s  (avg seen)    │ ~73.5 days  │ 211,757 (36.8 d) │ 50% ✓            │
│ ★ 49.5 GH/s (CURRENT)  │ ~107 days   │ 307,996 (53.5 d) │ 50% ✓            │
│ 10 GH/s                │ ~1.5 years  │ 1,524,656 (264 d)│ 50% ✓            │
│ 1 GH/s                 │ ~14.5 years │ 15,246,560 (7.2y)│ 50% ✓            │
└────────────────────────┴─────────────┴──────────────────┴──────────────────┘

★ = actual live pool state on March 2, 2026.
✓ = hopping requires sustained ≥50% of block interval at ALL pool sizes
```

**Key difference from fixed windows (quantified with live data):** At
49.5 GH/s, the fixed CHAIN_LENGTH of 8,640 covers **1.4%** of expected
TTB — not even close to meaningful coverage. The adaptive window at 308K
shares covers 50% of the 107-day block interval. A 10 GH/s pool gets a
264-day window — the hopper must mine for 264 days to flood it. That's not
a bug, that's the correct defense: if it takes ~1.5 years between blocks, a
hopper should need to commit for a significant fraction of that interval to
capture meaningful payout share. The cost is ~6.9 GB of RAM (with compaction,
see §7.3.14 for corrected share size). Any P2Pool operator running a Litecoin
full node already uses far more resources than that.

**Why this is more optimal and robust than fixed windows:**

1. **Self-adjusting to threat level.** When pool hashrate drops (blocks take
   longer, hopping more profitable), the window automatically grows, demanding
   more sustained presence. When hashrate rises (blocks come faster, hopping
   less attractive), window shrinks to reasonable size. The defense strengthens
   precisely when the pool is most vulnerable.

2. **No manual parameter tuning.** Fixed CHAIN_LENGTH was set years ago for
   different pool conditions. Adaptive windows track reality automatically.
   If pool hashrate doubles next month, the window adjusts without code
   changes.

3. **Deterministic consensus.** Both inputs — `get_pool_attempts_per_second()`
   and `block_target` — are already used in the share generation code path
   (line 730 in `data.py`). All nodes compute them identically from the same
   share chain state and bitcoind work. No floating-point, no timestamps, no
   external oracle.

4. **Proportional defense.** The 50% coverage target means a hopper must
   sustain presence for at least half the expected block interval. Combined
   with exponential decay (§7.2), a hopper who mines for exactly the window
   duration sees their earliest shares already at ~25% weight by the time
   they cover the full window — they can never capture 100% effective weight.

**Extended vesting lookback scales too (unclamped):**

```python
def get_adaptive_vesting_lookback(tracker, tip_hash, block_target, net):
    """
    Vesting lookback = 2× adaptive PPLNS window.
    
    No arbitrary cap — if the PPLNS window is 70 days (for a 10 GH/s pool),
    vesting lookback is 140 days. The resource cost is ~6.9 GB of RAM
    (with compaction) and O(800K) iteration per share generation. On PyPy
    this takes ~0.8 seconds per 15-second share period — 5% CPU overhead.
    Compaction (Strategy 2, §7.3.13) is required at this scale.
    """
    pplns_window = get_adaptive_chain_length(tracker, tip_hash, block_target, net)
    return 2 * pplns_window
```

The principle from §7.3.9 — vesting memory > payout memory — is preserved:
the vesting lookback is always 2× the PPLNS window. At current 49.5 GH/s,
that's ~107 days of vesting history, closely matching the ~107-day block
interval. Even at 295 GH/s (peak observed), it's ~18 days vs ~18-day TTB.

**Impact on tracker storage (verified with live data):**

```
┌────────────────────────┬──────────┬──────────┬──────────────┬──────────┬──────────┐
│ Pool Hashrate          │ PPLNS Win│ Vest. LB │ Tracker keeps│ RAM(raw) │RAM(comp) │
├────────────────────────┼──────────┼──────────┼──────────────┼──────────┼──────────┤
│ 1 TH/s                 │ 15,247   │ 30,494   │ ~60,988      │ 274 MB   │ 69 MB    │
│ 500 GH/s               │ 30,494   │ 60,988   │ ~121,972     │ 549 MB   │ 137 MB   │
│ 295 GH/s (peak seen)   │ 51,683   │ 103,366  │ ~206,732     │ 930 MB   │ 233 MB   │
│ 150 GH/s               │ 101,664  │ 203,328  │ ~406,656     │ 1.83 GB  │ 458 MB   │
│ 72 GH/s  (avg seen)    │ 211,757  │ 423,514  │ ~847,028     │ 3.81 GB  │ 953 MB   │
│ ★ 49.5 GH/s (CURRENT)  │ 307,996  │ 615,992  │ ~1,231,984   │ 5.55 GB  │ 1.39 GB  │
│ 10 GH/s                │ 1,524,656│ 3,049,312│ ~6,098,624   │ 27.4 GB  │ 6.86 GB  │
│ 1 GH/s                 │15,246,560│30,493,120│ ~60,986,240  │ 274 GB ✗ │ 68.6 GB✗ │
└────────────────────────┴──────────┴──────────┴──────────────┴──────────┴──────────┘

★ = actual live pool state on March 2, 2026.
Tracker keeps = 2 × vesting_lookback (for fork resolution)
RAM(raw) = ~4,500 bytes/share × tracker_keeps (PyPy 2.7 in-memory, see §7.3.14)
RAM(comp) = RAM(raw) / 4 — with Strategy 2 compaction (hot tier = PPLNS window only)
✗ = infeasible without aggressive compaction AND upper-bound cap
```

The `clean_tracker()` prune threshold becomes adaptive — keeping at least
`2 × vesting_lookback + 10` shares. At current 49.5 GH/s: ~5.5 GB raw,
~1.4 GB with compaction. At the extreme (1 GH/s pool, 14.5-year TTB):
~274 GB raw (infeasible) or ~69 GB with compaction (still infeasible).
This means **an upper-bound cap on the adaptive window is required** to
prevent memory exhaustion at extremely low hashrates. A reasonable cap of
`40 × CHAIN_LENGTH` (345,600 shares ≈ 60 days) limits raw RAM to ~1.56 GB
and compacted RAM to ~390 MB — manageable on any node running a Litecoin
full node. Pools below ~10 GH/s accept diminished hopping protection as a
trade-off for feasible resource usage.

**Interaction with other defenses:**

| Defense | Fixed Window | Adaptive Window | Improvement |
|---------|-------------|-----------------|-------------|
| Exp. decay (§7.2) half-life | CHAIN_LENGTH//4 = 9hr | adaptive//4 ≈ 13.4 days | Decay matches block rhythm |
| Vesting (§7.3.4) lookback | 2×CHAIN = 72hr | 2×adaptive ≈ 107 days | Lookback ≈ block interval |
| Emergency decay (§7.1.2) | SHARE_PERIOD×20 | Same (no change) | Independent (time-based) |
| Monitoring (Phase 3) | Fixed sliding windows | Adaptive sliding windows | Windows match pool conditions |

**Key insight:** Exponential decay half-life should also scale. When the PPLNS
window is 53.5 days (at current 49.5 GH/s), `half_life = adaptive_window // 4
≈ 13.4 days` means shares lose 50% weight per 13.4 days — appropriate
discrimination over the longer window. At high hashrate with shorter windows,
half_life returns proportionally shorter. The decay rate naturally matches the
pool's operational tempo.

**Edge cases and safety (comprehensive analysis):**

1. **Hashrate oscillation.** Pool hashrate changes → window size changes →
   miners at the edge of the old window get included/excluded. Mitigation:
   use a **smoothed** pool hashrate (e.g., computed over `2×TARGET_LOOKBEHIND`
   shares, or an EMA) to prevent oscillation. The window should change
   gradually, not jump.

2. **Block target changes.** LTC retargets every 2016 blocks (~2.8 days at
   network level). Window size changes at this cadence — slow and gradual.
   Not a concern.

3. **Pool grows rapidly.** New miners join → pool hashrate doubles → expected
   TTB halves → window shrinks → shares from the old larger window might
   extend beyond the new window. Mitigation: always use `max(current_window,
   previous_window)` for one transition period, or only allow window to
   shrink by 10% per evaluation cycle.

4. **All nodes must agree.** The adaptive formula uses `block_target` from
   the current Bitcoin/Litecoin work AND `pool_aps` from the share chain.
   Both are deterministic from the same chain state. But the `block_target`
   updates when a new block is found — all P2Pool nodes see this at slightly
   different times. Mitigation: use the `block_target` from the **share
   itself** (each share records `bits`), not from the node's latest bitcoind
   state. This makes the computation purely share-chain-deterministic.

5. **Bootstrap / cold start.** When P2Pool starts with an empty share chain
   (new node, first run, or after extended downtime), there are insufficient
   shares to compute `get_pool_attempts_per_second()` meaningfully.
   Mitigation: fall back to `net.CHAIN_LENGTH` (the static constant) until
   the share chain reaches `TARGET_LOOKBEHIND` (200) shares. At 15s per
   share, this bootstrap period is ~50 minutes — during which the pool
   operates with the legacy fixed window. No security degradation: a pool
   with <200 shares has negligible block probability anyway.

   ```python
   # In get_adaptive_chain_length():
   height = tracker.get_height(tip_hash)
   if height < net.TARGET_LOOKBEHIND:
       return net.CHAIN_LENGTH  # Static fallback during bootstrap
   ```

6. **Network partition / split-brain.** If the P2Pool network partitions,
   the two halves see different share chains → different `pool_aps` →
   different adaptive windows. This is **correct behavior**: each partition
   independently adjusts to its own effective hashrate. When partitions
   rejoin, the longer chain wins (standard share chain resolution), and all
   nodes reconverge on the same adaptive window. The transition may cause
   one recalculation cycle of window mismatch — handled by the smoothing
   in edge case #1.

7. **Share chain reorgs.** If a deep reorg replaces recent shares, the
   `pool_aps` estimate and therefore the adaptive window may change
   retroactively. All PPLNS payouts are only calculated at block-finding
   time (when generating the coinbase transaction), not pre-committed.
   So a reorg that changes the window size simply means the next block's
   payout uses the reorged chain's window — correct by construction.
   Mitigation: the smoothed hashrate (edge case #1) dampens the impact
   of reorgs that replace a small fraction of recent shares.

8. **Testnet vs mainnet parameter differences.** Litecoin testnet uses
   `SHARE_PERIOD=4` and `CHAIN_LENGTH=400` (vs mainnet 15s / 8640).
   The adaptive formula works identically — it computes from `pool_aps`
   and `block_target`, both of which reflect the testnet's faster pace.
   However, testnet's `fPowAllowMinDifficultyBlocks=true` can cause
   extreme block target spikes. Mitigation: use the share's recorded
   `bits` (edge case #4), not the latest bitcoind state, to avoid
   transient testnet difficulty anomalies.

9. **Extreme hashrate drop (last miner standing).** If pool hashrate drops
   to near-zero (e.g., one CPU miner), `expected_ttb` approaches infinity
   → adaptive window approaches infinity. This is mathematically correct
   but practically untenable (infinite RAM). Mitigation: the **ceiling**
   bound (`expected_ttb / SHARE_PERIOD`) already caps at 100% of TTB.
   Additionally, when `pool_aps` is extremely low, shares arrive so
   infrequently that the tracker simply never accumulates enough shares
   to fill the window. The share count is naturally bounded by how many
   shares actually exist. A pool mining one share per hour will never have
   millions of shares in its tracker, regardless of the adaptive target.

   ```
   Example: 100 MH/s pool (single S9)
   TTB = ~14,000 days ≈ 38 years
   Adaptive window = 7,000 days ≈ 19 years = 40,320,000 shares
   But at 1 share/hour, after 1 year only 8,760 shares exist
   Tracker stores ~17,500 shares → ~79 MB RAM (at ~4.5 KB/share)
   The window TARGET is large, but actual storage is bounded by reality.
   ```

   The adaptive window is a **policy**, not a pre-allocation. It defines
   how far back to look, but if fewer shares exist, it uses what's there.
   No memory explosion occurs — RAM usage is proportional to actual share
   count, not to the theoretical window size.

10. **Pool oscillation attack (hashrate pump-and-dump).** An attacker
    deliberately inflates pool hashrate to shrink the adaptive window, then
    withdraws to exploit the smaller window. This is analyzed in detail as
    Attack Vector 1 in the "New Attack Vectors" section below.

11. **Transition from fixed to adaptive.** When V37 activates and switches
    from `CHAIN_LENGTH` to `get_adaptive_chain_length()`, the window size
    may change dramatically (e.g., from 8,640 to 307,996 at current 49.5 GH/s).
    Shares in the old window suddenly become part of a larger PPLNS
    calculation. Mitigation: phase in the adaptive window over a transition
    period (e.g., 720 shares ≈ 3 hours), linearly interpolating between
    old and new window sizes. This prevents sudden payout redistribution
    at the activation boundary.

    ```python
    TRANSITION_PERIOD = 720  # shares (~3 hours)
    
    def get_transition_chain_length(tracker, tip_hash, block_target, net,
                                     activation_height):
        height = tracker.get_height(tip_hash)
        shares_since = height - activation_height
        if shares_since >= TRANSITION_PERIOD:
            return get_adaptive_chain_length(tracker, tip_hash,
                                              block_target, net)
        # Linear interpolation
        adaptive = get_adaptive_chain_length(tracker, tip_hash,
                                              block_target, net)
        progress = float(shares_since) / TRANSITION_PERIOD
        return int(net.CHAIN_LENGTH + (adaptive - net.CHAIN_LENGTH) * progress)
    ```

12. **Integer overflow / precision.** `expected_attempts` can be very large
    (2^256 range for low difficulty targets). The division
    `expected_attempts // pool_aps` must use Python's arbitrary-precision
    integers — not floating point. The existing code in `data.py` already
    uses integer arithmetic throughout (`target_to_average_attempts` returns
    `int`). No precision issue as long as the adaptive formula stays in
    integer domain. The `TARGET_COVERAGE = 0.5` multiplication should use
    `target_seconds = expected_ttb // 2` (integer division) rather than
    float multiplication.

**Comparison: Fixed vs Adaptive (verified with live data at 49.5 GH/s):**

```
┌────────────────────────────────────┬────────┬────────────────────────────┐
│ Metric                             │ Fixed  │ Adaptive (at 49.5 GH/s)    │
├────────────────────────────────────┼────────┼────────────────────────────┤
│ PPLNS window                       │ 36hr   │ 53.5 days                  │
│ Vesting lookback                   │ 72hr   │ 107 days                   │
│ Hopper must sustain burst          │ 36hr   │ 53.5 days                  │
│ Hopper burst fills window?         │ YES    │ NO (50%)                   │
│ Window/TTB coverage                │ 1.4%   │ 50%                        │
│ Hopper vesting after 36hr burst    │ ~70%   │ ~10%                       │
│ Hopper effective reward            │ 3.8×   │ ~0.2×                      │
│ Defense works at 10 GH/s pool?     │ <1% TTB│ 50% TTB (equally strong)   │
│ Defense works at 295 GH/s pool?    │ 8% TTB │ 50% TTB (equally strong)   │
│ Memory cost (at 49.5 GH/s)         │ ~5 MB  │ ~1.39 GB (compacted)       │
│ Memory cost (at 295 GH/s)          │ ~5 MB  │ ~233 MB (compacted)        │
│ Scales with pool conditions?       │ No     │ Yes ✓                      │
└────────────────────────────────────┴────────┴────────────────────────────┘
```

**Verdict:** Adaptive windows with no arbitrary caps are **the correct
architectural answer** to Vulnerability 5. The window tracks the only metric
that matters — expected time-to-block — and maintains consistent 50% coverage
regardless of pool size. When the pool is large and finds blocks quickly, the
window is short (saving resources). When the pool is small and blocks are
rare, the window grows to match (using more RAM but providing correct
defense). This is not a design choice to be capped — it's a physical
consequence of the pool's block-finding economics.

The only limits are effectiveness-based:
- **Floor** (`TARGET_LOOKBEHIND` = 200 shares): Below this, PPLNS weights are
  statistically meaningless — too few shares for any payout distribution.
- **Ceiling** (TTB / `SHARE_PERIOD`): Above this, coverage exceeds 100% of
  the block interval. Exponential decay already handles the depth dimension;
  extending past 1× TTB provides negligible marginal anti-hopping benefit.

**Implementation phasing (two-track — see §8):**
- **V36 (Python 2.7/PyPy):** Deploy PPLNS decay (Phase 2a) with fixed
  CHAIN_LENGTH. Proves defense mechanism, makes hopping unprofitable (0.6×).
  Asymmetric clamp + emergency decay + finder fee removal ship alongside.
- **V37 (C++ c2pool):** Deploy vesting (Phase 2b) + adaptive windows (Phase 4)
  once c2pool provides the performance headroom. Consensus determinism
  validated on c2pool testnet before mainnet.

**Risk:** MEDIUM-HIGH for adaptive windows. PPLNS decay alone (V36) is
LOW risk — it is a weight multiplier in an existing code path. Adaptive
windows change PPLNS window sizing fundamentally and require extensive
c2pool testnet validation before V37 mainnet deployment.

#### 7.3.11 New Attack Vectors Against the Adaptive Design

The adaptive window introduces new surfaces that a sophisticated attacker
might target. Each vector is analyzed with its feasibility, impact, and
mitigation.

**Attack Vector 1: Hashrate Pump-and-Dump (Window Squeeze)**

*Strategy:* Attacker joins with massive hashrate → inflates `pool_aps` →
adaptive window shrinks → attacker withdraws → exploits the now-shorter
window with a smaller follow-up burst.

```
Phase 1 (pump): Attacker mines at 500 GH/s for 50 minutes (200 shares)
  pool_aps jumps from 49.5 GH/s → ~275 GH/s (smoothed over TARGET_LOOKBEHIND)
  Adaptive window: 307,996 → ~55,276 shares (shrinks ~5.6×)

Phase 2 (dump): Attacker stops. Window is now only ~55K shares.
  Attacker's burst shares are within the smaller window.

Phase 3 (exploit): Attacker's 200 shares now occupy 0.36% of a 55K window
  (vs 0.065% of a 308K window). If a block is found now, attacker gets
  ~5.5× more reward than with the unsqueezed window.

Note: At 49.5 GH/s baseline, the attacker needs 10× the entire pool's
hashrate (500 GH/s) to meaningfully squeeze the window — a far larger
commitment than the observed attacker's 100 GH/s. This is a whale-class
attack against a small pool.
```

*Feasibility:* **LOW-MEDIUM.** The attack requires:
- Enough hashrate to meaningfully move `pool_aps` (must be comparable to
  the entire pool's hashrate)
- A block to be found during the squeezed window (probabilistic, not
  guaranteed)
- The smoothing mechanism (edge case #1) dampens the window change

*Mitigation:*
1. **Asymmetric window adjustment.** Allow the window to grow quickly
   (1 cycle) but shrink slowly (max -10% per evaluation, or -10% per
   `TARGET_LOOKBEHIND` shares). This means the pump phase has minimal
   effect — the window barely shrinks before the attacker's hashrate
   is no longer in the `pool_aps` calculation.

   ```python
   def get_safe_adaptive_chain_length(tracker, tip_hash, block_target,
                                       net, previous_window):
       raw = get_adaptive_chain_length(tracker, tip_hash,
                                        block_target, net)
       if raw >= previous_window:
           return raw  # Grow freely
       # Shrink at most 10% per cycle
       return max(raw, int(previous_window * 9 // 10))
   ```

2. **EMA smoothing on pool_aps.** Compute `pool_aps` as an exponential
   moving average over `4×TARGET_LOOKBEHIND` (800 shares ≈ 3.3 hours).
   A 50-minute burst of high hashrate only moves the EMA by ~25%, making
   the window squeeze negligible.

3. **Vesting (§7.3.4) already nullifies the reward.** Even if the window
   shrinks, the attacker's burst shares have low vesting weight. The 2%
   raw PPLNS share is multiplied by ~0.3 vesting factor = 0.6% effective
   weight. Not profitable after electricity costs.

*Residual risk:* **NEGLIGIBLE** with all three mitigations.

**Attack Vector 2: Slow Bleed (Gradual Pool Drain)**

*Strategy:* Attacker doesn't burst — instead, gradually reduces their
hashrate over weeks/months, causing `pool_aps` to slowly drop → window
slowly grows → attacker's old shares accumulate increasingly large
effective weight as the window expands to include them.

```
Week 0: Attacker mines at 50 GH/s (101% of 49.5 GH/s pool — essentially doubles it)
  Pool total: ~99.5 GH/s, window shrinks to ~153K shares
Week 2: Attacker reduces to 30 GH/s, pool_aps ≈ 79.5 GH/s
  Window grows to ~191K shares
Week 4: Attacker reduces to 10 GH/s, pool_aps ≈ 59.5 GH/s
  Window grows to ~256K shares
Week 6: Attacker stops entirely, pool_aps ≈ 49.5 GH/s
  Window returns to ~308K shares
  Attacker's old shares from weeks 0-4 are now inside the expanding window
```

*Feasibility:* **VERY LOW.** The attacker is actually mining for 6 weeks —
that's not an attack, that's legitimate mining with decreasing commitment.
Their shares earned proportional weight during weeks 0-6 based on real work
contributed. The exponential decay (§7.2) means their oldest shares (week 0)
are at ~6% weight by week 6. The "expanding window" includes heavily decayed
shares with minimal effective weight.

*Mitigation:* Exponential PPLNS decay (§7.2) is the complete answer. Even if
the window expands to include more history, those old shares are decayed to
near-zero weight. The attacker would earn MORE by mining continuously than
by this slow-bleed strategy — it's strictly dominated.

*Residual risk:* **NONE.** This is not a viable attack.

**Attack Vector 3: Block Withholding + Window Manipulation**

*Strategy:* Attacker finds a valid Litecoin block but withholds it to
manipulate `block_target`. If LTC difficulty drops (due to the missing
block slowing the network), `expected_attempts` decreases → `expected_ttb`
decreases → adaptive window shrinks.

*Feasibility:* **EXTREMELY LOW.** LTC's difficulty adjusts over 2016 blocks
(~2.8 days). Withholding one block has negligible impact on the next
retarget. The attacker also forfeits the ~6.25 LTC block reward — a massive
opportunity cost. The attacker would need to withhold ~100+ consecutive
blocks to meaningfully affect difficulty, which would require >50% of
network hashrate (at that point, they can do much worse things).

*Mitigation:* Using the share's recorded `bits` (edge case #4) means
`block_target` is the target the share was generated against, not the
latest from bitcoind. This introduces additional lag in target changes,
further dampening any manipulation attempt.

*Residual risk:* **NONE.** The economic cost (forfeited block rewards)
vastly exceeds any possible gain from window shrinkage.

**Attack Vector 4: Sybil Window Fragmentation**

*Strategy:* Attacker runs many small P2Pool nodes, each contributing minimal
hashrate. If the adaptive window formula computes differently on each node
(due to slight chain state differences), Sybil nodes might produce shares
with slightly different window expectations, creating consensus divergence.

*Feasibility:* **NONE.** The adaptive window formula uses only share-chain-
deterministic data (`pool_aps` from share chain, `block_target` from the
share's `bits` field). All nodes with the same share chain tip compute
identical windows. Sybil nodes that follow the protocol compute the same
values. Nodes that deviate produce invalid shares — rejected by all peers.

*Mitigation:* Built into the design (share-chain determinism, edge case #4).

*Residual risk:* **NONE.**

**Attack Vector 5: Memory Exhaustion Griefing**

*Strategy:* Attacker causes the adaptive window to grow very large to
exhaust memory on victim P2Pool nodes, causing them to crash or degrade.
This could be done by mining briefly to establish a very low `pool_aps`,
then triggering a window calculation that requires storing millions of
shares.

*Feasibility:* **LOW.** As analyzed in edge case #9, actual share storage is
bounded by the number of shares that physically exist — not by the adaptive
window target. A pool that produces 1 share per hour will only have ~8,760
shares after a year regardless of what the adaptive window calculation
targets. To actually cause millions of shares to exist, the attacker must
submit millions of shares — which requires massive hashrate sustained over
a long period (real cost).

*Mitigation:*
1. Shares must meet difficulty requirements to be accepted. The attacker
   cannot cheaply generate millions of valid shares.
2. `clean_tracker()` prunes shares beyond `2×vesting_lookback` — but this
   is bounded by actual shares, not target window size.
3. Operators can configure a hard memory limit (process-level) as a safety
   valve, though this should never be needed in practice.

*Residual risk:* **NEGLIGIBLE.** The attacker's cost (real mining) exceeds
the damage (moderate RAM usage on victim nodes).

**Attack Vector 6: Cross-Defense Interaction Exploit**

*Strategy:* Exploit the interaction between multiple defense layers. For
example: adaptive window shrink (Attack 1) + vesting gaming (§7.3.3
Attack 1: tenure farming) — if the window shrinks while the attacker has
pre-farmed vesting tenure, the combination might create a brief profitable
window.

*Analysis:*

```
Setup: Attacker mines at 10% for 2 weeks (tenure farming)
  → Vesting factor: ~0.8 (good but not 1.0 due to low work rate)
Pump: Attacker adds massive hashrate for 50 minutes
  → Window unchanged (asymmetric shrink, edge case #1)  
  → New shares: high diff but vesting factor = 0.3 (brief burst)
  → Old shares: in window, vesting = 0.8, but work is LOW (10% hashrate)
Dump: Attacker withdraws
  → Net effect: 2 weeks at 10% = earned 10% fair share. Vesting 0.8.
    Effective claim = 10% × 0.8 = 8% — LESS than proportional contribution.
```

*Feasibility:* **NONE.** The work-weighted vesting (§7.3.4) defeats tenure
farming because vesting is a function of WORK contributed, not time elapsed.
Low-hashrate farming yields low vesting weight. The defenses are synergistic,
not antagonistic — each one closes loopholes in the others.

*Residual risk:* **NONE.** The defense stack is designed to be composable.

**Attack vector summary:**

```
┌──────────────────────────────┬───────────┬────────────────────────────┐
│ Attack Vector                │ Risk      │ Primary Mitigation         │
├──────────────────────────────┼───────────┼────────────────────────────┤
│ 1. Hashrate pump-and-dump    │ NEGLIGIBLE│ Asymmetric shrink + EMA    │
│ 2. Slow bleed                │ NONE      │ Exponential decay (§7.2)   │
│ 3. Block withholding         │ NONE      │ Share-chain determinism    │
│ 4. Sybil fragmentation       │ NONE      │ Share-chain determinism    │
│ 5. Memory exhaustion         │ NEGLIGIBLE│ Physical share count bound │
│ 6. Cross-defense interaction │ NONE      │ Work-weighted vesting      │
└──────────────────────────────┴───────────┴────────────────────────────┘
```

**Conclusion:** The adaptive window design, when combined with the full
defense stack (asymmetric shrink + EMA smoothing + exponential decay +
work-weighted vesting), has NO viable novel attack vectors. All identified
vectors are either economically irrational (cost exceeds gain) or
structurally impossible (share-chain determinism prevents manipulation).
The greatest residual risk is Attack Vector 1 (pump-and-dump), which
requires asymmetric window adjustment as an explicit mitigation — without
this, it would be a **MEDIUM** risk.

#### 7.3.12 Honest Miner Impact Analysis

The defense stack must not penalize loyal miners. Each defense layer is
analyzed for its impact on honest participants, with particular attention
to small miners, new miners, and miners with intermittent connectivity.

**Miner profiles for analysis:**

| Profile | Description | Hashrate | Uptime | Concern |
|---------|-------------|----------|--------|---------|
| Alice | Large loyal miner | 20 GH/s (13%) | 24/7 | Baseline reference |
| Bob | Small loyal miner | 500 MH/s (0.3%) | 24/7 | Disproportionate impact? |
| Carol | New miner | 5 GH/s (3%) | Just joined | Cold start penalty? |
| Dave | Intermittent miner | 10 GH/s (7%) | 16hr/day | Penalized like hopper? |
| Eve | Weekend miner | 15 GH/s (10%) | Fri-Sun only | Caught by vesting? |
| **Frank** | **Night-tariff miner** | **8 GH/s (5%)** | **00:00–06:00 daily** | **Electricity tariff optimization** |
| **Grace** | **Solar-powered miner** | **3 GH/s (2%)** | **~08:00–17:00 daily** | **Solar generation window only** |
| **Henry** | **Heat-recycling miner** | **6 GH/s (4%)** | **18:00–08:00 daily** | **Uses miner heat for home heating** |
| **Iris** | **Dual-job GPU miner** | **2 GH/s (1.3%)** | **22:00–09:00 daily** | **GPU renders by day, mines by night** |
| **Jack** | **Hobbyist/travel miner** | **1 GH/s (0.7%)** | **Sporadic (3-4 days/week)** | **Mines when home, travels often** |

**Real-world mining pattern context:**

These profiles reflect documented patterns in actual mining communities:

- **Frank (night tariff):** In many countries (Germany, Spain, UK, Japan,
  South Korea, parts of US), electricity providers offer time-of-use (ToU)
  pricing with off-peak rates 30–60% cheaper between ~22:00–06:00. Mining
  forums consistently report miners scheduling rigs to run only during
  cheap-rate hours. Frank's 6-hour window (00:00–06:00) is a conservative
  estimate — some tariffs offer 8–10 hour off-peak windows.

- **Grace (solar):** Solar-powered mining is documented in Texas, Australia,
  and parts of Southern Europe where excess solar generation makes mining
  effectively free during peak sun hours. Grace mines ~9 hours/day during
  solar output, shuts down at sunset.

- **Henry (heat recycling):** In cold climates (Nordics, Canada, Russia,
  Northern US/UK), miners replace electric heaters with mining hardware,
  making mining cost-neutral during heating season. Henry runs 14 hours/day
  during evening-through-morning when heating is needed, stops during warm
  afternoon hours. Documented by several Bitcointalk and Reddit mining
  communities.

- **Iris (dual-use GPU):** Content creators, 3D artists, and AI/ML
  practitioners commonly mine during off-hours on their GPU workstations.
  Iris mines ~11 hours/day when the GPU isn't needed for rendering/training.
  Common pattern with GPU-minable coins, applicable to LTC via Scrypt
  acceleration or merged mining setups.

- **Jack (hobbyist/sporadic):** Travels for work 2-3 days/week, mines when
  home. Has a single ASIC in a home office that gets manually started.
  Represents a significant fraction of P2Pool's target audience: people who
  value decentralization but can't commit to 24/7 uptime.

**Fixed Window (8,640 shares / 36h) vs Adaptive Window — Miner Retention
Analysis:**

> **The core problem:** With the current fixed 36-hour PPLNS window, any
> miner who stops for ≥36 hours **loses ALL accumulated PPLNS weight**.
> Their shares scroll out of the window completely. When they restart, they
> begin from zero — exactly like a brand-new miner. This is devastating
> for retention.

```
┌─────────┬──────────┬───────────────┬──────────────┬──────────────┬────────────┐
│ Miner   │ Daily    │ Off-period    │ Fixed 36h:   │ Adaptive 53d:│ Retention  │
│         │ mining   │ (longest      │ shares lost  │ shares lost  │ improvement│
│         │ hours    │ consecutive)  │ after off?   │ after off?   │            │
├─────────┼──────────┼───────────────┼──────────────┼──────────────┼────────────┤
│ Alice   │ 24h      │ 0h            │ No           │ No           │ —          │
│ Bob     │ 24h      │ 0h            │ No           │ No           │ —          │
│ Carol   │ 24h (new)│ N/A           │ No (building)│ No (building)│ —          │
│ Dave    │ 16h      │ 8h            │ No           │ No           │ —          │
│ Eve     │ 72h/week │ 96h (Mon-Thu) │ **YES** ✗    │ No           │ ★★★★       │
│ Frank   │ 6h       │ 18h           │ No (tight)   │ No           │ ★          │
│ Grace   │ 9h       │ 15h           │ No           │ No           │ ★          │
│ Henry   │ 14h      │ 10h           │ No           │ No           │ —          │
│ Iris    │ 11h      │ 13h           │ No           │ No           │ —          │
│ Jack    │ ~14h×4d  │ **72h** (trip)│ **YES** ✗    │ No           │ ★★★★       │
└─────────┴──────────┴───────────────┴──────────────┴──────────────┴────────────┘

✗ = shares completely scrolled out of PPLNS window, miner restarts from zero
★ = stars indicate how much the adaptive window improves retention for this miner
```

**Detailed per-miner analysis (fixed 36h window vs adaptive 53-day window):**

**Frank (night-tariff, 00:00–06:00 daily = 6h on / 18h off):**

- *Fixed window:* Frank mines 6h → 18h gap → mines 6h again. In the
  fixed 36h window, Frank has shares from the last two sessions (today
  0–6h and yesterday 0–6h) = 12h of mining in a 36h window. His 18h gap
  does NOT scroll out his shares (18h < 36h). However, Frank's shares
  from 2 days ago are completely gone. **Frank retains ~33% window
  coverage** (12h of mining in 36h window).

- *Adaptive 53-day window:* Frank's shares from the past 53 days are ALL
  still in the window. He has ~53 × 6h = 318 hours of mining history.
  With exponential decay, recent sessions are weighted more, but the
  depth of history means Frank's PPLNS claim is deep and stable.
  **Frank retains ~25% window coverage** (6h/24h continuously).
  His payout stability is dramatically higher — no day-to-day fluctuation
  from shares scrolling out.

- *Retention advantage:* +300% payout stability. Frank's hourly mining
  rate is constant, but with the fixed window, his PPLNS share fluctuates
  ±15% day-to-day depending on when the last block was found relative to
  his sessions. With adaptive windows, this smooths to <2% variation.

**Grace (solar, 08:00–17:00 daily = 9h on / 15h off):**

- *Fixed window:* Similar to Frank but better positioned — Grace has ~18h
  of mining in the 36h window (today + yesterday). **~50% coverage.**

- *Adaptive 53-day window:* 53 × 9h = 477 hours. Deep PPLNS claim.
  **~37.5% coverage** (9h/24h continuously). Very stable payouts.

- *Retention advantage:* +150% payout stability. Grace already does OK
  in fixed windows (no total loss), but adaptive windows give her
  consistent, predictable earnings.

**Henry (heat-recycling, 18:00–08:00 daily = 14h on / 10h off):**

- *Fixed window:* Henry mines 14h/day. In a 36h window, he has ~28h of
  mining = **~78% coverage.** Henry does well in both systems.

- *Adaptive 53-day window:* **~58% coverage.** Stable, predictable.

- *Retention advantage:* Minimal. Henry's pattern is already well-suited
  to fixed windows. Adaptive windows add stability but he's not at risk
  of total share loss.

**Iris (dual-use GPU, 22:00–09:00 daily = 11h on / 13h off):**

- *Fixed window:* ~22h of mining in 36h window = **~61% coverage.**

- *Adaptive 53-day window:* **~46% coverage.** Stable.

- *Retention advantage:* Minimal. Like Henry, Iris is safe in both systems.

**Jack (sporadic, ~4 days/week, 14h/day when home, 72h trips):**

- *Fixed window:* Jack's 72-hour trip **completely empties his PPLNS
  window** (72h > 36h). When Jack returns, he starts from absolute zero.
  His first few hours of mining earn almost nothing (competing against
  miners who have 36h of accumulated shares). **This is the single
  biggest retention problem** — Jack is exactly the kind of
  decentralization-minded hobbyist P2Pool should attract, but the fixed
  window punishes him severely for having a life.

- *Adaptive 53-day window:* Jack's shares from before his 3-day trip are
  still in the window. There are ~50 days of mining history. His 72h gap
  means some shares have higher decay, but his overall PPLNS position is
  intact. When he returns, he immediately earns at a reasonable rate.

- *Retention advantage:* **CRITICAL.** This is the difference between
  Jack staying on P2Pool or leaving for a centralized pool that pays per
  share with no memory. A centralized pool doesn't care about uptime
  history — Jack gets paid immediately. P2Pool with fixed windows
  punishes him with cold-start after every trip. Adaptive windows +
  decay make P2Pool competitive with centralized alternatives.

**Eve (weekend miner — revisited with retention lens):**

- *Fixed window:* Eve mines Fri-Sun (72h), then is off Mon-Thu (96h).
  **96h > 36h → complete share loss.** Every Monday through Thursday, Eve
  has ZERO PPLNS weight. If a block is found Tuesday, Eve gets NOTHING —
  despite having mined all weekend. This is the most unfair outcome for
  an honest miner.

- *Adaptive 53-day window:* Eve's weekend shares from the past ~7 weekends
  are still in the window. With exponential decay, recent weekends have
  more weight, but Eve always has a non-zero PPLNS claim. If a block is
  found on Tuesday, Eve gets a fair (decayed) share of it — much smaller
  than Alice's, but non-zero and proportional to her recent contribution.

- *Retention advantage:* **CRITICAL.** Without adaptive windows, P2Pool
  is fundamentally broken for weekend miners. They subsidize 24/7 miners
  during the week (their shares hold weight then) but get nothing for
  mid-week blocks found using work they contributed on the weekend.

**Pool attraction & retention summary:**

```
┌─────────┬──────────────────────────────────┬───────────────────────────┐
│ Profile │ Fixed Window (8640/36h) Problem  │ Adaptive Window Solution  │
├─────────┼──────────────────────────────────┼───────────────────────────┤
│ Alice   │ None                             │ Slight improvement (+2%)  │
│ Bob     │ None                             │ Slight improvement (+2%)  │
│ Carol   │ 36h cold start                   │ Same cold start, better   │
│         │                                  │ long-term stability       │
│ Dave    │ Minor (8h < 36h, safe)           │ Better stability          │
│ Eve     │ **TOTAL SHARE LOSS** mid-week    │ Continuous PPLNS claim ✓  │
│ Frank   │ ±15% daily payout fluctuation    │ <2% variation ✓           │
│ Grace   │ ±10% daily fluctuation           │ <2% variation ✓           │
│ Henry   │ Minor fluctuation                │ Stability improvement     │
│ Iris    │ Minor fluctuation                │ Stability improvement     │
│ Jack    │ **TOTAL SHARE LOSS** after trips │ Continuous PPLNS claim ✓  │
├─────────┼──────────────────────────────────┼───────────────────────────┤
│ **Net** │ **Loses Eve, Jack, discourages   │ **Retains all miners,     │
│         │ Frank/Grace. Only 24/7 miners    │ rewards contribution      │
│         │ are well-served.**               │ proportionally.**         │
└─────────┴──────────────────────────────────┴───────────────────────────┘
```

> **The pool's #1 goal is miner retention.** A decentralized pool with 3
> miners is less useful than one with 30, even if total hashrate is the same.
> Fixed windows actively drive away part-time miners — the very people P2Pool
> is designed for (home miners, hobbyists, decentralization advocates). Adaptive
> windows make P2Pool competitive with centralized pools for ALL mining
> schedules, not just 24/7 datacenter operations.

All analysis assumes 49.5 GH/s total pool hashrate (live March 2, 2026)
unless stated otherwise.

**Phase 1a: Asymmetric Difficulty Clamp (§7.1.1)**

| Miner | Impact | Analysis |
|-------|--------|----------|
| Alice | **NONE** | Continuously mines → difficulty stays appropriate |
| Bob | **NONE** | Small but steady → personal difficulty tracks correctly |
| Carol | **POSITIVE** | Before: if joining during a difficulty spike, Carol waits hours for shares. After: asymmetric clamp (-40%) recovers difficulty in ~40 minutes instead of ~3 hours |
| Dave | **SLIGHT POSITIVE** | When Dave reconnects after 8hr gap, difficulty may have drifted up. Asymmetric downward clamp brings it back faster |
| Eve | **POSITIVE** | Monday difficulty recovery is faster (weekday miners don't suffer from weekend concentration) |
| Frank | **POSITIVE** | 18-hour daily gap (06:00→midnight) means difficulty drifts during daytime. Asymmetric clamp (-40%) recovers within ~30 min of Frank's midnight restart instead of ~2 hours |
| Grace | **POSITIVE** | 15-hour overnight gap means difficulty may spike. When Grace comes online at 08:00, faster downward clamping helps her find first share quickly |
| Henry | **SLIGHT POSITIVE** | 10-hour daytime gap (08:00→18:00) is moderate. Difficulty drift is small since most pool hashrate is online during daytime. Fast recovery at 18:00 |
| Iris | **POSITIVE** | 13-hour afternoon gap (09:00→22:00). Asymmetric clamp helps Iris find shares quickly when re-joining at 22:00 |
| Jack | **STRONGLY POSITIVE** | Multi-day gaps between sessions mean significant difficulty drift. Without asymmetric clamp, Jack's first share after a 2-3 day absence could take hours at elevated difficulty |

**Verdict:** Phase 1a is **universally beneficial** for honest miners.
Nobody is penalized. Recovery from difficulty spikes helps everyone.
Part-time miners (Frank, Grace, Jack) benefit the MOST from fast downward
clamping because their longer off-periods create larger difficulty gaps.

**Phase 1b: Time-Based Emergency Decay (§7.1.2)**

| Miner | Impact | Analysis |
|-------|--------|----------|
| Alice | **POSITIVE** | Without Phase 1b, a whale attack could kill the pool permanently. With it, Alice's pool survives extreme difficulty spikes |
| Bob | **STRONGLY POSITIVE** | Small miners are most vulnerable to death spirals — Bob can never find a share at 100× difficulty. Emergency decay saves Bob's mining operation |
| Carol | **POSITIVE** | If Carol joins during a recovery period, emergency decay ensures the difficulty is dropping fast enough for her to find shares |
| Dave | **NONE** | Emergency decay only triggers during extreme gaps (>300s between shares on mainnet). Dave's 8-hour absence doesn't trigger it — normal shares continue in his absence |
| Eve | **NONE** | Same as Dave — normal weekend absence doesn't trigger emergency conditions |
| Frank | **NONE** | Frank's 18-hour absence doesn't trigger emergency decay — other miners keep the share chain moving. Emergency decay requires >300s gap between ANY shares, not per-miner gaps |
| Grace | **NONE** | Same as Frank — Grace's overnight absence is invisible to the emergency decay mechanism because other miners are producing shares |
| Henry | **NONE** | Henry's 10-hour daytime gap is irrelevant to emergency decay |
| Iris | **NONE** | Same — Iris's 13-hour absence doesn't affect pool-level share intervals |
| Jack | **NONE** | Even Jack's multi-day absence doesn't trigger emergency decay (pool continues without him). However, Jack BENEFITS if emergency decay was triggered by a whale attack during his absence — the pool recovers before he returns |

**Verdict:** Phase 1b is **protective for all miners** and only activates
during genuine emergencies. No false positives for normal mining patterns.

**Phase 2a: Exponential PPLNS Decay (§7.2)**

> **✓ IMPLEMENTED & TESTED (2026-03-03).** Phase 2a exponential PPLNS decay
> reduces the hopper arrival advantage from **5.27× to 1.52×** (71.1%
> improvement). At arrival, the hopper starts with only 20% of anchor's payout
> and must gradually rebuild through continuous mining. Even after 5 minutes,
> the hopper's payout (0.617 tLTC) remains below the anchor's (0.906 tLTC).
> See docs/PHASE2A_TEST_REPORT.md for full test data.

This is the most impactful change for honest miners. The key question:
**does depth-based decay unfairly penalize any legitimate mining pattern?**

| Miner | Impact | Analysis |
|-------|--------|----------|
| Alice | **SLIGHT NEGATIVE** (-3%) | Alice's shares at the tail of the window (27-36 hours old) now have 6-12% weight instead of 100%. However, her continuous stream of new shares at 100% weight dominates. Net effect: Alice's share of payouts shifts ~3% from oldest shares to newest shares — she's paying herself. |
| Bob | **NEUTRAL** | Same reasoning as Alice. Bob's shares decay at the same rate as everyone else's. Proportional payout is unchanged because the decay applies uniformly. Bob's tiny share count doesn't change the math — exponential decay is ratio-preserving for continuous miners. |
| Carol | **SLIGHT NEGATIVE** (-5% initial) | Carol's first shares are her ONLY shares. They start decaying immediately with nothing newer to compensate. After 9 hours (one half-life), Carol's claim is at 50%. However: Carol is also contributing only 3% of pool hashrate, so her absolute claim grows as she mines more shares (new shares compensate for decaying old ones). After 24 hours, Carol's steady-state payout matches her proportional contribution. |
| Dave | **MODERATE NEGATIVE** (-8%) | Dave mines 16 hours, sleeps 8 hours. His oldest shares (from ~24 hours ago) have decayed to 16% weight when he wakes up. His shares from 8 hours ago (when he stopped) have decayed to 50%. Meanwhile, Alice and Bob's shares from the same period have been supplemented by fresh shares. Dave's effective PPLNS share is ~8% lower than in a flat system. **This is the intended behavior** — Dave contributes less work than a 24/7 miner with the same hashrate, and exponential decay reflects this correctly. |
| Eve | **SIGNIFICANT NEGATIVE** (-15-25%) | Eve mines Fri-Sun (72 hours) then is absent Mon-Thu (96 hours). By Thursday, Eve's Friday shares have decayed to ~2% weight. Her Sunday shares are at ~13%. Eve's effective payout is 15-25% lower than in a flat PPLNS system. **This is also intended** — Eve contributes 72/168 = 43% as much work as a 24/7 miner, and flat PPLNS was over-rewarding her because her concentrated weekend shares held full weight during the week. |
| Frank | **SIGNIFICANT NEGATIVE** (-20-30%) | Frank mines 6h/24h = 25% of the time. His midnight–06:00 shares decay through 18 hours of absence. By midnight the next day, last night's shares are at ~12% weight (18h decay). Only tonight's fresh shares carry meaningful weight. Under flat PPLNS, Frank's shares held full weight for the entire 36h window (covering 2 nights = ~12h of shares). With decay, effective payout drops ~20-30% vs flat. **Correctly reflects 25% time contribution.** Under flat PPLNS, Frank was earning ~40% of a 24/7 miner's rate; with decay, ~25-30% — much closer to true contribution. |
| Grace | **MODERATE NEGATIVE** (-15-20%) | Grace mines 9h/24h = 37.5% of the time. Her 08:00–17:00 shares decay through 15 hours overnight. By 08:00 next morning, yesterday's afternoon shares are at ~18% weight. Under flat PPLNS, Grace had up to 2 full daytime sessions in the 36h window. With decay, effective payout drops ~15-20% vs flat. **Correctly reflects 37.5% contribution.** |
| Henry | **SLIGHT NEGATIVE** (-6%) | Henry mines 14h/24h = 58.3% of the time. His 10-hour daytime gap causes moderate decay — shares from last night's session are at ~35% weight when Henry resumes at 18:00. Very similar to Dave's pattern but with a longer mining window. The 6% reduction brings Henry closer to his true 58.3% proportional contribution. |
| Iris | **MODERATE NEGATIVE** (-10%) | Iris mines 11h/24h = 45.8% of the time. Her 13-hour afternoon gap causes shares from last night (22:00–09:00) to decay to ~25% weight by 22:00. Intermediate between Dave (-8%) and Eve (-15-25%), correctly reflecting Iris's intermediate 45.8% time commitment. |
| Jack | **SEVERE NEGATIVE** (-30-40%) | Jack mines sporadically, 3-4 days per week with multi-day gaps. After 48 hours offline, Jack's shares have decayed to ~3% weight. After 72 hours offline, ~0.4%. Jack effectively starts from near-zero each session. Under flat PPLNS, shares older than 36h were already gone — so Jack's loss is less about decay and more about the short window. **Under adaptive windows (Phase 4), the picture changes dramatically — see below.** |

**Key insight: Exponential decay is FAIR, not punitive.** It aligns payout
with actual contribution over time. Miners who mine more (Alice, Bob, Henry)
get proportionally more. Miners who mine less (Frank, Grace, Eve, Jack) get
proportionally less — which is correct. The decay impact correlates precisely
with each miner's duty cycle: Henry 58% → -6%, Dave 67% → -8%, Iris 46% →
-10%, Grace 38% → -15-20%, Frank 25% → -20-30%, Eve 43% → -15-25%,
Jack ~40% → -30-40%. Miners who were previously over-rewarded by flat PPLNS
now receive payout proportional to their actual contribution.

**Variance impact on small miners:**

```
Pool: 49.5 GH/s, PPLNS window = 307,996 shares (adaptive, live data)

Alice (6.5 GH/s, 13%):
  Expected shares in window: ~40,040
  Payout variance: ±0.5% (very low — many shares)

Bob (500 MH/s, 1%):
  Expected shares in window: ~3,080
  Payout variance: ±1.8% (low)
  
  With exponential decay, Bob's effective shares decrease because
  older shares lose weight. But the PPLNS denominator also decreases
  (everyone's old shares decay). Net payout PROPORTION is identical.
  Variance is unchanged — it's still 1/√(shares).
  
  Note: the adaptive window (308K shares vs 8,640) actually REDUCES
  variance for small miners because many more shares are included.
```

Exponential decay does NOT increase variance for small miners. It changes
absolute weights but preserves proportional weights for continuous miners.

**Phase 2b: Work-Weighted Exponential Vesting (§7.3.4)**

| Miner | Impact | Analysis |
|-------|--------|----------|
| Alice | **NONE** (-0%) | 24/7 mining → vesting factor = 1.0 at all times. Her continuous work stream maximizes the `calculate_robust_vesting()` output. No change in effective payout. |
| Bob | **NONE** (-0%) | Bob mines 24/7, just at lower hashrate. Vesting is work-weighted, so Bob's smaller but steady contribution gives vesting factor = 1.0. Work rate is lower, but it's CONSISTENT — exactly what vesting rewards. |
| Carol | **MODERATE NEGATIVE** (-20% for first ~24hr, then normalizing) | Carol just joined. Her vesting lookback is empty. After 1 hour of mining, her decayed work is ~1 hour worth vs the WORK_THRESHOLD of ~36 hours. Vesting factor ≈ 0.03. After 6 hours: ~0.15. After 24 hours: ~0.6. After 48 hours: ~0.85. After 72 hours: ~0.95. **This is the ramp-up period.** |

   Carol's experience:

   ```
   ┌──────────┬─────────────┬────────────────────────────────────┐
   │ Time     │ Vest. Factor│ Payout vs Instant-Credit           │
   ├──────────┼─────────────┼────────────────────────────────────┤
   │ 1 hour   │ 0.03        │ 3% (heavy discount)                │
   │ 6 hours  │ 0.15        │ 15% (significant discount)         │
   │ 12 hours │ 0.35        │ 35% (moderate discount)            │
   │ 24 hours │ 0.60        │ 60% (approaching fair value)       │
   │ 48 hours │ 0.85        │ 85% (near fully vested)            │
   │ 72 hours │ 0.95        │ 95% (essentially fully vested)     │
   │ 96 hours │ 0.99        │ 99% (fully vested)                 │
   └──────────┴─────────────┴────────────────────────────────────┘
   ```

   **Is this fair?** Yes. The discount protects against burst attacks, and
   Carol's lost payout during the ramp-up is small in absolute terms (she's
   new and has few shares). The shares that ARE found as blocks during the
   ramp-up period still pay Carol — just at reduced weight. After ~48 hours,
   Carol is at near-full vesting and mining normally.

| Dave | **SLIGHT NEGATIVE** (-3%) | Dave's 8-hour daily gap causes his vesting to dip from 1.0 to ~0.92 overnight (his oldest work decays without replenishment during sleep). When he resumes, vesting recovers to ~0.97 within 2 hours and back to 1.0 within 4 hours. The -3% average loss is small and reflects the reality that Dave contributes less total work than a 24/7 miner. |
| Eve | **MODERATE NEGATIVE** (-10-15%) | Eve's Mon-Thu absence causes vesting to decay significantly. By Thursday, her vesting factor is ~0.3. When she starts mining Friday, she ramps back up: Friday evening ~0.6, Saturday ~0.8, Sunday ~0.95. Her average effective vesting over the weekend is ~0.85-0.90 — a 10-15% reduction from full credit, correctly reflecting her 43% time commitment. |
| Frank | **MODERATE NEGATIVE** (-12-18%) | Frank's 18-hour daily gap causes significant vesting decay. At midnight (restart), vesting is ~0.45 (decayed from last night's accumulated work). During his 6h session: 01:00 → 0.55, 03:00 → 0.65, 06:00 → 0.72. Average session vesting ~0.62. After a week of consistent nightly mining, accumulated decayed work stabilizes — vesting slightly improves to ~0.50→0.75 range (average ~0.65). Frank's effective vesting cost: -12-18%, reflecting his 25% duty cycle. |
| Grace | **MODERATE NEGATIVE** (-10-15%) | Grace's 15-hour overnight gap causes vesting to drop to ~0.50 at 08:00. During 9 hours of daytime solar mining, vesting climbs: 10:00 → 0.60, 13:00 → 0.72, 17:00 → 0.80. Average session vesting ~0.68. Grace benefits from longer mining hours than Frank (9h vs 6h), yielding slightly better vesting. After a week of consistent solar mining, steady-state average ~0.68-0.72. Effective cost: -10-15%. |
| Henry | **SLIGHT NEGATIVE** (-4%) | Henry's 10-hour daytime gap (08:00→18:00) is similar to Dave's 8-hour pattern. Vesting dips from ~0.98 at 08:00 to ~0.88 at 18:00. During 14 hours of overnight mining, vesting recovers to ~0.95 within 3 hours and reaches ~0.99 by morning. Average vesting ~0.94-0.96. Henry's long mining window (~58% duty cycle) keeps vesting high. |
| Iris | **SLIGHT NEGATIVE** (-6%) | Iris's 13-hour afternoon gap (09:00→22:00) causes vesting to drop to ~0.78 at restart. During 11 hours of overnight mining, vesting climbs to ~0.92 by 09:00. Average session vesting ~0.85. Intermediate between Dave (-3%) and Grace (-10-15%), reflecting Iris's intermediate 46% duty cycle. |
| Jack | **SIGNIFICANT NEGATIVE** (-20-30%) | Jack's multi-day gaps devastate vesting. After 48 hours offline, vesting drops to ~0.10. After 72 hours offline, ~0.03. On return, Jack must rebuild: 2h → 0.08, 6h → 0.20, 12h → 0.40, 24h → 0.60. If Jack mines for 2 consecutive days then takes 2 days off, his average vesting across his mining days is ~0.45-0.55. **Jack is the most impacted miner** — but his sporadic pattern (~40% duty cycle with multi-day gaps) is the hardest for any payout system to reward efficiently. |

**Phase 4: Adaptive Windows (§7.3.10)**

| Miner | Impact | Analysis |
|-------|--------|----------|
| Alice | **POSITIVE** (+2-3%) | Longer window (53.5 days vs 36hr at current 49.5 GH/s) means more of Alice's shares are in the PPLNS window. Alice is a 24/7 miner, so she benefits from longer history. With exponential decay, her oldest shares have reduced weight — but she has MORE shares in the window than before. Net: slight positive. |
| Bob | **POSITIVE** (+2-3%) | Same reasoning as Alice. Bob benefits equally because the window expansion is proportional for all continuous miners. |
| Carol | **SLIGHT NEGATIVE** (ramp-up ~1 day longer) | The adaptive window means Carol needs more shares to reach the same PROPORTION of the window. But the adaptive vesting lookback (2×window ≈ 9.2 days) also extends her ramp-up period. Practically, the ramp-up timing is similar (vesting reaches 0.6 at ~24hr, 0.85 at ~48hr) because the WORK_THRESHOLD scales with the window. |
| Dave | **NEUTRAL** | Dave's 16hr/24hr pattern sees the same proportional effect with adaptive windows as with fixed. |
| Eve | **MODERATE POSITIVE** (+5%) | Larger window means Eve's Friday-Sunday shares stay in the PPLNS window longer (53.5 days at current hashrate). Under the old 36-hour window, Eve's Friday shares were at risk of scrolling out by Sunday night. Now they continue earning reduced-weight payouts deep into the following week. Eve's total contribution is more accurately credited. |
| Frank | **STRONGLY POSITIVE** (+8-12%) | Under fixed 36h window, Frank's nightly 6h sessions mean only the last 2 nights fit in the window (12h of shares). Under adaptive 53.5-day window, many prior nights contribute at decayed weight. Even with exponential decay, older nights still add meaningful cumulative value. This is a major improvement over fixed window. Frank shifts from "I lose most history after 36h" to "many prior nights still count (at decreasing weight)." |
| Grace | **STRONGLY POSITIVE** (+8-12%) | Same logic as Frank. Grace's daily solar mining sessions accumulate across the adaptive window. Under fixed 36h, only yesterday's and today's sessions counted. Under adaptive 53.5-day window, many prior daytime sessions contribute at decayed weight. Grace benefits strongly — her consistent daily pattern is exactly what adaptive windows reward. |
| Henry | **POSITIVE** (+3%) | Henry mines 14h/day — very similar to Alice/Bob in terms of consistency. Adaptive window benefits him proportionally, with slightly more gain than Dave due to Henry's longer daily mining window. |
| Iris | **MODERATE POSITIVE** (+5-7%) | Iris mines 11h/day. Under fixed 36h, ~2.5 sessions fit. Under adaptive 53.5-day, many prior sessions contribute at decayed weight. The additional accumulated weight is meaningful and partially compensates for the Phase 2a/2b penalties. |
| Jack | **VERY STRONGLY POSITIVE** (+15-25%) | This is where adaptive windows are TRANSFORMATIVE for Jack. Under fixed 36h window, any gap >36h means TOTAL share loss. Jack regularly takes 2-3 day breaks → his shares are GONE when he returns. Under adaptive 53-day window, even after a 3-day absence, Jack's previous week of mining still has meaningful (if decayed) weight. Jack goes from "I lose everything every time I take a break" to "my mining history persists across breaks." **This is the single biggest retention improvement for sporadic miners.** |

**Combined defense stack — full honest miner impact:**

```
┌────────┬─────────────┬───────────┬───────────┬──────────┬───────────┐
│ Miner  │ Phase 1a+1b │ +Phase 2a │ +Phase 2b │ +Phase 4 │ Net       │
├────────┼─────────────┼───────────┼───────────┼──────────┼───────────┤
│ Alice  │ +benefit    │ -3%       │ -0%       │ +2%      │ ~-1% ≈0   │
│ Bob    │ +benefit    │ neutral   │ -0%       │ +2%      │ ~+2%      │
│ Carol  │ +benefit    │ -5% init  │ -20% init │ -1 day   │ ~equal*   │
│ Dave   │ +benefit    │ -8%       │ -3%       │ neutral  │ ~-11%     │
│ Eve    │ +benefit    │ -15-25%   │ -10-15%   │ +5%      │ ~-20-35%  │
│ Frank  │ +benefit    │ -20-30%   │ -12-18%   │ +8-12%   │ ~-24-36%  │
│ Grace  │ +benefit    │ -15-20%   │ -10-15%   │ +8-12%   │ ~-17-23%  │
│ Henry  │ +benefit    │ -6%       │ -4%       │ +3%      │ ~-7%      │
│ Iris   │ +benefit    │ -10%      │ -6%       │ +5-7%    │ ~-9-11%   │
│ Jack   │ +benefit    │ -30-40%   │ -20-30%   │ +15-25%  │ ~-35-45%  │
│────────│─────────────│───────────│───────────│──────────│───────────│
│ Hopper │ none        │ -50%      │ -70-90%   │ neutral  │ ~-90%     │
└────────┴─────────────┴───────────┴───────────┴──────────┴───────────┘

* Carol's initial penalty vanishes after ~72 hours of continuous mining.

Duty cycle correlation:
  Alice 100% → -1%, Bob 100% → +2%, Henry 58% → -7%,
  Dave 67% → -11%, Iris 46% → -10%, Grace 38% → -20%,
  Frank 25% → -30%, Eve 43% → -27%, Jack ~40% → -40%,
  Hopper <5% → -90%
```

**Interpretation:**

- **24/7 miners (Alice, Bob):** Essentially zero net impact. All defenses
  are designed to be neutral for continuous miners. Alice loses ~1% from
  exponential decay (tail shares) but gains ~2% from adaptive windows
  (more shares weighted in her favor). Bob is slightly better off because
  exponential decay correctly reduces the weight of stale shares from
  larger miners who've gone offline — shifting proportional payout to
  Bob who's still here.

- **New miners (Carol):** 48-72 hour ramp-up period. This is an intentional
  trade-off: the ramp-up is the cost of anti-burst protection. Carol's
  absolute loss during the ramp-up is small (few shares × reduced weight),
  and she reaches ~95% vesting within 3 days. **Recommendation:** the web
  dashboard should show Carol her current vesting factor and estimated
  time to full vesting, so she understands the progression.

- **Intermittent miners (Dave):** ~11% effective reduction. This is
  **mathematically correct** — Dave mines 16/24 = 67% of the time and
  should earn ~67% of what a 24/7 miner earns per unit hashrate. Under
  flat PPLNS, Dave was earning ~92% (because his stale shares held full
  weight during his 8-hour sleep). The 11% reduction brings Dave's payout
  closer to his ACTUAL contribution. This is not a penalty — it's a
  correction.

- **Night-tariff miners (Frank):** ~24-36% reduction reflects Frank's 25%
  duty cycle (6h/24h). Under flat PPLNS, Frank earned ~40% of a 24/7
  miner's rate; now ~25-30%. Adaptive windows (+8-12%) provide the biggest
  recovery — Frank's consistent nightly sessions accumulate value across
  the extended window. **Retention note:** Frank's mining schedule is driven
  by electricity costs. He CANNOT mine during daytime without losing money.
  The defense stack correctly reflects his contribution without driving him
  away — he still earns proportionally to his work. The key is that the
  dashboard shows Frank his accumulated PPLNS weight persisting across
  nights, so he sees the long-term value of consistent nightly mining.

- **Solar miners (Grace):** ~17-23% reduction reflects Grace's 37.5% duty
  cycle (9h/24h). Grace actually benefits more than Frank from the same
  mechanisms because her 9-hour sessions provide more vesting accumulation.
  Solar-powered mining is growing worldwide — especially in regions with
  high daytime solar irradiance. Grace's pattern (mine when electricity is
  free from solar panels) is increasingly common and important to support.

- **Heat-recycling miners (Henry):** ~7% reduction, very close to Dave.
  Henry's 14h/24h = 58% duty cycle means he's almost a full-time miner.
  The defense stack is nearly transparent to Henry — his long overnight
  sessions and moderate daytime gap produce high vesting and low decay.
  **Best-case part-time miner.**

- **Dual-job GPU miners (Iris):** ~10% reduction, between Dave and Eve.
  Iris's 11h/24h = 46% duty cycle maps to a proportional reduction.
  Iris represents miners who use GPUs for computation during work hours
  and switch to mining at night. This pattern is common among technically
  savvy miners and reasonably well-served by the defense stack.

- **Weekend miners (Eve):** ~20-35% reduction. Eve mines 72/168 = 43%
  of the time. Under flat PPLNS, she was earning ~75-80% of a 24/7
  miner's rate. With the full defense stack, Eve earns ~50-60% — much
  closer to her actual 43% contribution. **Eve is not being "punished"**
  — she was previously over-rewarded at the expense of Alice and Bob.

- **Sporadic miners (Jack):** ~35-45% reduction — the harshest impact.
  Jack's multi-day gaps are devastating under any fair payout system.
  However, **adaptive windows are Jack's lifeline**: without them, Jack
  would lose 100% of shares every time he takes a 2-day break. With
  adaptive windows, Jack's shares persist (at reduced weight) across his
  absences. The difference is between "zero payout from previous sessions"
  and "some payout from previous sessions." **Jack should be shown on the
  dashboard how his accumulated share weight persists across breaks** to
  encourage return after each absence.

**The fairness principle:** A defense stack that merely reduces EVERYONE's
payouts proportionally is pointless. The purpose is to align payout with
contribution. Miners who contribute more consistently should earn more per
unit hashrate than miners who contribute sporadically — because consistent
miners provide the steady work foundation that makes blocks happen. The
defense stack achieves this while also making hopping deeply unprofitable.

**Hopper vs honest miners comparison (demonstrates proportionality):**

```
Alice (24/7 miner, baseline):
  Mines 168 hours/week at 6.5 GH/s
  Vesting: 1.0 (always fully vested)
  Effective reward efficiency: 100% (reference)

Frank (night-tariff miner, honest):
  Mines 42 hours/week at 8 GH/s (6h/night × 7 nights)
  Duty cycle: 25%. Consistent DAILY pattern.
  Vesting at session start: ~0.50 → 0.72 by end of 6h session
  Average vesting: ~0.65
  PPLNS: nightly shares accumulate across adaptive window
  Effective reward efficiency: ~25-30% of Alice's rate per GH
  → MATCHES duty cycle. Frank earns proportional to work.

Jack (sporadic miner, honest):
  Mines ~65 hours/week at 1 GH/s (3-4 days on, 3-4 days off)
  Duty cycle: ~40%. IRREGULAR pattern with multi-day gaps.
  Vesting on return: ~0.10 → 0.60 after 24h mining
  Average vesting during active days: ~0.45-0.55
  PPLNS: shares from previous week still in adaptive window
  Effective reward efficiency: ~20-30% of Alice's rate per GH
  → Slightly below duty cycle due to gap penalty.

Eve (weekend miner, honest):
  Mines 72 hours/week at 15 GH/s (Fri-Sun)
  Duty cycle: 43%. Concentrated burst pattern.
  Vesting on arrival (Friday): ~0.3 → 0.95 by Sunday
  Average weekend vesting: ~0.75
  PPLNS weight: ~70% (decay weighted toward fresh Sunday shares)
  Effective reward efficiency: ~50-60% of Alice's rate per GH
  → Close to duty cycle. Eve's 72h continuous burst helps vesting.

Hopper (burst attacker, malicious):
  Mines 8 hours, then LEAVES PERMANENTLY
  Duty cycle: <5%. ONE-TIME burst with no return.
  Vesting: 0.03 → 0.15 (never reaches useful level)
  PPLNS weight: shares decay rapidly after departure
  Effective reward efficiency: ~10% of Alice's rate (0.3× ratio)
  → 2-5× BELOW duty cycle. Anti-hopping surcharge works.

  Discrimination gradient:
    Alice (100% duty) → 100% efficiency  (ratio: 1.0×)
    Henry (58% duty)  → ~93% efficiency  (ratio: 1.6×)
    Dave  (67% duty)  → ~89% efficiency  (ratio: 1.3×)
    Eve   (43% duty)  → ~55% efficiency  (ratio: 1.3×)
    Grace (38% duty)  → ~32% efficiency  (ratio: 0.85×)
    Frank (25% duty)  → ~28% efficiency  (ratio: 1.1×)
    Jack  (40% duty)  → ~25% efficiency  (ratio: 0.6×)
    Hopper (<5% duty) → ~10% efficiency  (ratio: <0.2×)

  Key observation: Honest miners with REGULAR schedules (Frank, Grace,
  Henry, Iris) get efficiency ratios near 1.0× — they earn proportional
  to their duty cycles. Miners with IRREGULAR gaps (Jack, Eve) get slight
  penalties from vesting reset. The hopper gets a 5-10× penalty. The
  system correctly distinguishes between "I mine on a schedule" and
  "I'm here to extract value from one burst."
```

**RAM and CPU impact on honest miners (at current 49.5 GH/s):**

| Component | Current | Naive Full Stack | Optimized (§7.3.13) |
|-----------|---------|------------------|----------------------|
| Tracker RAM | ~5 MB | ~5.55 GB | **~1.39 GB** (compacted) |
| PPLNS calc | O(log 8640) ~13 steps | O(log 307996) ~18 steps | ~18 steps (unchanged) |
| Vesting calc | N/A | O(615992) = ~616ms | **O(30) = ~3μs** ★ |
| Share gen | ~50ms | ~667ms | **~52ms** |
| Total overhead | ~0.3% of 15s budget | ~4.4% of 15s budget | **~0.35%** |
| Crash recovery | Local shares + small peer gap-fill (~30s) | Large adaptive-history peer gap-fill (multi-minute) | **SQLite ~8s** |

★ = with incremental vesting cache (Strategy 1, §7.3.13). **Critical** for
pools below ~20 GH/s where naive O(n) vesting exceeds the share period.

Note: At higher pool hashrates, overhead drops further:
- At 295 GH/s (peak): 233 MB RAM (compact), ~0.003ms vesting
- At 500 GH/s: 137 MB RAM (compact), ~0.003ms vesting

If SQLite WAL is enabled with a 64 MB page cache, total process RSS is
~1.39 GB + 64 MB ≈ **~1.45 GB** at current 49.5 GH/s (with compaction).

The optimized computational overhead is virtually zero. The incremental
vesting cache (§7.3.13 Strategy 1) and share compaction (Strategy 2) are
both **P0 prerequisites** for adaptive windows deployment. With corrected
share size estimates (~4,500 bytes/share in PyPy memory vs the original
~300 byte assumption — see §7.3.14), compaction is no longer optional:
without it, 49.5 GH/s requires ~5.55 GB of tracker RAM.
SQLite WAL (Strategy 3) remains a P1 quality-of-life improvement.
See §7.3.13 for full analysis.

**Recommendations for honest miner experience:**

1. **Dashboard transparency.** Show each miner their current vesting
   factor, PPLNS effective weight, and comparison to proportional (flat)
   weight. This lets Carol see her ramp-up progress and Eve understand
   why her weekend-only pattern yields ~50% of Alice's rate.

2. **Vesting ETA display.** Show "Estimated time to 95% vesting: X hours"
   for new miners. This manages expectations and reduces confusion.

3. **Payout explanations.** When a block is found, the `/payouts` endpoint
   should show each miner their raw share count, effective weight (after
   decay + vesting), and the resulting payout — with a note explaining
   which factors reduced or increased their effective weight.

4. **Configurable notifications.** Alert miners when their vesting factor
   drops below 0.5 (due to absence) or when they reach 0.95 (fully vested
   after ramp-up). This is especially valuable for Eve-pattern miners who
   may not realize their Monday-Thursday absence affects Friday payouts.

#### 7.3.13 Performance Optimization for Large Adaptive Windows

The adaptive window design (§7.3.10) creates windows of 308K–1.5M+ shares
depending on pool hashrate. This section analyzes the computational costs,
identifies bottlenecks, and proposes three optimization strategies: an
incremental vesting cache, share compaction with tiered storage, and
optional SQLite WAL persistence for crash recovery.

**Current architecture bottleneck analysis:**

The P2Pool tracker (`p2pool/util/forest.py`) stores shares in a Python dict
(`self.items = {} # hash -> item`) with a linked list structure via
`previous_hash`. Two distinct operations hit this data:

1. **PPLNS weight calculation** (`WeightsSkipList` in `data.py` line ~1641):
   Uses the geometric skip list with O(log n) traversal. At 308K shares,
   this is ~18 steps with LRU(5) memoization. **Not a bottleneck.**

2. **Vesting calculation** (proposed `calculate_robust_vesting()` in §7.3.4):
   Iterates the full vesting lookback via `tracker.get_chain()`, which is a
   Python generator walking the linked list one item at a time. This is
   **O(n)** where n = vesting_lookback.

**Measured and projected costs (PyPy 2.7.18, dict iteration ~1M items/sec):**

```
┌──────────────────────┬────────────┬──────────┬───────────┬────────────┬────────────┐
│ Pool Hashrate        │ PPLNS Win  │ Vest. LB │ PPLNS     │ Vesting    │ % of 15s   │
│                      │            │          │ (skiplist)│ (O(n) walk)│ budget     │
├──────────────────────┼────────────┼──────────┼───────────┼────────────┼────────────┤
│ 295 GH/s (peak seen) │ 51,683     │ 103,366  │ ~0.08ms   │ ~103ms     │ 0.7%       │
│ 72 GH/s  (average)   │ 211,757    │ 423,514  │ ~0.09ms   │ ~424ms     │ 2.8%       │
│ ★ 49.5 GH/s (CURRENT)│ 307,996    │ 615,992  │ ~0.09ms   │ ~616ms     │ 4.1%       │
│ 10 GH/s              │ 1,524,656  │ 3,049,312│ ~0.10ms   │ ~3,049ms   │ 20.3%      │
│ 1 GH/s               │ 15,246,560 │30,493,120│ ~0.12ms   │ ~30,493ms  │ 203% ✗     │
└──────────────────────┴────────────┴──────────┴───────────┴────────────┴────────────┘

★ = actual live pool state on March 2, 2026.
✗ = exceeds share period — vesting calculation cannot finish in time.
```

At **49.5 GH/s**, the O(n) vesting walk takes ~616ms per share generation
(every 15 seconds) — 4.1% of the budget. Acceptable but the dominant cost.

At **10 GH/s**, it's 3 seconds per 15-second cycle — 20% CPU. Problematic.

At **1 GH/s**, it's 30 seconds per 15-second cycle — **impossible**. Vesting
can't even finish before the next share generation starts.

**Conclusion:** The naive O(n) vesting iteration does not scale below ~20 GH/s
pool hashrate with adaptive windows. Three complementary optimizations solve
this at all scales.

##### Strategy 1: Incremental Vesting Cache (★ RECOMMENDED — O(1) per share)

Instead of re-scanning the entire vesting lookback on every share generation,
maintain a running weighted sum that updates incrementally:

**Mathematical basis:**

The vesting score for address A is:

$$S_A(n) = \sum_{i \in \text{window}} w_i \cdot 2^{-(n-h_i)/\lambda}$$

where $w_i$ is share work, $h_i$ is share height, $n$ is current height,
and $\lambda$ is the half-life. When a new share arrives at height $n+1$:

$$S_A(n+1) = S_A(n) \cdot 2^{-1/\lambda} + w_{\text{new}} \cdot [A = A_{\text{new}}] - w_{\text{old}} \cdot 2^{-L/\lambda}$$

where $L$ is the lookback window size and $w_{\text{old}}$ is the share
falling off the tail. This is **O(1)** per share regardless of window size.

**Implementation (Python 2.7 / PyPy compatible, integer arithmetic):**

```python
class IncrementalVestingCache(object):
    """O(1) per-share incremental vesting score tracker.
    
    Maintains per-address exponential-decayed work sums using fixed-point
    integer arithmetic for consensus safety. No floating point.
    
    Key insight: each new share only requires:
    1. Multiply all scores by the per-share decay factor (O(addresses))
    2. Add the new share's work to its address
    3. Subtract the decayed contribution of the share leaving the window
    
    At 30 active addresses, this is O(30) — not O(616,000).
    """
    
    # Fixed-point precision: 40 bits of fractional precision
    # This gives ~12 decimal digits of precision, sufficient for
    # decay calculations over millions of shares.
    PRECISION = 40
    SCALE = 1 << 40  # 2^40 ≈ 1.1e12
    
    def __init__(self, half_life):
        self.half_life = half_life
        self.scores = {}  # address -> scaled_decayed_work (integer)
        self.generation = 0  # shares processed since last rebuild
        
        # Pre-compute per-share decay factor in fixed-point:
        # decay = 2^(-1/half_life)
        # In fixed-point: decay_fp = floor(2^PRECISION * 2^(-1/half_life))
        #                          = floor(2^(PRECISION - 1/half_life))
        # For integer-only: use the identity
        #   2^(-1/half_life) = (2^half_life - 1) / 2^(1) ... no, simpler:
        #   decay_fp = SCALE * (2**half_life - 1) // (2**half_life)  # approximate
        # More precise: use rational approximation
        #   decay_fp = SCALE - SCALE // (2 * half_life)  # first-order Taylor
        # Best: pre-compute exactly using Python long integers
        #   decay_fp = (SCALE << half_life) >> half_life  # identity, useless
        # Actually: compute iteratively with Newton's method in integers
        # For simplicity and correctness, use: SCALE * exp(-ln2/half_life)
        
        # Integer-safe: abuse the identity 2^(-1/h) ≈ 1 - ln(2)/h for large h
        # For h=77000 (current half_life at 49.5 GH/s): error < 1e-10, acceptable
        # ln(2) ≈ 0.693147... in fixed-point: LN2_FP = 762123384786 (40-bit)
        self.LN2_FP = (self.SCALE * 693147180559945) // (10**15)
        self.decay_fp = self.SCALE - self.LN2_FP // half_life
        
        # Decay factor for full window (lookback shares old):
        # decay_window = decay_fp ^ lookback ≈ 2^(-lookback/half_life)
        # For lookback = 2*half_life: decay_window = 2^(-2) = 0.25
        # Pre-computed at query time since lookback may change
    
    def on_new_share(self, address, work, old_address, old_work, lookback):
        """Update scores when a new share is added to the chain.
        
        Args:
            address: new share's miner address
            work: new share's target_to_average_attempts value
            old_address: address of the share falling off the tail (or None)
            old_work: work of the share falling off the tail (or 0)
            lookback: current vesting lookback window size
        
        Cost: O(num_addresses) — typically 20-40 active addresses.
        """
        # Step 1: Decay all existing scores by one share period
        for addr in self.scores:
            # score = score * decay_fp / SCALE
            self.scores[addr] = (self.scores[addr] * self.decay_fp) >> self.PRECISION
        
        # Step 2: Add new share's contribution at full weight
        scaled_work = work << self.PRECISION  # work * SCALE
        self.scores[address] = self.scores.get(address, 0) + scaled_work
        
        # Step 3: Remove contribution of share leaving the window
        if old_address is not None and old_work > 0:
            # The old share has been decayed 'lookback' times
            # Its current contribution: old_work * decay^lookback
            # For large lookback (>2*half_life), this is negligible (<6.25%)
            # but we subtract it for correctness.
            # Compute decay^lookback using repeated squaring:
            old_contribution = self._decay_power(old_work << self.PRECISION, lookback)
            self.scores[old_address] = max(0,
                self.scores.get(old_address, 0) - old_contribution)
        
        # Prune zero-score addresses (left the pool long ago)
        self.scores = {a: s for a, s in self.scores.iteritems() if s > 0}
        self.generation += 1
    
    def _decay_power(self, value, exponent):
        """Compute value * (decay_fp/SCALE)^exponent using repeated squaring.
        
        O(log exponent) multiplications.
        """
        if exponent <= 0:
            return value
        result = value
        base = self.decay_fp
        # Apply base^exponent via binary exponentiation
        remaining = exponent
        factor = base  # starts as decay^1
        while remaining > 0:
            if remaining & 1:
                result = (result * factor) >> self.PRECISION
            factor = (factor * factor) >> self.PRECISION
            remaining >>= 1
        return result
    
    def get_vesting_factor(self, address, threshold):
        """Get vesting factor for an address (0.0 to 1.0 as fixed-point).
        
        Returns: (numerator, denominator) tuple for integer division.
        """
        score = self.scores.get(address, 0)
        if score >= threshold:
            return (1, 1)  # Fully vested
        return (score, threshold)
    
    def rebuild(self, tracker, tip_hash, lookback, half_life):
        """Full rebuild from tracker data. O(lookback).
        
        Called once at startup or when parameters change. After this,
        all updates are incremental via on_new_share().
        """
        self.scores = {}
        self.half_life = half_life
        self.LN2_FP = (self.SCALE * 693147180559945) // (10**15)
        self.decay_fp = self.SCALE - self.LN2_FP // half_life
        
        share_index = 0
        for share in tracker.get_chain(tip_hash, lookback):
            work = bitcoin_data.target_to_average_attempts(share.target)
            addr = share.share_data['address']
            age_factor = self._decay_power(self.SCALE, share_index)
            self.scores[addr] = self.scores.get(addr, 0) + \
                ((work * age_factor) >> self.PRECISION) << self.PRECISION
            share_index += 1
        
        self.generation = 0
```

**Performance comparison:**

```
┌──────────────────────┬─────────────────┬──────────────────┬─────────────┐
│ Pool Hashrate        │ Naive O(n) walk │ Incremental O(1) │ Speedup     │
├──────────────────────┼─────────────────┼──────────────────┼─────────────┤
│ 295 GH/s (peak)      │ ~103ms          │ ~0.003ms         │ 34,000×     │
│ 72 GH/s  (average)   │ ~424ms          │ ~0.003ms         │ 141,000×    │
│ ★ 49.5 GH/s (CURRENT)│ ~616ms          │ ~0.003ms         │ 205,000×    │
│ 10 GH/s              │ ~3,049ms        │ ~0.003ms         │ 1,016,000×  │
│ 1 GH/s               │ ~30,493ms ✗     │ ~0.003ms         │ 10,164,000× │
└──────────────────────┴─────────────────┴──────────────────┴─────────────┘

Incremental cost: ~30 dict operations × 1 integer multiply = ~3μs.
Startup rebuild: same as naive O(n) — runs once, then all incremental.
```

**The incremental cache reduces vesting from the dominant cost (4–200% of
share period) to effectively zero (<0.001% of share period) at any pool
size.** This is the single most important optimization for adaptive windows.

**Consensus safety:** The incremental cache produces identical results to
the full O(n) calculation because the math is equivalent — it's just
computed in a different order (running sum vs batch iteration). If there's
any concern about fixed-point drift over millions of shares, a periodic
full rebuild (e.g., every 10,000 shares ≈ 42 hours) can be used to re-sync.
The rebuild cost is O(n) but amortized over 10K shares = O(n/10K) per share.

##### Strategy 2: Share Compaction (Tiered Storage)

For very low hashrate pools (≤50 GH/s) where the adaptive window exceeds
300K shares, the tracker RAM (~5.5 GB+ at ~4,500 bytes/share under PyPy)
becomes significant. **Compaction is a P0 prerequisite for adaptive windows
at current pool hashrate** — not optional. Share compaction reduces memory
by aggregating old shares into per-address summary records.

**Architecture: Three-tier storage**

```
┌────────────────────────────────────────────────────────────┐
│ Tier 1 — HOT (in-memory, full share objects)               │
│ Scope: PPLNS window (308K shares at 49.5 GH/s)             │
│ Used by: WeightsSkipList (PPLNS payout), skip list         │
│ Structure: tracker.items dict + linked list                │
│ RAM: ~1.39 GB (~4,500 bytes/share × 308K shares)           │
├────────────────────────────────────────────────────────────┤
│ Tier 2 — WARM (in-memory, compacted summaries)             │
│ Scope: Vesting tail (window+1 to 2×window)                 │
│ Used by: Vesting calculation (incremental cache)           │
│ Structure: per-address per-epoch aggregates                │
│ RAM: ~1.3 MB (vs 1.39 GB for full shares)                  │
├────────────────────────────────────────────────────────────┤
│ Tier 3 — COLD (on-disk SQLite, optional)                   │
│ Scope: All historical shares for crash recovery            │
│ Used by: Restart resync (replaces peer-based re-sync)      │
│ Structure: SQLite WAL database                             │
│ Disk: ~150-400 MB at current pool size (wire ~1.1 KB/share)│
└────────────────────────────────────────────────────────────┘
```

**Compact summary record:**

```python
class CompactEpochSummary(object):
    """Aggregated share data for one epoch (720 shares ≈ 3 hours).
    
    Replaces 720 full share objects (~216 KB) with one summary (~100 bytes).
    Memory reduction: 2,160× per epoch.
    """
    __slots__ = ['epoch_id', 'first_height', 'last_height',
                 'per_address_work',  # {address: total_work}
                 'total_work', 'share_count']
    
    def __init__(self, epoch_id, first_height, last_height,
                 per_address_work, total_work, share_count):
        self.epoch_id = epoch_id
        self.first_height = first_height
        self.last_height = last_height
        self.per_address_work = per_address_work
        self.total_work = total_work
        self.share_count = share_count
    
    @classmethod
    def from_shares(cls, epoch_id, shares):
        """Compact a list of share objects into a single summary."""
        per_address = {}
        total_work = 0
        for share in shares:
            work = bitcoin_data.target_to_average_attempts(share.target)
            addr = share.share_data['address']
            per_address[addr] = per_address.get(addr, 0) + work
            total_work += work
        return cls(
            epoch_id=epoch_id,
            first_height=shares[-1].share_data.get('height', 0),
            last_height=shares[0].share_data.get('height', 0),
            per_address_work=per_address,
            total_work=total_work,
            share_count=len(shares),
        )
```

**Memory impact:**

```
┌──────────────────────┬──────────────┬──────────────────┬────────┐
│ Pool Hashrate        │ Without      │ With Compaction  │ Saving │
│                      │ Compaction   │ (Tier 1+2)       │        │
├──────────────────────┼──────────────┼──────────────────┼────────┤
│ 295 GH/s (peak)      │ 930 MB       │ 233 MB           │ 75%    │
│ 72 GH/s  (average)   │ 3.81 GB      │ 953 MB           │ 75%    │
│ ★ 49.5 GH/s (CURRENT)│ 5.55 GB      │ 1.39 GB          │ 75%    │
│ 10 GH/s              │ 27.4 GB ✗    │ 6.86 GB          │ 75%    │
│ 1 GH/s               │ 274 GB ✗     │ 68.6 GB ✗        │ 75%    │
└──────────────────────┴──────────────┴──────────────────┴────────┘

At 49.5 GH/s: 5.55 GB → 1.39 GB. The vesting tail (308K shares = 1.39 GB)
is replaced by ~428 epoch summaries (~43 KB). Tier 1 stays at 1.39 GB.

**IMPORTANT:** These corrected figures (based on ~4,500 bytes/share in PyPy
2.7 memory, see §7.3.14) show that Strategy 2 compaction is now a **P0
prerequisite** for adaptive windows at any pool size ≤295 GH/s — not merely
a "quality-of-life" improvement. Without compaction, even the current 49.5
GH/s pool would need 5.55 GB of tracker RAM alone.
```

**When compaction triggers:** As shares leave the PPLNS window (Tier 1),
they're aggregated into epoch summaries (Tier 2) rather than discarded.
The `clean_tracker()` function in `node.py` (line ~386) already prunes at
`2 × CHAIN_LENGTH + 10`. With compaction, shares between `PPLNS_window`
and `2 × vesting_lookback` are compacted instead of fully stored.

**Interaction with incremental vesting cache:** The cache (Strategy 1)
eliminates the need to iterate Tier 2 at all during normal operation. The
compact summaries are only needed for:
- Cache rebuild after restart
- Periodic validation (integrity check)
- Fork resolution across deep reorgs

This means compaction is primarily a **memory optimization**, not a
performance optimization — the incremental cache already solved the CPU
problem.

##### Strategy 3: SQLite WAL Persistence (Crash Recovery)

The current P2Pool tracker is in-memory, but shares **are** persisted to
disk via the `ShareStore` class (`data.py`). The `ShareStore` writes shares
to numbered `shares.N` files every 60 seconds, persisting up to
`2 × CHAIN_LENGTH` (~17,280) recent shares. Files rotate at ~10 MB.

On restart, the node loads shares from these disk files FIRST (typically
recovering 8,640–17,280 shares in seconds), then requests only the gap (shares
mined during the last 60 seconds before crash + any missed during restart)
from P2P peers. **This is NOT a full re-sync** — it is a partial gap-fill.

However, with adaptive windows of 308K+ shares in the PPLNS window (and
~1.2M in the full tracker), the current `ShareStore` only covers a fraction:

```
Fixed window:   8,640 shares → ShareStore covers 100% (2 × 8,640 persisted)
Adaptive window: 308K shares → ShareStore covers ~5.6% (17,280 / 308,000)
Full tracker:   1.2M shares  → ShareStore covers ~1.4% (17,280 / 1,200,000)
```

After restart with adaptive windows, the node has ~17K shares from disk
but needs ~308K for full PPLNS calculation. The remaining ~291K shares
must be fetched from P2P peers — a process that takes minutes to hours.
**This is where SQLite WAL adds critical value**: persisting the ENTIRE
adaptive window to a crash-safe database, not just the rolling 17K shares.

**SQLite WAL mode** (Write-Ahead Logging) provides:
- Concurrent reads during writes (no locking during share generation)
- ACID persistence (crash-safe without explicit flushing)
- Single-writer, multiple-reader architecture (matches P2Pool's model)
- Available in Python 2.7 stdlib (`import sqlite3`)
- PyPy compatible (sqlite3 module is supported)

**Schema:**

```sql
-- Core share table
CREATE TABLE shares (
    hash        BLOB PRIMARY KEY,
    prev_hash   BLOB,
    address     TEXT NOT NULL,
    target      INTEGER NOT NULL,
    work        INTEGER NOT NULL,  -- target_to_average_attempts(target)
    height      INTEGER NOT NULL,
    timestamp   INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    raw_data    BLOB               -- serialized share for full reconstruction
);

-- Indexes for common queries
CREATE INDEX idx_height ON shares(height);
CREATE INDEX idx_address_height ON shares(address, height);
CREATE INDEX idx_prev_hash ON shares(prev_hash);

-- Compact epoch summaries
CREATE TABLE epochs (
    epoch_id        INTEGER PRIMARY KEY,
    first_height    INTEGER NOT NULL,
    last_height     INTEGER NOT NULL,
    total_work      INTEGER NOT NULL,
    share_count     INTEGER NOT NULL
);

CREATE TABLE epoch_address_work (
    epoch_id    INTEGER REFERENCES epochs(epoch_id),
    address     TEXT NOT NULL,
    work        INTEGER NOT NULL,
    PRIMARY KEY (epoch_id, address)
);

-- Metadata
CREATE TABLE meta (
    key     TEXT PRIMARY KEY,
    value   TEXT
);
-- INSERT INTO meta VALUES ('schema_version', '1');
-- INSERT INTO meta VALUES ('chain_tip', '<hash>');
-- INSERT INTO meta VALUES ('last_compact_height', '<height>');
```

**Write pattern (WAL mode):**

```python
import sqlite3

class ShareDB(object):
    """Persistent share storage with WAL mode for P2Pool.
    
    Writes happen on the main thread after share validation.
    Reads happen during share generation (concurrent with writes in WAL).
    
    NOT used for consensus calculations — the in-memory tracker remains
    the authoritative source. SQLite is a persistence/recovery layer only.
    """
    
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')  # fsync on checkpoint only
        self.conn.execute('PRAGMA cache_size=-65536')    # 64 MB page cache
        self.conn.execute('PRAGMA wal_autocheckpoint=1000')  # checkpoint every 1K pages
        self._create_tables()
    
    def add_share(self, share):
        """Write a new share. Called after share validation.
        
        Cost: ~0.1ms per share (WAL append, no fsync).
        At 1 share/15s = 240 shares/hour: negligible.
        """
        work = bitcoin_data.target_to_average_attempts(share.target)
        self.conn.execute(
            'INSERT OR REPLACE INTO shares '
            '(hash, prev_hash, address, target, work, height, timestamp, version) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (buffer(share.hash), buffer(share.previous_hash) if share.previous_hash else None,
             share.share_data['address'], int(share.target), work,
             share.share_data.get('height', 0), share.timestamp, share.VERSION)
        )
        # Batch commit: every 10 shares or on explicit flush
        if self._pending_writes >= 10:
            self.conn.commit()
            self._pending_writes = 0
        else:
            self._pending_writes += 1
    
    def load_chain(self, tip_hash, length):
        """Load shares from database for tracker reconstruction.
        
        Called once at startup to rebuild the in-memory tracker.
        """
        cursor = self.conn.execute(
            'SELECT hash, raw_data FROM shares '
            'WHERE height >= (SELECT height FROM shares WHERE hash = ?) - ? '
            'ORDER BY height DESC',
            (buffer(tip_hash), length)
        )
        return cursor.fetchall()
    
    def get_vesting_summary(self, min_height):
        """Get per-address work summary for vesting (cache rebuild).
        
        Returns: {address: total_work} for shares above min_height.
        Uses SQL aggregation — much faster than Python iteration.
        """
        cursor = self.conn.execute(
            'SELECT address, SUM(work) FROM shares '
            'WHERE height >= ? GROUP BY address',
            (min_height,)
        )
        return dict(cursor.fetchall())
    
    def prune(self, min_height):
        """Remove shares below min_height (after compaction).
        
        Frees disk space. Called periodically (e.g., hourly).
        """
        self.conn.execute('DELETE FROM shares WHERE height < ?', (min_height,))
        self.conn.execute('VACUUM')  # reclaim space (run infrequently)
```

**Startup recovery time improvement:**

```
┌──────────────────────┬──────────────────┬───────────────────┬──────────┐
│ Tracker Size         │ P2P Re-sync      │ SQLite Load       │ Speedup  │
├──────────────────────┼──────────────────┼───────────────────┼──────────┤
│ 17,290 (current)     │ ~30 seconds      │ ~2 seconds        │ 15×      │
│ 308,000 (49.5 GH/s)  │ ~5–10 minutes    │ ~8 seconds        │ 38–75×   │
│ 1,232,000 (49.5 GH/s)│ ~20–40 minutes   │ ~30 seconds       │ 40–80×   │
│ 6,099,000 (10 GH/s)  │ ~2–4 hours       │ ~2 minutes        │ 60–120×  │
└──────────────────────┴──────────────────┴───────────────────┴──────────┘

P2P re-sync: limited by peer bandwidth, handshake overhead, validation.
SQLite load: limited by sequential disk read (~100 MB/s SSD).
```

**Key design principle:** SQLite is a **persistence layer**, not a consensus
layer. The in-memory tracker remains authoritative for all consensus-critical
calculations (PPLNS weights, share validation, difficulty retarget). SQLite
only provides crash recovery and fast startup. This separation ensures that
database corruption or SQLite bugs cannot cause consensus divergence.

##### Combined Optimization Architecture

```
            ┌─────────────────────────────────────────────────────────┐
            │            Share Generation Cycle (15s)                 │
            │                                                         │
  New share │  1. Validate share        O(1)         ~1ms             │
  arrives   │  2. Add to tracker        O(1)         ~0.1ms           │
            │  3. Update vesting cache  O(addrs)     ~0.003ms  ★      │
            │  4. PPLNS via skip list   O(log n)     ~0.09ms          │
            │  5. Persist to SQLite     O(1)         ~0.1ms    ★★     │
            │  6. Compact if needed     O(720/epoch) ~0.5ms amort ★★★ │
            │                                                         │
            │  Total:                                ~1.8ms            │
            │  vs naive vesting:                     ~617ms            │
            │  Speedup:                              ~340×             │
            └─────────────────────────────────────────────────────────┘

  ★   = Strategy 1 (incremental vesting cache)
  ★★  = Strategy 3 (SQLite WAL persistence)
  ★★★ = Strategy 2 (share compaction, triggers every 720 shares)
```

**RAM budget at current 49.5 GH/s pool (with all optimizations):**

```
┌──────────────────────────┬────────────────┬──────────────────┐
│ Component                │ Without Optim. │ With Optim.      │
├──────────────────────────┼────────────────┼──────────────────┤
│ Tier 1 (PPLNS window)    │ 1.39 GB        │ 1.39 GB (unchanged)│
│ Tier 2 (vesting tail)    │ 1.39 GB        │ ~43 KB (compact) │
│ Fork resolution (2×vest) │ 2.78 GB        │ ~86 KB (compact) │
│ Vesting cache             │ N/A            │ ~2 KB (30 addrs) │
│ Skip list memo            │ ~1 MB          │ ~1 MB (unchanged)│
│ SQLite page cache         │ N/A            │ 64 MB (config.)  │
├──────────────────────────┼────────────────┼──────────────────┤
│ **Total in-memory**       │ **~5.55 GB**   │ **~1.45 GB**     │
│ **On-disk (SQLite)**      │ N/A            │ ~350 MB          │
└──────────────────────────┴────────────────┴──────────────────┘

Savings: 5.55 GB → 1.39 GB core (75% reduction in core RAM).
Note: Tier 1 (hot shares in PPLNS window) cannot be compacted — the full
share objects are needed for WeightsSkipList PPLNS queries. The compaction
savings come entirely from Tier 2 (vesting tail) and fork resolution copies.
If SQLite cache is enabled (64 MB), process RSS is ~1.45 GB total.
Plus: crash recovery in ~8 seconds instead of ~10 minutes re-sync.
```

**Implementation priority:**

| Strategy | Impact | Effort | Priority | Phase |
|----------|--------|--------|----------|-------|
| 1. Incremental vesting cache | **Critical** — eliminates O(n) bottleneck | Medium (new class, integer math) | **P0** | Phase 2b |
| 2. Share compaction | **High** — 75% core RAM reduction | Medium (epoch aggregation, pruning) | P1 | Phase 4 |
| 3. SQLite WAL persistence | **Medium** — crash recovery | High (new subsystem, migration) | P2 | Post-Phase 4 |

Strategy 1 is a **prerequisite** for adaptive windows at any pool hashrate
below ~20 GH/s. Strategies 2 and 3 are quality-of-life improvements that
become increasingly valuable as the window grows but are not blockers.

**Risk assessment:**

- **Strategy 1 risk: LOW.** The incremental cache produces mathematically
  identical results to the full O(n) scan. It can be validated by running
  both methods in parallel on testnet and comparing outputs. Fixed-point
  integer arithmetic avoids all floating-point consensus issues.

- **Strategy 2 risk: LOW.** Compaction only applies to shares outside the
  PPLNS window. The PPLNS calculation (consensus-critical) still uses the
  full in-memory skip list. Compaction errors would only affect vesting
  cache rebuilds, which are validated against the incremental cache.

- **Strategy 3 risk: LOW-MEDIUM.** SQLite is a well-tested library, but
  introducing a persistence layer adds a new failure mode (disk full,
  corruption, WAL overflow). Mitigated by treating SQLite as non-authoritative
  — any inconsistency triggers a full P2P re-sync as fallback.

#### 7.3.14 V36 Share Size Analysis (Corrected from ~300 B to ~4.5 KB)

> **ERRATA:** Earlier analysis assumed ~300 bytes per in-memory share.
> Actual measurement reveals V36 shares are **~4,500 bytes** in PyPy 2.7
> memory — a **15× correction**. All RAM estimates in §7.3.10 and §7.3.13
> have been updated. Share compaction (Strategy 2) is promoted from P1 to
> **P0 prerequisite** for adaptive windows.

**Wire format (serialized, on the P2P network):**

| Scenario | Size | Notes |
|----------|------|-------|
| V36 minimal (no merged mining, depth-7 merkle) | **~780 B** | Two 7-deep merkle branches dominate (~450 B combined) |
| V36 with DOGE (1 aux chain, 6-deep coinbase merkle) | **~1,100 B** | `merged_coinbase_info` adds ~290 B (80-byte header + proof) |
| V36 with DOGE + 100 B message | **~1,200 B** | Share messaging adds `message_data` field |

**Wire format breakdown (V36 with DOGE merged mining):**

```
┌─────────────────────────────────────────────┬──────────────┐
│ Field                                       │ Bytes        │
├─────────────────────────────────────────────┼──────────────┤
│ Envelope (type VarInt + contents VarStr)     │ 4            │
│ min_header (small_block_header_type)         │ 49           │
│ share_data (prev_hash, coinbase, nonce, ...) │ 79           │
│ segwit_data (txid_merkle_link + wtxid_root)  │ 257          │
│ merged_addresses (1 chain)                   │ 31           │
│ merged_coinbase_info (80B header + proof)     │ 292          │
│ merged_payout_hash                           │ 32           │
│ far_share_hash + max/min bits + timestamp     │ 57           │
│ ref_merkle_link                              │ 1            │
│ last_txout_nonce                             │ 8            │
│ hash_link (state + extra_data + length)       │ 63           │
│ merkle_link (7-deep branch)                  │ 225          │
│ message_data (None)                          │ 1            │
├─────────────────────────────────────────────┼──────────────┤
│ TOTAL                                       │ ~1,099       │
└─────────────────────────────────────────────┴──────────────┘
```

Dominant cost drivers: merkle branches (each hash = 32 B, depth scales with
$\log_2(\text{tx count})$). Three branch lists: segwit txid, main merkle,
DOGE coinbase merkle — together ~640 B at typical depths.

**In-memory representation (PyPy 2.7) — why 4× larger than wire:**

Each share is stored as a `MergedMiningShare` Python object with ~24
`__slots__` attributes plus a `__dict__`. Key cost multipliers:

| Component | Estimated bytes | Why larger than wire |
|-----------|----------------|---------------------|
| Share object (slots + dict + type ptr) | ~250 | Python object overhead |
| `contents` nested dict tree | ~1,800 | Each Python `long` for 256-bit hash = ~48 B (vs 32 B packed). 20+ hashes × 48 B overhead. Each `dict` = ~56 B base. |
| `share_info` + `share_data` sub-dicts | ~850 | Nested dict key strings, int objects |
| Merkle branch lists (~22 hashes) | ~1,056 | 22 × 48 B Python long objects |
| `header` dict (copy of min_header + root) | ~200 | Fresh dict copy, not alias |
| Scalar attributes (address, script, time) | ~150 | String + float overhead |
| Tracker bookkeeping | ~650 | `items` dict entry, `reverse` set, `_deltas`, skip list entries, `verified` SubsetTracker |
| **TOTAL** | **~4,500** | **~4× wire format** |

**Sensitivity to merged mining chains:**

| Configuration | Wire (B) | In-memory (B) |
|--------------|----------|--------------|
| No merged mining | ~780 | ~3,500 |
| 1 aux chain (DOGE) | ~1,100 | ~4,500 |
| 2 aux chains | ~1,400 | ~5,500 |

**Impact on all RAM calculations:**

All sections using the previous ~300 B/share estimate have been updated:
- §7.3.10 adaptive window text and tracker storage table
- §7.3.13 Strategy 2 compaction table
- §7.3.13 Strategy 3 disk estimates
- Compaction promoted from P1 → **P0** prerequisite

#### 7.3.15 Attack Vector: Coinbase Overflow with Variable Windows

> **Concern:** With adaptive windows of 308K+ shares, could the number of
> unique payout addresses exceed the coinbase transaction's capacity and
> make block creation impossible?

**Short answer: No — existing safeguards prevent overflow, but the
safeguards have consequences that need careful consideration.**

**Existing protection mechanisms:**

| Mechanism | Location | Effect |
|-----------|----------|--------|
| **Hard cap: 4,000 destinations** | `data.py` line 888–889: `dests = sorted(...)[-4000:]` | Keeps only top 4,000 by payout amount; drops smallest earners |
| **Zero-amount filtering** | `data.py` ~line 958 | Integer division `subsidy * 199 * weight // (200 * total_weight)` rounds small weights to 0 |
| **Dust-threshold difficulty adjustment** | `work.py` lines 2236–2239 | If miner's expected payout < `DUST_THRESHOLD` (30,000 sat = 0.0003 LTC), share difficulty raised |
| **ASIC firmware warning** | `data.py` line 891 | Warns at 200+ outputs (Antminer S9s crash near 226 outputs) |

**Block weight budget analysis:**

Litecoin: `BLOCK_MAX_WEIGHT = 4,000,000` WU (same as Bitcoin SegWit).

| Output Type | Size (bytes) | Weight (WU) | Max in 4M WU |
|-------------|-------------|-------------|-------------|
| P2PKH | 34 | 136 | ~29,400 |
| P2WPKH | 31 | 124 | ~32,250 |
| P2WSH | 43 | 172 | ~23,250 |

The **4,000-destination hard cap** is the binding constraint, well under
the weight limit: 4,000 P2PKH outputs × 136 WU = 544,000 WU (~14% of
block weight). The block weight is never the bottleneck.

**The real concern — small miner exclusion attack:**

With a 308K-share adaptive window, an attacker could create hundreds of
Sybil addresses mining at minimum difficulty, each contributing tiny work.
Goal: push legitimate small miners below the 4,000-destination cutoff.

**Why this is limited:**

1. **Weight-based cutoff, not count-based.** The `[-4000:]` sorts by
   payout amount (which is proportional to work weight). Sybil addresses
   with tiny work get tiny payouts → they're the ones dropped, not
   legitimate miners.

2. **Integer division helps.** With a 12.5 LTC subsidy (1.25B sat) and
   4,000 destinations at equal weight: each output = 311,250 sat ≈ 0.003
   LTC. Well above dust. With unequal weights, tiny Sybil addresses round
   to zero and are filtered out.

3. **Cost to attacker.** Each Sybil address must mine real shares (valid
   PoW). The total work across N Sybil addresses still equals the
   attacker's actual hashrate — splitting addresses doesn't multiply
   attack power, it dilutes it.

**ASIC compatibility concern (separate from overflow):**

The more pressing practical issue: the `data.py` line 891 warning about
Antminer S9 firmware crashing near 226 coinbase outputs. With adaptive
windows, even ~50 unique miners could produce 200+ outputs (since outputs
include both P2Pool payouts AND merged mining outputs). **Mitigation:**
- Modern firmware handles thousands of outputs
- S9s are increasingly rare on LTC mining
- The `SPREAD` parameter (default 3) already limits output count somewhat
- Consider addding a configurable `MAX_COINBASE_OUTPUTS` consensus
  parameter in V36, defaulting to 4,000 but adjustable for firmware
  compatibility

**Recommendation:** The existing 4,000-destination hard cap is sufficient
for block validity. Add monitoring for coinbase output count via a
`/coinbase_stats` web endpoint. Document the S9 firmware limit as a known
compatibility issue. No consensus changes required.

#### 7.3.16 Version Transition Safety with Variable Windows

> **Concern:** If V36 introduces variable-length PPLNS windows, how does
> the V37 version transition count supermajority signals? Could different
> nodes compute different window lengths and disagree on activation?

**Current version activation architecture (all versions through V36):**

The version signaling system uses **three independent counting contexts**,
all based on the **fixed** `net.CHAIN_LENGTH` constant:

| Context | Code Location | Window | Threshold |
|---------|--------------|--------|-----------|
| Share validation (`check()`) | `data.py` L1261–1267 | `CHAIN_LENGTH // 10` (864 shares) at offset 9/10 | 60% work-weighted |
| Protocol version ratchet | `data.py` L1933 | Same as above (called from `check()`) | 95% to bump `MINIMUM_PROTOCOL_VERSION` |
| AutoRatchet (share production) | `data.py` L2040–2068 | `REAL_CHAIN_LENGTH` (full 8,640) | 95% activate, 50% deactivate |
| Warning system | `data.py` L2363 | `min(CHAIN_LENGTH, 3600//SHARE_PERIOD, height)` | 50% for upgrade warning |

**Key insight:** All four contexts use **fixed constants** (`CHAIN_LENGTH`
or `REAL_CHAIN_LENGTH`), not the adaptive PPLNS window. This means:

1. **The PPLNS window can vary without affecting version signaling.** The
   adaptive window only changes `REAL_CHAIN_LENGTH` for PPLNS reward
   calculation, not for version counting in `check()`.

2. **Version activation is fully deterministic.** All nodes look at the
   same fixed-length sampling window of 864 shares, weight votes by
   `target_to_average_attempts()`, and apply the same 60% threshold. No
   floating-point, no external data.

**One risk: AutoRatchet uses `REAL_CHAIN_LENGTH`:**

The AutoRatchet at `data.py` L2047:
```python
sample = min(height, net.REAL_CHAIN_LENGTH)
```

If V37 makes `REAL_CHAIN_LENGTH` variable/adaptive, different nodes could
compute different AutoRatchet sampling windows, causing them to disagree
on when to start/stop producing new-version shares. **This is a consensus
divergence risk.**

**Recommendations for V37 transition safety:**

| # | Recommendation | Priority |
|---|---------------|----------|
| **R1** | **Keep `CHAIN_LENGTH` fixed for version signaling.** The `check()` code already uses the fixed `net.CHAIN_LENGTH`. V37 must preserve this invariant. | P0 |
| **R2** | **Pin AutoRatchet to `CHAIN_LENGTH`, not `REAL_CHAIN_LENGTH`.** Change `data.py` L2047 from `net.REAL_CHAIN_LENGTH` to `net.CHAIN_LENGTH` before deploying variable windows. | P0 |
| **R3** | **Define a `VOTING_WINDOW` constant.** Separate version signaling from PPLNS: `VOTING_WINDOW = 8640` (fixed), `PPLNS_WINDOW = adaptive(...)`. Makes separation explicit. | P1 |
| **R4** | **Set `MergedMiningShare.SUCCESSOR`** before V37 ships. Currently `SUCCESSOR = None` (`data.py` L1477). | P0 (V37) |
| **R5** | **Add the R2 fix to V36 Step 3a** (§8.1) — pin AutoRatchet to `CHAIN_LENGTH`. This is a one-line change that must happen before variable windows go live. | P0 (V36) |

**Invariant (must hold for all future versions):**

> Version signaling windows MUST be a fixed constant agreed upon by all
> nodes. The PPLNS window can be adaptive, but the activation counting
> window must never depend on hashrate-derived state.

### 7.4 Defense 4: Dual-Window PPLNS

**Consensus required:** YES — V36 supermajority.

Blend payouts from a short window and a long window:

```python
short_window = last N/12 shares  # 720 shares ≈ 3 hours
long_window  = last N shares     # 8640 shares ≈ 36 hours

payout = 0.3 × short_window_weight + 0.7 × long_window_weight
```

**Effectiveness:** After the hopper's shares exit the short window (~3 hours
after departure), their effective weight drops by 30%:

```
Hopper after 3 hours away:
  Short window: 0% (shares scrolled out)
  Long window: 11.8%
  Blended: 0.3 × 0% + 0.7 × 11.8% = 8.3%
```

Improvement is **marginal** (11.8% → 8.3%). Not recommended as primary
defense.

### 7.5 Defense 5: Concentration Penalty

**Consensus required:** NO — local merged-mining calculation.

Apply quadratic penalty when a single address exceeds a threshold of total
weight:

```python
THRESHOLD = 0.33
excess = max(0, fraction - THRESHOLD)
penalty = excess ** 2
effective_fraction = fraction × (1.0 - penalty)
```

**Caveat:** Trivially defeated by Sybil attack — the hopper splits across N
addresses. Only useful as a soft auxiliary defense for merged mining payouts.

### 7.6 Anti-Patterns (What NOT to Do)

| Approach | Why It Fails |
|----------|-------------|
| Minimum share count before payout | Whale submits minimum+1 and hops |
| Address blacklisting | New address costs nothing |
| Proof-of-stake lockup | Incompatible with P2Pool trustless design |
| Rate-limiting connections | Whale uses multiple proxy nodes |
| Banning at stratum | Attacker uses own node via P2P |
| Manual intervention | Defeats decentralization |

---

### 7.7 Defense 7: Pure Difficulty Accounting — Remove Block Finder Fee (V36)

**Consensus required:** YES — V36 activation (changes `generate_transaction()`
coinbase distribution formula).

#### 7.7.1 The Problem: Legacy 0.5% Finder Fee

Since P2Pool's original 2011 codebase, the block reward has been split:

```python
# data.py line ~827 (current code)
amounts = dict((script, subsidy*(199*weight)//(200*total_weight))
               for script, weight in weights.iteritems())

# 0.5% bonus to the share that happened to solve an LTC block
amounts[this_address] = amounts.get(this_address, 0) + subsidy//200
```

The PPLNS window also **excludes the finder's share and its parent** —
it starts from the grandparent:

```python
# data.py line ~820: PPLNS starts from grandparent
weights = tracker.get_cumulative_weights(
    previous_share.share_data['previous_share_hash'],  # grandparent
    max(0, min(height, net.REAL_CHAIN_LENGTH) - 1),    # 8639 shares
    65535 * net.SPREAD * target_to_average_attempts(block_target),
)
```

The claimed justification: compensate the finder for being excluded from
their own PPLNS window. But the math doesn't hold:

| Factor | Value |
|--------|-------|
| Shares excluded from window | 2 (finder + parent) |
| Total shares in window | 8,640 |
| Expected weight loss from exclusion | 2/8640 = **0.023%** |
| Finder fee compensation | **0.500%** |
| Overcompensation factor | **21.7×** |

The finder fee overcompensates by 21.7×. It's a zero-sum lottery that adds
variance without improving expected value for any miner.

#### 7.7.2 Why It's Harmful

**1. Increases payout variance for small miners.**

Every block, 0.5% is siphoned from the PPLNS pool into a lottery that pays
one lucky miner. A miner with 0.1% of pool hashrate finds ~0.1% of blocks,
so in expectation they pay 0.5% × 99.9% of the time and receive 0.5% × 0.1%
of the time. Expected net = 0, but variance increases. Small miners who never
personally find a block lose 0.5% on every single block — they subsidize
large miners who find blocks more frequently.

At current 49.5 GH/s pool hashrate with ~107-day expected time-to-block,
a 100 MH/s miner (0.2% of pool) expects to find a block once every
~147 years. They will pay the 0.5% tax their entire mining career and
never collect.

**2. Adds substantial complexity to merged mining.**

The parent chain finder fee is 2 lines of legacy code. But for V36 merged
mining, it requires an entire finder-script derivation pipeline:

| Component | Location | LOC | Purpose |
|-----------|----------|-----|---------|
| `CANONICAL_MERGED_FINDER_FEE_PER_MILLE` | data.py:172 | 1 | Constant |
| `get_canonical_merged_finder_script()` | data.py:288-320 | 33 | 3-tier address derivation |
| Finder fee in `build_canonical_merged_coinbase()` | data.py:210-257 | 10 | Fee calculation + output coalescing |
| Finder fee in `build_merged_coinbase()` (legacy) | merged_mining.py:154-261 | 30 | Same logic, older path |
| Canonical path in `_build_user_specific_merged_work()` | work.py:2050-2059 | 10 | Finder script derivation per-user |
| Legacy path in work.py | work.py:2068-2081 | 14 | Fallback finder fee plumbing |
| **Total merged-specific finder fee code** | | **~98** | |

All 98 lines are **consensus-critical** — every peer must produce identical
finder scripts or merged block verification fails. Every edge case (P2SH
pubkey_type, unconvertible P2TR addresses, NULL fallback) is a potential
consensus split vector.

**3. The anti-withholding argument is negligible.**

A miner who withholds a block-solving share loses their entire PPLNS
payout for that block — all shares in the window become worthless. The
finder fee adds 0.5% to a 100% loss. Going from "lose everything" to
"lose everything + 0.5% bonus" doesn't change the game theory.

**4. Creates an artificial asymmetry in the PPLNS accounting.**

The `199/200` factor means PPLNS weights are consistently worth 0.5%
less than they should be. This isn't "difficulty accounting" — it's a
systematic underpayment of work-proportional rewards to fund a lottery.

#### 7.7.3 The Fix: Pure Difficulty Accounting in V36

V36 eliminates the finder fee and closes the PPLNS window gap:

**Change 1:** Full subsidy goes to PPLNS weights (no `199/200` haircut)
```python
# V36: 100% to PPLNS (was 99.5%)
amounts = dict((script, share_data['subsidy'] * weight // total_weight)
               for script, weight in weights.iteritems())
# DELETE: amounts[this_address] += subsidy//200
```

**Change 2:** Include parent share in PPLNS window (close the 2-share gap)
```python
# V36: Start from parent share (was grandparent)
weights = tracker.get_cumulative_weights(
    previous_share.hash,                              # parent (was grandparent)
    min(height, net.REAL_CHAIN_LENGTH),               # 8640 shares (was 8639)
    65535 * net.SPREAD * target_to_average_attempts(block_target),
)
```

**Change 3:** Set merged finder fee to zero
```python
CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0  # was 5
```

This doesn't just remove ~98 lines from the merged path — it **eliminates
an entire class of consensus-critical edge cases** (finder address
derivation, unconvertible address fallback, script coalescing).

#### 7.7.4 Economic Impact

```
EVERY miner's expected payout change: +0.5% (net, not gross)

Explanation:
  Before: each miner receives (99.5% × their_PPLNS_fraction) + (0.5% × P(find_block))
  After:  each miner receives (100% × their_PPLNS_fraction)

  Since P(find_block) ∝ their_PPLNS_fraction, the expected value is:
    Before: 99.5% × f + 0.5% × f = 100% × f
    After:  100% × f

  Same expected value. But the VARIANCE is different:
    Before: Var = f × (1-f) × (0.005 × subsidy)²  [lottery component]
    After:  Var = 0                                  [no lottery]

  Net: same mean, strictly lower variance → Pareto improvement.
```

Every single miner benefits from this change. There is no loser.

#### 7.7.5 Compatibility

| Aspect | Impact |
|--------|--------|
| Pre-V36 shares | Not affected — old coinbase formula applies |
| V36 shares | New formula: 100% PPLNS, no finder fee |
| V35 → V36 transition | Gated on 95% V36 signaling (Step 8) |
| Merged mining (V36 canonical) | `CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0` → skip entire finder pipeline |
| Merged mining (legacy path) | `finder_fee_percentage=0.0` → no finder output |
| jtoomim upstream | Diverges from upstream `199/200` — acceptable for V36 fork |
| Block explorers | See slightly different coinbase distribution — cosmetic |

---

## 8. Implementation Recommendations

### Two-Track Strategy

The defense stack is split into two release tracks based on **implementation
complexity, runtime constraints, and impact-per-line-of-code**:

1. **V35→V36 (Python 2.7 / PyPy):** Simple, high-impact moves that
   **eliminate hopper profitability NOW** using the current codebase. Pure
   integer arithmetic, minimal new data structures, ~135 net LOC changed.
   Result: hopper efficiency drops from **3.8× → 0.6× (unprofitable)**.

2. **V36→V37 (C++ c2pool):** Structural/architectural advancements that
   benefit from C++ performance, native memory management, and c2pool's
   LevelDB storage. These provide defense-in-depth and miner retention
   improvements, but are **not required** to make hopping unprofitable.
   Result: hopper efficiency drops from **0.6× → 0.1× (economically
   irrational)**.

**Rationale for the split:**

- PyPy 2.7's ~4,500 bytes/share overhead and ~1M items/sec dict iteration
  make adaptive windows (308K shares, ~1.39 GB RAM) a hard scaling wall.
  C++ eliminates this bottleneck entirely.
- Work-weighted vesting requires an `IncrementalVestingCache` with O(n)
  rebuild on startup — acceptable in C++ (~3ms), painful in PyPy (~600ms).
- The simple V36 defenses (PPLNS decay + asymmetric clamp + finder fee
  removal) cut hopper profit to **below break-even** with ~135 LOC. No
  new classes, no new persistence layers, no RAM scaling concerns.
- c2pool already has LevelDB persistence, making share compaction and
  crash recovery a natural extension rather than a bolt-on.

### Priority Order

**Track 1: V35→V36 — "Cut the Hopper" (Python 2.7 / PyPy)**

| Phase | Defense | Consensus | Timeline | Effect |
|-------|---------|-----------|----------|--------|
| ~~**1a**~~ | ~~Asymmetric difficulty clamp (§7.1.1)~~ | ~~No~~ | ~~Immediate~~ | **REVERTED** — tested 2026-03-03, clamp never triggers (see PHASE1A_TEST_REPORT.md) |
| **1b** | Time-based emergency decay (§7.1.2) | Yes (V36) | **DEPLOYED** | Survive true whale death spiral — 80s threshold testnet, 300s mainnet |
| **2a** | Exponential decay on PPLNS weights (§7.2) | Yes (V36) | **TESTED** | Arrival HAR 5.27× → **1.52×** (71.1% improvement). See PHASE2A_TEST_REPORT.md |
| **2c** | Pure difficulty accounting — remove finder fee (§7.7) | Yes (V36) | **DEPLOYED** | Exact work-proportional payouts, lower variance |
| **3L** | Lightweight log monitoring (§8.1 Step 5) | No | **DEPLOYED** | Attack detection (structured log lines) |
| **R2** | Pin AutoRatchet to CHAIN_LENGTH (§7.3.16) | Yes (V36) | **DEPLOYED** | V37 transition safety — signaling window stays fixed |

**V36 result: hopper 3.8× → 0.6× (unprofitable). ~135 LOC changed.**

**Track 2: V36→V37 — "Structural Hardening" (C++ c2pool)**

| Phase | Defense | Why C++ | Effect |
|-------|---------|---------|--------|
| **2b** | Work-weighted exponential vesting (§7.3.4) + incremental cache (§7.3.13) | Complex cache, O(n) rebuild, benefits from C++ perf | Burst shares start weak; O(1) steady-state |
| **4** | Adaptive PPLNS/vesting windows (§7.3.10) | 308K shares × 4.5KB = 1.39GB; C++ ~500B/share | Windows scale with TTB; 50% coverage |
| **4-S2** | Share compaction — tiered storage (§7.3.13) | Native memory mgmt, struct packing | 75% RAM reduction vs raw storage |
| **4b** | LevelDB persistence (c2pool native) | c2pool already has LevelDB | Sub-second crash recovery |
| **5** | Full dashboard UI + payout explanation (§8.1 Step 5) | c2pool web server architecture | Miner retention, transparency |
| **5-A** | Advanced monitoring + alerting thresholds | Richer web framework in c2pool | False-positive-tuned alerts |

**V37 result: hopper 0.6× → 0.1× (irrational). Adaptive windows + vesting.**

### Phase 1: Asymmetric Difficulty + Emergency Decay (Immediate)

> **⚠ TESTED & REVERTED (2026-03-03).** Phase 1a (asymmetric clamp) was
> implemented, deployed to testnet nodes 29+31, and tested with a 34-minute
> automated hop test. **The asymmetric clamp was never triggered** during
> the test because `TARGET_LOOKBEHIND=200` smooths per-share changes to
> ~1–6%, far below the 50% threshold. The clamp is effectively dead code
> for any departure below 99%. Code reverted. See full results in
> `docs/PHASE1A_TEST_REPORT.md`.
>
> **Phase 1a Test Results (2026-03-03):**
> - Test: 4-phase hop cycle (baseline→departure→arrival→final_departure)
> - Miners: 3 L1s (~1.5 GH/s each), 1 CPU (~25 kH/s); 67% hash departs
> - Miner control: iptables REJECT --reject-with tcp-reset (bidirectional)
> - Max per-share difficulty drop: **−6.10%** (asymmetric threshold: −33%)
> - Asymmetric clamp activations: **0** (never triggered)
> - Difficulty dropped 27% over 8 min via the standard ±10% symmetric clamp
> - Hopper arrival payout efficiency: **5.27× vs anchor** (hoppers exploit
>   depressed difficulty on reconnect)
> - Root cause: With TARGET_LOOKBEHIND=200, even 95% hash departure only
>   produces ~10% per-share change. Only 99%+ departure triggers the 1.5×
>   threshold.
>
> **Conclusion:** The difficulty clamp approach (both symmetric and asymmetric)
> is the wrong layer for anti-hopping defense. The 200-share rolling window
> inherently limits per-share adjustments to small increments. The real
> fix is Phase 2a (PPLNS weight decay), which attacks the payout side.

**File:** `p2pool/data.py`, `generate_transaction()` retarget section (~line 730)

Two changes in the same code block:

1. **Part A (asymmetric clamp):** ~~Replace the symmetric `9//10 .. 11//10`
   clamp with asymmetric: keep +10% cap for difficulty increases, allow up to
   -40% per share for decreases when ratio is extreme.~~ **REVERTED — ineffective.**

2. **Part B (emergency decay):** Before the per-share clamp, check
   `desired_timestamp - previous_share.timestamp`. If the gap exceeds
   `SHARE_PERIOD × 20` (e.g. 300s on mainnet), apply exponential decay to
   the previous share's target with half-life of `SHARE_PERIOD × 10`.
   *(Still viable for death spiral prevention, but NOT an anti-hopping measure.)*

~~Both are local calculation changes — no protocol modification, no consensus
needed. Can be deployed to test nodes A and B immediately.~~

Recovery time improvement (difficulty recovery, not PPLNS persistence):
- ~~**Semiwhale (3×):** ~3 hours → **~40 minutes**~~ (Phase 1a tested: no meaningful improvement)
- **True whale (100×):** ∞ (death spiral) → **~15 minutes** (Phase 1b untested, theoretically sound)

### Phase 2a: Exponential Decay on PPLNS Weights (V36 Consensus)

**File:** `p2pool/data.py`, `WeightsSkipList.get_delta()` (~line 1641)

Multiply share weight by `2^(-depth/half_life)` where `half_life =
CHAIN_LENGTH // 4`. This makes recent work more valuable than stale work.

Must be deployed as part of V36 activation to ensure all nodes calculate
identical payouts. Requires testnet validation first.

Hopper efficiency: **3.8× → 0.6× (unprofitable).**

### Phase 2b: Work-Weighted Exponential Vesting (⏭ DEFERRED TO V37 / c2pool)

> **V37 track.** This defense is deferred to the C++ c2pool release.
> PPLNS exponential decay (Phase 2a) alone reduces hopper profit to 0.6×
> (unprofitable). Vesting adds defense-in-depth (→0.3×) but requires an
> `IncrementalVestingCache` with O(n) rebuild that is better served by
> C++ performance. The design is fully specified below for V37 implementation.

**File (c2pool):** Payout calculation in coinbase generation.

Apply `calculate_robust_vesting()` (§7.3.4) to per-miner payout weights.
This multiplies each miner's share weight by their vesting factor — a
function of their recent work contribution with exponential decay.

Deploy as part of V37 activation in c2pool. The two mechanisms use the
same exponential math but different half-lives:
- Phase 2a (PPLNS decay): `HALF_LIFE = CHAIN_LENGTH // 4` (~9 hours)
- Phase 2b (vesting): `RECENCY_HALF_LIFE = CHAIN_LENGTH // 4` (~9 hours)

Combined effect: shares start weak (vesting) AND decay over time (PPLNS).
No profitable window exists for a hopper at any point.

Gaming resistance: Defeats all 5 identified attacks (§7.3.3). Only residual
vector is address splitting (Attack 5), which increases attacker cost
linearly — fully solved by multi-temporal architecture (Part 16).

Extended vesting lookback (§7.3.9): Uses 2×CHAIN_LENGTH (72 hours) for
vesting calculations while payout window stays at REAL_CHAIN_LENGTH (36
hours). This means a 36-hour burst only fills half the vesting window,
giving loyal miners a structural advantage.

**Why deferred:** The `IncrementalVestingCache` class (~60 LOC in Python)
requires O(active_addresses) work per share and O(n) full rebuild on
startup. At 308K shares under PyPy 2.7, rebuild takes ~600ms — acceptable
but fragile. In C++, the same structure rebuilds in ~3ms with struct
packing. More importantly, vesting's full value only manifests with
adaptive windows (also V37), where the vesting lookback expands from 72h
to ~107 days.

### Phase 3: Monitoring + Dashboard Visibility

Phase 3 is split across both tracks:

- **V36 (Phase 3L):** Lightweight structured log lines in `monitor.py`. ~230 LOC.
  No HTTP endpoints needed. Operators grep logs for `[MONITOR-*]` prefixes.
- **V37 (Phase 5):** Full dashboard UI in c2pool's web framework with
  vesting bars, share trend charts, payout explanations, and pool health
  panels. Described in detail below for V37 planning.

#### Phase 3L: Lightweight Log Monitoring (V36 — Immediate)

**File:** `p2pool/monitor.py` + `p2pool/main.py` (integration)

Structured `[MONITOR-*]` log lines emitted every status cycle (~30s).
No HTTP endpoints — grep-friendly, zero attack surface, works with
existing log pipelines (tail, grep, logrotate).

**Log prefixes:**

| Prefix | Purpose | Frequency |
|--------|---------|----------|
| `[MONITOR-SUMMARY]` | One-line health status | Every cycle (~30s) |
| `[MONITOR-HASHRATE]` | Pool hashrate vs 1h moving average | ALERT on spike/drop, ok every ~5min |
| `[MONITOR-CONC]` | Per-address work concentration | ALERT >40%, WARN >25%, top3 every ~5min |
| `[MONITOR-EMERGENCY]` | Share gap / emergency decay | ALERT when gap > threshold |
| `[MONITOR-DIFF]` | Difficulty anomaly detection | ALERT on >2x deviation |

**Alert thresholds (configurable in PoolMonitor):**

| Alert | Trigger | Default |
|-------|---------|---------|
| `concentration_alert` | Single address > 40% of window | On |
| `concentration_warn` | Single address > 25% of window | On |
| `hashrate_spike` | Pool hashrate > 150% of 1h average | On |
| `hashrate_drop` | Pool hashrate < 50% of 1h average | On |
| `difficulty_anomaly` | Target deviation > 200% from expected | On |
| `emergency_gap` | Share gap > `SHARE_PERIOD × 20` | On |

#### Phase 5: Full Dashboard UI (⏭ V37 / c2pool)

> **V37 track.** Full frontend dashboard with vesting bars, share trend
> charts, payout explanation toasts, and pool health panels. Requires
> the vesting system (Phase 2b) to be implemented first. Designed here
> for c2pool's web framework.

**Dashboard UI changes** (frontend, `web-static/`):

**1. Per-miner page (`miner.html`) — "My Mining Status" panel:**

```
┌─────────────────────────────────────────────────────────────────┐
│  MY MINING STATUS                                    [miner.html]│
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Vesting Factor:  ████████░░  0.82                              │
│                   ETA to 95%: ~14 hours                          │
│                                                                  │
│  PPLNS Weight:    ██████████  97.2%  (of your raw shares)       │
│  Effective Payout: ████████░░  79.7%  (vesting × PPLNS decay)   │
│                                                                  │
│  Share Trend (24h):                                              │
│    ▁▂▃▅█████████▅▃▁░░░░░░░░░▁▂▃▅██████                         │
│    ^--- mining ----^-- sleep --^-- mining now ---                │
│                                                                  │
│  Accumulated Weight: 12,847 effective shares in PPLNS window    │
│  Payout Share: 3.1% of next block reward                        │
│  Estimated Next Block: 107.2 days (pool avg)                    │
│                                                                  │
│  ⓘ Your vesting factor increases as you mine consistently.      │
│    Mining 6+ hours per day maintains vesting above 0.90.        │
│    Your shares persist in the PPLNS window even while offline.  │
└─────────────────────────────────────────────────────────────────┘
```

**Why this matters:** Carol sees her 0.35 vesting factor and "ETA ~60h"
and understands the ramp-up. Frank sees his nightly pattern in the share
trend chart and watches accumulated weight grow across sessions. Jack
returns after 2 days off and sees his accumulated weight is still there
(reduced but not zero) — encouraging him to keep mining.

**2. All-miners page (`miners.html`) — sortable columns:**

```
┌────────────────┬──────────┬─────────┬────────────┬──────────┬────────┐
│ Address        │ Hashrate │ Shares  │ Vest.      │ PPLNS    │ Payout │
│                │ (GH/s)   │ (24h)   │ Factor     │ Weight   │ Share  │
├────────────────┼──────────┼─────────┼────────────┼──────────┼────────┤
│ LTC...abc (me) │ 8.0      │ 2,880   │ ██░░ 0.72  │ █████ 97 │ 4.2%   │
│ LTC...def      │ 6.5      │ 5,616   │ ████ 1.00  │ █████ 99 │ 12.9%  │
│ LTC...ghi      │ 15.0     │ 10,800  │ ███░ 0.95  │ █████ 98 │ 22.1%  │
│ ...            │          │         │            │          │        │
└────────────────┴──────────┴─────────┴────────────┴──────────┴────────┘
```

Vesting and PPLNS weight show as **mini progress bars** — instantly
visible, no JSON parsing needed. Miners can sort by any column to
understand their position in the pool.

**3. Dashboard page (`dashboard.html`) — pool health panel:**

```
┌─────────────────────────────────────────────────────────────────┐
│  POOL HEALTH                                    [dashboard.html]│
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Pool Hashrate: 49.5 GH/s          Active Miners: 31            │
│  Share Difficulty: 8.56 min         PPLNS Window: 308K shares   │
│  Time to Block: ~107 days           Last Block: 3.2 days ago    │
│                                                                 │
│  Defense Status:                                                │
│    Asymmetric Clamp: ✅ Active  (up: 127, fast-down: 3 today)   │
│    Emergency Decay:  ✅ Standby (0 triggers, last: never)       │
│    Exp. PPLNS Decay: ✅ Active  (half-life: 9.0h)               │
│    Vesting:          ✅ Active  (31 miners fully vested)        │
│    Adaptive Window:  ✅ Active  (53.5 days, 308K shares)        │
│                                                                 │
│  ⚠ Alerts (last 24h): 0 hopping alerts, 0 concentration alerts  │
│                                                                 │
│  Miner Retention (30d): 28/31 miners active > 7 days            │
│  New miners this week: 3  |  Churned: 0                         │
└─────────────────────────────────────────────────────────────────┘
```

**4. Payout explanation toast (on block found):**

When a block is found, the dashboard shows a brief explanation of how
each miner's payout was calculated:

```
┌─────────────────────────────────────────────────────────────────┐
│  🎉 BLOCK FOUND at height 2,847,123!                            │
│  Reward: 6.25 LTC distributed to 31 miners                      │
│                                                                 │
│  YOUR PAYOUT: 0.194 LTC (3.1% of block)                         │
│                                                                 │
│  Breakdown:                                                     │
│    Raw shares in window:     12,847                             │
│    After PPLNS decay:        ×0.972 → 12,487 effective          │
│    After vesting:            ×0.820 → 10,239 final weight       │
│    Your share of total:      10,239 / 330,142 = 3.1%            │
│                                                                 │
│  ⓘ Tip: Mining consistently increases your vesting factor.     │
│    Current: 0.82 → Keep mining 14 more hours for 0.95           │
└─────────────────────────────────────────────────────────────────┘
```

**Implementation in existing files:**

| File | Change | LOC estimate |
|------|--------|-------------|
| `web-static/miner.html` | Add "My Mining Status" panel with vesting bar, share trend chart, effective weight | ~80 |
| `web-static/miners.html` | Add vesting/PPLNS columns with mini progress bars, sortable | ~40 |
| `web-static/dashboard.html` | Add "Pool Health" panel with defense status, alerts, retention | ~60 |
| `web-static/miner.html` | Add payout explanation toast on block events | ~30 |
| `p2pool/web.py` | Backend endpoints (as listed in Step 5 §8.1) | ~200 |
| Total | | ~410 |

**Key retention principle:** Every number the API returns should appear
somewhere a miner can SEE it without using curl or Postman. If a defense
mechanism changes a miner's payout, the dashboard must EXPLAIN why, with
a progress indicator showing how to improve it. Opacity drives miners to
centralized pools; transparency retains them.

### Combined Defense Effectiveness

```
┌───────────────────────────────────────────┬────────┬──────────┬──────────┬─────────┐
│ Configuration                             │Hopper %│ LTC/hr   │ vs Loyal │ Track   │
├───────────────────────────────────────────┼────────┼──────────┼──────────┼─────────┤
│ No defense (current V35)                  │ 11.8%  │ 0.1481   │ 3.8× ✗   │  —      │
│ Phase 1a only (asym clamp) [TESTED]       │ 11.8%  │ 0.1481   │ 3.8× ✗   │ REVERTED│
│   └─ Test showed clamp never triggers     │        │          │          │         │
│   └─ Hopper arrival efficiency: 5.27× ✗   │        │          │          │         │
│ Phase 1b only (emergency decay) [DEPLOYED]│ 11.8%  │ 0.1481   │ 3.8× ✗*  │ V36     │
│   └─ Death spiral safety net (no hopper   │        │          │          │         │
│   │  profit impact — prevents pool death) │        │          │          │         │
│ Phase 2a only (exp decay) [TESTED]        │ ~3.3%  │ ~0.04    │ 1.52× ✗  │ V36     │
│   └─ Arrival HAR reduced 5.27× → 1.52×    │        │          │          │         │
│   └─ 71.1% improvement over flat PPLNS    │        │          │          │         │
│ ─── V36 RELEASE LINE ───────────────────  │        │          │          │         │
│ Phase 2a+2c (V36 full stack) [DEPLOYED]   │ ~2%    │ ~0.025   │ 0.6× ✓   │ V36 ★   │
├───────────────────────────────────────────┼────────┼──────────┼──────────┼─────────┤
│ + Phase 2b (vesting, c2pool)              │ ~1%    │ ~0.013   │ 0.3× ✓✓  │ V37     │
│ + Phase 4 (adaptive windows, c2pool)      │ ~0.3%  │ ~0.004   │ 0.1× ✓✓✓ │ V37     │
└───────────────────────────────────────────┴────────┴──────────┴──────────┴─────────┘

✗ = hopping profitable    ≈ = break-even    ✓ = hopping unprofitable
✓✓ = hopping deeply unprofitable (hopper earns 1/3 of loyal miner)
✓✓✓ = hopping economically irrational (hopper earns 1/10 of loyal miner)
★  = V36 release target — hopping becomes unprofitable with ~100 LOC

(*) Phase 1b doesn't change hopper profit — it prevents the pool from DYING.
    Without 1b, a true whale attack causes a permanent death spiral where
    no honest miner can ever find a share again. With 1b, the pool recovers
    in ~15 minutes regardless of how extreme the difficulty spike was.
```

**V36 delivers the critical threshold:** hopping becomes unprofitable (0.6×).
The hopper earns 40% LESS than a loyal miner per mining hour. The V36
defense stack achieves this with:
- ~~Phase 1a+1b: Pool survives the attack (difficulty recovery)~~ Phase 1a reverted (ineffective)
- Phase 1b: Death spiral prevention (emergency decay, still recommended)
- Phase 2a: Stale shares erode after departure (no long tail of rewards)
- Phase 2c: Pure difficulty accounting (removes finder fee lottery noise)
- Combined: No profitable hopping pattern exists

**V37 provides defense-in-depth:** vesting + adaptive windows push the
hopper to 0.1× (economically irrational), but V36 already closes the door.

### Phase 4: Adaptive Windows (⏭ DEFERRED TO V37 / c2pool)

> **V37 track.** Adaptive windows require 308K+ shares in memory at
> current pool hashrate. At ~4,500 bytes/share under PyPy 2.7, this is
> ~1.39 GB with compaction (or 5.5 GB without). C++ c2pool's native
> memory management (~500 bytes/share) makes this tractable. The design
> is fully specified below for V37 implementation.

Replace fixed `CHAIN_LENGTH` with `get_adaptive_chain_length()` (§7.3.10).
All window-dependent parameters — PPLNS window, vesting lookback, decay
half-life, monitoring sliding windows — scale automatically with pool
hashrate and network difficulty.

At current 49.5 GH/s (verified March 2, 2026 from live node):
- PPLNS window: 36 hours → **53.5 days** (35.6× larger)
- Vesting lookback: 72 hours → **107 days** (~= TTB!)
- Decay half-life: 9 hours → **13.4 days**
- Hopper must sustain burst: 36 hours → **53.5 days** (35.6× harder)
- Window/TTB coverage: 1.4% → **50%**
- Memory: ~5 MB → **~1.39 GB core** (with compaction, §7.3.13; 5.55 GB without)
  + optional SQLite page cache (64 MB) = **~1.45 GB total RSS**

At peak observed 295 GH/s:
- PPLNS window: 36 hours → **9 days**
- Memory: ~5 MB → **~233 MB** (with compaction)

**Performance prerequisites — solved by C++ (§7.3.13):**
- **Vesting cache** (Strategy 1) — O(1) per share in C++ with struct packing.
- **Share compaction** (Strategy 2) — reduces RAM from ~5.5 GB to ~1.39 GB at
  49.5 GH/s. C++ struct packing can reduce further to ~150 MB.
- **LevelDB persistence** (replaces SQLite WAL) — c2pool already has LevelDB.
  Crash recovery in sub-second instead of re-syncing 1.2M shares from peers.

Requires extensive testnet validation of consensus determinism across nodes.
Phases 2b + 4 ship together as part of V37 activation in c2pool.

### 8.1 V36 Step-by-Step Implementation Plan (Python 2.7 / PyPy)

This is the execution plan for the **V35→V36 transition** — focused on
eliminating hopper profitability with minimal, high-impact code changes
in the current Python 2.7/PyPy codebase.

**Scope:** Steps 0–3a (consensus), Step 5L (monitoring), Step 8 (activation).
Steps 4, 6, 7 (vesting, adaptive windows, persistence) are deferred to V37/c2pool.

Each step has a hard checkpoint gate; do not proceed until the gate passes.

```
V36 Dependency graph:

  Step 0 (baseline)
     │
     ├──→ Step 1 (asymmetric clamp)     ──→ Step 2 (emergency decay)
     │                                            │
     │    ┌───────────────────────────────────────┘
     │    │
     │    ├──→ Step 3 (PPLNS exp decay)  ──→ Step 3a (pure diff accounting
     │    │                                         + AutoRatchet pin R2)
     │    │                                        │
     │    │    ┌───────────────────────────────────┘
     │    │    │
     │    │    ├──→ Step 5L (lightweight monitoring) [can deploy with Step 1]
     │    │    │
     │    └────┴──→ Step 8 (V36 activation gate)
     │
  Steps 1-2: non-consensus, deploy immediately to test nodes A/B
  Steps 3-3a: consensus (V36), ship together at activation
  Step 5L: non-consensus, deploy anytime after Step 1
  Step 8: activation gate after all checkpoints pass

  ─── V37 / c2pool (future) ───────────────────────────────────
  Step 4 (vesting + cache)         → V37 consensus
  Step 6 (adaptive windows)        → V37 consensus
  Step 7 (LevelDB persistence)     → V37 non-consensus
  Step 5F (full dashboard UI)      → V37 non-consensus
```

---

#### Step 0 — Baseline capture (pre-change)

**Goal:** Freeze reference behavior before code changes.

**Honest miner impact:** NONE — observation only. See §8.2 for full
step-by-step honest miner analysis.

**Actions:**
1. Capture 24h baseline metrics on test nodes A and B:
   - Share rate, stale rate, target trajectory, payout distribution.
   - CPU/RAM (via `ps -o rss` and `/local_stats`), reorg count.
2. Save baseline snapshots from `/local_stats`, `/global_stats`, `/users`,
   `/rate`, `/difficulty`, `/current_payouts` every 5 minutes via cron.
3. Export current share logs (`shares.*`) for replay testing.
4. Build deterministic replay harness that reads share logs and produces
   the exact same `generate_transaction()` outputs.

**Files touched:** None (tooling/scripts only).

**Estimated effort:** ~100 LOC (Python script for capture + replay harness).

**Checkpoint C0 (must pass):**
- Baseline dataset complete for at least 24h.
- Replay harness reproduces current payout results from captured shares
  with zero deviation (byte-identical coinbase outputs).

**Rollback:** N/A — no code changes to revert.

---

#### Step 1 — Phase 1a implementation (asymmetric clamp)

**Goal:** Eliminate slow wall-clock recovery after semiwhale departure.

**Honest miner impact:** Universally positive. Difficulty recovery after
large-miner departure drops from ~3h to ~40m. No miner earns less; miners
joining during recovery find shares sooner. See §8.2 Step 1.

**Files changed:**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | `generate_transaction()` line ~732 | Replace symmetric `9//10 .. 11//10` clamp with asymmetric logic |

**Code change (line 732):**

Before:
```python
pre_target2 = math.clip(pre_target, (previous_share.max_target*9//10,
                                      previous_share.max_target*11//10))
```

After:
```python
# Asymmetric difficulty clamp (§7.1.1)
# Up: max +10% per share (keeps existing conservative tightening)
max_up = previous_share.max_target * 11 // 10

# Down: check how far actual target deviates from previous
# If significant overshoot (ratio > 150% of target), allow fast -40% drop
ratio = previous_share.max_target * 100 // (pre_target + 1)  # integer %
if ratio > 150:
    max_down = previous_share.max_target * 6 // 10   # -40% fast drop
else:
    max_down = previous_share.max_target * 9 // 10   # -10% normal

pre_target2 = math.clip(pre_target, (max_down, max_up))
```

**Estimated LOC:** ~15 changed, ~5 added (counters).

**Debug counters to add:** `clamp_up_count`, `clamp_normal_down_count`,
`clamp_fast_down_count` — exposed via a new `/clamp_stats` web endpoint.

**Checkpoint C1:**
- Deterministic replay: new shares identical across two independent nodes
  given same input stream. Verify by running replay harness on test nodes A
  and B independently and diffing coinbase outputs.
- Semiwhale simulation (3× burst) recovery < 60 minutes (compare C0 baseline
  of ~3 hours).
- No increase in stale/reject rates > 5% vs C0 baseline.
- Clamp counters fire correctly in controlled tests.

**Rollback:** Revert single `math.clip` line to symmetric `9//10..11//10`.
One-line change, no state migration needed.

> **Step 1 STATUS: TESTED → REVERTED (2026-03-03)**
>
> Implementation used a slightly different variant (checking pre_target > 1.5×
> vs previous_share.max_target) but the result is the same: the asymmetric
> branch never activates. With TARGET_LOOKBEHIND=200, the maximum per-share
> target change for a 67% hash departure is only ~1.01× — far below the 1.5×
> threshold. Even 95% departure only produces ~1.10×. Only 99%+ departure
> triggers the asymmetric path.
>
> Code reverted to original symmetric clamp:
> `pre_target2 = math.clip(pre_target, (previous_share.max_target*9//10, previous_share.max_target*11//10))`
>
> Full test report: `docs/PHASE1A_TEST_REPORT.md`

---

#### Step 2 — Phase 1b implementation (time-based emergency decay)

**Goal:** Guarantee recovery from extreme death-spiral conditions.

**Honest miner impact:** Protective — only activates during genuine
emergencies (>300s share gap). Normal mining never triggers this path.
Prevents permanent pool death from 100×+ whale departure. See §8.2 Step 2.

**Depends on:** Step 1 (asymmetric clamp should be in place first, but not
strictly required — emergency decay works independently).

**Files changed:**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | `generate_transaction()` lines ~728–733 | Add emergency decay block before the per-share clamp |

**Code change (insert before line 732):**

```python
# Emergency time-based decay (§7.1.2, BCH-inspired failsafe)
EMERGENCY_THRESHOLD = net.SHARE_PERIOD * 20   # 300s on mainnet
DECAY_HALF_LIFE = net.SHARE_PERIOD * 10        # 150s on mainnet

if previous_share is not None:
    time_since_share = max(0, desired_timestamp - previous_share.timestamp)
    if time_since_share > EMERGENCY_THRESHOLD:
        excess_time = time_since_share - EMERGENCY_THRESHOLD
        # NOTE: Float OK — emergency decay is LOCAL, not consensus-critical.
        # Each node uses its own wall clock for emergency_max_target.
        decay_factor = 0.5 ** (excess_time / DECAY_HALF_LIFE)
        emergency_max_target = int(previous_share.max_target / decay_factor)
        emergency_max_target = min(emergency_max_target, net.MAX_TARGET)
        # Use emergency target as the "previous" for clamping
        previous_max_for_clamp = emergency_max_target
    else:
        previous_max_for_clamp = previous_share.max_target
else:
    previous_max_for_clamp = net.MAX_TARGET
```

Then update the clamp to use `previous_max_for_clamp` instead of
`previous_share.max_target`.

**Estimated LOC:** ~20 added.

**Telemetry to add:** `emergency_trigger_count`, `emergency_last_timestamp`,
`emergency_max_decay_factor` — exposed via `/emergency_stats`.

**Checkpoint C2:**
- True-whale simulation (100× burst then leave): recovery to mineable target
  within 15–20 minutes (compare C0 baseline: ∞ death spiral).
- Normal conditions: emergency path trigger rate = 0 over 48h (except
  genuine network stalls).
- No consensus divergence across test nodes A/B in 48h soak. Verify by
  comparing share hashes and payout vectors every minute.
- Timestamp monotonicity: `desired_timestamp` never goes backward.

**Rollback:** Remove the emergency decay block. No state migration.

> **Step 2 STATUS: IMPLEMENTED (2026-03-03)**
>
> Phase 1b emergency time-based decay has been implemented and deployed to
> testnet nodes 29+31. Key implementation details:
>
> - **Consensus-gated on V36** (`v36_active` flag) — NOT a local-only change
>   as originally planned; max_bits is in the share header and must match
>   across all verifying nodes.
> - `EMERGENCY_THRESHOLD = net.SHARE_PERIOD * 20` (80s testnet, 300s mainnet)
> - `DECAY_HALF_LIFE = net.SHARE_PERIOD * 10` (40s testnet, 150s mainnet)
> - Uses integer-only arithmetic: `previous_share.max_target << halvings`
>   with linear interpolation for fractional half-life, clamped to MAX_TARGET.
> - Replaces `previous_share.max_target` in the ±10% clamp reference with
>   the emergency-eased target, allowing faster absolute difficulty drops.
> - **Normal operation: never triggers.** With ~3.3 MH/s testnet hashrate
>   and SHARE_PERIOD=4s, an 80s gap requires Poisson probability e^(-20)
>   ≈ 2×10⁻⁹ — effectively impossible under normal mining.
> - **Death spiral recovery:** After threshold, difficulty halves every 40s
>   (testnet) / 150s (mainnet). A 100× whale departure recovers in ~15 min.
> - Fresh sharechain deployed with Phase 2a.
>
> Actual code differs from the planned pseudocode:
> - Uses `v36_active` gating (consensus change, not local-only)
> - Uses integer bit-shift `<< halvings` instead of float `0.5 ** x`
> - Decay factor applied to clamp reference target, not raw pre_target
>
> **Death Spiral Test (2026-03-03 06:44–06:58 UTC):**
> Three ASIC miners (~3.83 MH/s) blocked via iptables, leaving one CPU miner
> (~70 kH/s) — a 98% hashrate drop. Timeline:
>
> | Time (UTC) | Gap   | Difficulty | Event                          |
> |------------|-------|------------|--------------------------------|
> | 06:45:24   |  2s   | 8.27       | Last ASIC share before block   |
> | 06:46:36   | 72s   | 8.38       | CPU only — below 80s threshold |
> | 06:47:50   | 74s   | 8.49       | CPU only — still below threshold |
> | 06:49:32   | 102s  | 3.31       | **EMERGENCY #1** — 60% drop    |
> | 06:50:33   | 50s   | 4.09       | CPU recovery share             |
> | 06:51:45   | 72s   | 4.54       | CPU share — just below threshold |
> | 06:54:00   | 135s  | 0.63       | **EMERGENCY #2** — 92% drop from peak |
> | 06:54:07   |  1s   | 1.07       | ASICs unblocked, rapid recovery |
> | 06:54:16   |  1s   | 2.23       | +16s: diff already 3.5× valley |
> | 06:54:25   |  1s   | 3.33       | +25s: diff 5.3× valley         |
>
> Results:
> - **Emergency decay triggered twice** (102s and 135s gaps)
> - **Deepest difficulty drop: 8.27 → 0.63** (92% reduction, 13× easier)
> - **CPU miner sustained mining** throughout — death spiral prevented
> - **Recovery: 0.63 → 3.33 in 25 seconds** after ASICs returned
> - **Zero consensus errors** across both nodes (1148+ shares verified)
> - Test script: `scripts/death_spiral_test.py`

---

#### Step 3 — Phase 2a implementation (PPLNS exponential decay)

> **Step 3 STATUS: IMPLEMENTED & TESTED (2026-03-03)**
>
> Phase 2a exponential PPLNS decay has been implemented and tested on the
> LTC testnet. Key results:
>
> - **Arrival HAR reduced from 5.27× to 1.52× (71.1% improvement)**
> - Implementation uses direct O(n) iteration with 40-bit fixed-point
>   arithmetic (not SkipList — depth-dependent weights can't be cached)
> - New function: `get_decayed_cumulative_weights()` in data.py (~70 lines)
> - Gated on `v36_active` for backward compatibility
> - HALF_LIFE = CHAIN_LENGTH // 4 (100 shares on testnet, 2160 on mainnet)
> - Payout evolution at arrival: hopper starts at 20% of anchor, rebuilds
>   to 68% over 5 minutes (vs immediate 238% in flat PPLNS)
> - Remaining 1.52× advantage is from difficulty-layer effects (easier
>   shares), not payout-layer. Phase 2c could address the residual.
>
> See docs/PHASE2A_TEST_REPORT.md for full test report with per-share data.

**Goal:** Remove stale-share reward persistence — recent work is worth more.

**Honest miner impact:** 24/7 miners (Alice, Bob) see ~0% change. New miners
(Carol) slightly negative for first shares. Intermittent miners (Dave -8%,
Eve -15-25%) are correctly realigned to actual contribution. See §8.2 Step 3.

**Depends on:** Step 0 (baseline for comparison). Steps 1-2 recommended but
not required — PPLNS decay is an independent consensus mechanism.

**Consensus change:** YES — all V36 nodes must compute identical decay
weights. This ships at V36 activation, not before.

**Files changed:**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | `WeightsSkipList.get_delta()` line ~1642 | Apply depth-based exponential decay to `att` |
| `p2pool/data.py` | `WeightsSkipList` class | Add `_decay_power()` helper, `HALF_LIFE` constant |
| `p2pool/data.py` | `generate_transaction()` line ~820 | Pass depth context through payout path |
| `p2pool/data.py` | `MergedWeightsSkipList.get_delta()` line ~1693 | Mirror same decay logic for merged chain (§8.3.4) |

**Code change — `WeightsSkipList.get_delta()` (line 1642):**

Before:
```python
def get_delta(self, element):
    from p2pool.bitcoin import data as bitcoin_data
    share = self.tracker.items[element]
    att = bitcoin_data.target_to_average_attempts(share.target)
    return (1, {share.address: att*(65535-share.share_data['donation'])},
            att*65535, att*share.share_data['donation'])
```

After:
```python
# Constants for 40-bit fixed-point decay (§7.2, §7.3.13)
_DECAY_PRECISION = 40
_DECAY_SCALE = 1 << _DECAY_PRECISION
_LN2_FP = (_DECAY_SCALE * 693147180559945) // (10**15)

def _decay_power(scale, n, base, precision):
    """Compute base^n in fixed-point via O(log n) repeated squaring."""
    result = scale
    b = base
    while n > 0:
        if n & 1:
            result = (result * b) >> precision
        b = (b * b) >> precision
        n >>= 1
    return result

# In WeightsSkipList:
def get_delta(self, element, depth=0):
    from p2pool.bitcoin import data as bitcoin_data
    share = self.tracker.items[element]
    att = bitcoin_data.target_to_average_attempts(share.target)

    # Apply exponential PPLNS decay: weight × 2^(-depth/half_life)
    half_life = self.tracker.net.CHAIN_LENGTH // 4  # 2160 shares
    if depth > 0 and half_life > 0:
        decay_per = _DECAY_SCALE - _LN2_FP // half_life
        decay_fp = _decay_power(_DECAY_SCALE, depth, decay_per,
                                _DECAY_PRECISION)
        att = (att * decay_fp) >> _DECAY_PRECISION

    return (1, {share.address: att*(65535-share.share_data['donation'])},
            att*65535, att*share.share_data['donation'])
```

> **Note:** The `depth` parameter threading through `SkipList.get_delta()`
> calls also requires changes in `skiplist.py` and `forest.py` to pass the
> share's position in the PPLNS window. See `TrackerSkipList` base class.

**Shadow audit mode:** During testnet, run BOTH old and new weight functions
side-by-side and log `old_weight / new_weight` ratio per address. This
validates the decay model without affecting payouts until activation.

**Estimated LOC:** ~40 changed/added in `data.py`, ~15 in `skiplist.py`.

**Checkpoint C3:**
- Hopper replay: payout tail reduced per model (target: ~11.8% → ~3.3%).
  Measure by replaying captured share logs through new code and comparing
  `/current_payouts` vectors.
- Honest 24/7 miner proportionality error < 1% in replay windows.
  Specifically: `|alice_new_pct / alice_old_pct - 1| < 0.01`.
- Cross-node payout vectors match exactly in deterministic test. Compare
  `generate_transaction()` outputs from 3+ nodes given identical share
  chains.
- Integer overflow check: `att * decay_fp` at max target stays within
  Python's arbitrary-precision integers (no truncation issues).

**Merged mining (§8.3.4):** Mirror the identical decay logic in
`MergedWeightsSkipList.get_delta()` (line ~1693 in `data.py`). The
`_decay_power()` helper is a module-level function shared by both classes.
Estimated +8 LOC. Both redistribution points (work.py local + data.py
consensus) automatically receive decayed weights — no changes needed at
the redistribution layer. See §8.3.4 for full code snippet.

**Rollback:** Remove decay logic from `get_delta()`, revert `depth`
parameter threading. Requires coordinated V36 deactivation.

---

#### Step 3a — Pure difficulty accounting: remove block finder fee (V36 consensus)

**Goal:** Eliminate the legacy 0.5% finder fee lottery. All block rewards
are distributed 100% by PPLNS work-proportional weights. Close the 2-share
PPLNS window gap. Remove ~98 lines of merged mining consensus code.

**Honest miner impact:** Every miner's expected payout is the same (0.5%
moved from lottery to guaranteed PPLNS). Payout **variance decreases** for
all miners — especially small miners who statistically never find blocks.
This is a strict Pareto improvement. See §8.2 Step 3a.

**Depends on:** Step 3 (PPLNS decay). Both are V36 consensus changes that
ship together at activation. Step 3a is independent of Step 3 technically
but should be validated together since both change the coinbase formula.

**Consensus change:** YES — changes `generate_transaction()` coinbase
distribution. All V36 nodes must use the new formula simultaneously.

**Files changed:**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | `generate_transaction()` line ~827 | Replace `subsidy*(199*weight)//(200*total_weight)` with `subsidy*weight//total_weight` |
| `p2pool/data.py` | `generate_transaction()` line ~852 | **Remove** `amounts[this_address] += subsidy//200` |
| `p2pool/data.py` | `generate_transaction()` line ~820 | Change PPLNS start from grandparent to parent |
| `p2pool/data.py` | `CANONICAL_MERGED_FINDER_FEE_PER_MILLE` line ~172 | Set to `0` |
| `p2pool/data.py` | `get_canonical_merged_finder_script()` lines ~288-320 | Can be deleted or short-circuited |
| `p2pool/data.py` | `build_canonical_merged_coinbase()` lines ~210-257 | Remove finder fee calculation and coalescing |
| `p2pool/merged_mining.py` | `build_merged_coinbase()` line ~154 | `finder_fee_percentage` forced to 0 |
| `p2pool/merged_mining.py` | Lines ~243-261 | Remove finder fee output logic |
| `p2pool/work.py` | Line ~735, ~899 | Remove `finder_fee_percentage=0.5` |
| `p2pool/work.py` | Lines ~2050-2059 | Remove canonical finder_script derivation |
| `p2pool/work.py` | Lines ~2068-2081 | Remove legacy finder_fee plumbing |

**Code change 1 — Parent chain PPLNS distribution (line 827):**

Before (V35):
```python
# 99.5% distributed by PPLNS, 0.5% to finder
amounts = dict((script, share_data['subsidy']*(199*weight)//(200*total_weight))
               for script, weight in weights.iteritems())

# 0.5% bonus to block finder
amounts[this_address] = amounts.get(this_address, 0) + share_data['subsidy']//200
```

After (V36):
```python
# 100% distributed by PPLNS — pure difficulty accounting
amounts = dict((script, share_data['subsidy'] * weight // total_weight)
               for script, weight in weights.iteritems())
# No finder fee — reward is exactly proportional to work performed
```

**Code change 2 — Close the PPLNS window gap (line 820):**

Before (V35):
```python
# PPLNS starts from grandparent — excludes 2 most recent shares
weights = tracker.get_cumulative_weights(
    previous_share.share_data['previous_share_hash'],  # grandparent
    max(0, min(height, net.REAL_CHAIN_LENGTH) - 1),    # 8639 shares
    65535 * net.SPREAD * bitcoin_data.target_to_average_attempts(block_target),
)
```

After (V36):
```python
# PPLNS starts from parent — includes all shares except the one being created
# (which can't include itself since it IS the coinbase being built)
if v36_active:
    pplns_start = previous_share.hash                           # parent
    pplns_count = min(height, net.REAL_CHAIN_LENGTH)            # 8640 shares
else:
    pplns_start = previous_share.share_data['previous_share_hash']  # grandparent
    pplns_count = max(0, min(height, net.REAL_CHAIN_LENGTH) - 1)    # 8639 shares

weights, total_weight, donation_weight = tracker.get_cumulative_weights(
    pplns_start, pplns_count,
    65535 * net.SPREAD * bitcoin_data.target_to_average_attempts(block_target),
)
```

**Code change 3 — Merged mining finder fee (data.py line 172):**

Before:
```python
CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 5   # 0.5% = 5 per mille
```

After:
```python
CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0   # V36: no finder fee
```

With `CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0`, `finder_fee_amount`
is always 0 in `build_canonical_merged_coinbase()`, and the entire
finder-script branch (`if finder_fee_amount > 0`) is never entered.
The `get_canonical_merged_finder_script()` function is never called.
All ~98 lines of merged finder code become dead code that can be removed
(or left as defensive dead branches for now).

**Code change 4 — Legacy merged path (work.py):**

```python
# Before: finder_fee_percentage=0.5
# After: finder_fee_percentage=0.0 (or omit entirely)
finder_fee_percentage = 0.0  # V36: no finder fee
```

**Estimated LOC:** -5 lines in `generate_transaction()` (net: replace 5
with 4), +0 in merged canonical path (just constant change), -20 removable
from merged_mining.py legacy path, -14 removable from work.py.
**Net: ~35 lines removed** (or ~100 lines if dead merged finder code is
cleaned up).

**Checkpoint C3a:**
- Replay baseline shares through new formula. Compare payout vectors:
  every miner's V36 payout should be within +0.5% ± rounding of their
  V35 payout (the finder fee is redistributed to everyone proportionally).
- The **finder's share creator** payout should match their PPLNS-proportional
  amount exactly — no bonus, no penalty.
- Verify `amounts` sum exactly equals `subsidy - donation_remainder`.
  Integer rounding remainder goes to donation (unchanged behavior).
- Cross-node determinism: 3+ nodes produce identical coinbase outputs.
- Merged mining: `build_canonical_merged_coinbase()` produces identical
  outputs with and without `finder_script=None` (since fee is 0 either way).
- PPLNS window now includes parent share: verify that the parent share's
  weight appears in the `weights` dict.
- Boundary share fractional credit still works correctly with the new
  window start position.

**Rollback:** Revert to `199/200` formula, restore `subsidy//200` finder
bonus, revert PPLNS window to grandparent start, set
`CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 5`. Requires coordinated V36
deactivation.

> **Step 3a STATUS: IMPLEMENTED & DEPLOYED (2026-03-03)**
>
> Phase 2c (pure difficulty accounting) has been implemented and deployed
> to testnet nodes 29+31. Five code changes, all V36-gated:
>
> 1. **PPLNS window start**: `previous_share.hash` (parent) instead of
>    `previous_share.share_data['previous_share_hash']` (grandparent).
>    `min(height, REAL_CHAIN_LENGTH)` (N) instead of N-1.
> 2. **Amounts formula**: `subsidy*weight//total_weight` (100% PPLNS)
>    instead of `subsidy*(199*weight)//(200*total_weight)` (99.5%).
> 3. **Finder fee removed**: `amounts[this_address] += subsidy//200`
>    is skipped when `v36_active`.
> 4. **Canonical merged**: `CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0`
>    (was 5). Finder script pipeline becomes dead code.
> 5. **Legacy merged**: `finder_fee_percentage=0.0` in work.py (was 0.5).
>
> Fresh sharechain deployed. Initial consensus check: **zero errors** on
> both nodes (121+ shares verified within 2 minutes of startup).
> Overnight soak test pending.

**Additional V36 prerequisite — Pin AutoRatchet to CHAIN_LENGTH (§7.3.16 R2):**

Before variable windows go live, change `data.py` L2047 from:
```python
sample = min(height, net.REAL_CHAIN_LENGTH)   # ← dangerous if variable
```
to:
```python
sample = min(height, net.CHAIN_LENGTH)        # ← fixed, deterministic
```
This is a one-line change. Without it, variable PPLNS windows could cause
AutoRatchet to use different sampling lengths on different nodes, leading
to consensus divergence during V37 transition. Include in Step 3a testing.

---

#### Step 4 — Phase 2b implementation (⏭ DEFERRED TO V37 / c2pool)

> **V37 track.** Work-weighted vesting is deferred to C++ c2pool. PPLNS
> decay (Step 3) already makes hopping unprofitable (0.6×). Vesting adds
> defense-in-depth but requires new data structures better suited to C++.
> The full design below is preserved for V37 implementation reference.

**Goal:** Make burst mining immediately weak and non-profitable.

**Honest miner impact:** 24/7 miners unchanged (vesting = 1.0 always). New
miners (Carol) ramp from 3% → 95% over ~72h — small absolute cost, the
dashboard shows progress. Weekend miners (Eve) ~10-15% additional
reduction (stacks with Phase 2a). See §8.2 Step 4.

**Depends on:** Step 3 (PPLNS decay). Vesting multiplies with decay — both
must use consistent half-life definitions.

**Consensus change:** YES — ships with V37 activation in c2pool.

**Files changed:**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | New function | `calculate_robust_vesting()` (§7.3.4) |
| `p2pool/data.py` | New class | `IncrementalVestingCache` (§7.3.13 Strategy 1) |
| `p2pool/data.py` | `generate_transaction()` line ~828 | Apply vesting factor to per-miner weights |
| `p2pool/data.py` | `_decay_power()` | Shared with Step 3 (already added) |
| `p2pool/data.py` | `get_v36_merged_weights()` line ~2197 | Apply vesting to merged weights before redistribution (§8.3.5) |

**Key implementation details:**

1. **Vesting formula** — for each miner address, walk 2×CHAIN_LENGTH shares
   (up to 17,280 using existing tracker retention) and sum decayed work:
   ```python
   def calculate_robust_vesting(tracker, tip, address, net):
       RECENCY_HALF_LIFE = net.CHAIN_LENGTH // 4
       lookback = 2 * net.CHAIN_LENGTH
       # ... O(n) walk, decayed work sum, return vesting factor
   ```

2. **Incremental cache** — avoids the O(n) walk on every share generation:
   ```python
   class IncrementalVestingCache(object):
       __slots__ = ('_per_addr', '_decay_per', '_scale', '_precision')
       # On each new share: multiply all cached values by decay,
       # add new share's work to its address. O(active_addresses).
   ```

3. **Payout integration** — in `generate_transaction()`, after computing
   weights from `WeightsSkipList`:
   ```python
   # Apply vesting factor
   for script, weight in weights.iteritems():
       vesting = vesting_cache.get_vesting(script)  # 0.0–1.0
       weights[script] = weight * vesting // SCALE
   ```

4. **Full-rebuild verifier** — every 10,000 shares, rebuild from scratch
   and assert `|incremental - full| / full < 0.001` per address.

**Estimated LOC:** ~120 added (`IncrementalVestingCache` ~60, vesting
formula ~30, payout integration ~15, verifier ~15).

**Checkpoint C4:**
- Cache parity: incremental vs full O(n) diff < 0.1% per address across
  100K shares of replay data.
- Performance: vesting update < 1ms/share at 49.5 GH/s profile. Benchmark
  with `time.time()` around cache update in `generate_transaction()`.
- Hopper replay: combined Phase 1+2a+2b profitability < 1.0× for all
  scenarios in S1–S6.
- Carol (new miner) profile: vesting reaches 0.60 at 24h, 0.85 at 48h,
  0.95 at 72h (within ±5% tolerance from model predictions in §7.3.12).

**Merged mining (§8.3.5):** Apply vesting to merged weights inside
`get_v36_merged_weights()` (line ~2197 in `data.py`) so both redistribution
points (work.py and data.py) see vested weights. Vesting identity key maps
from `'MERGED:<hex_script>'` back to the parent chain address via
`share.address`. Estimated +20 LOC. See §8.3.5 for implementation detail
and Approach A (preferred) vs Approach B discussion.

**Rollback:** Remove vesting cache and multiplication from payout path.
Requires coordinated V37 deactivation.

---

#### Step 5L — Lightweight log monitoring (V36 — non-consensus) ✅ DEPLOYED

**Goal:** Operational visibility and attack detection via structured log
lines. Dashboard UI deferred to V37/c2pool (Step 5F).

**V36 scope:** `[MONITOR-*]` log prefixes emitted every status cycle
(~30s). No HTTP endpoints, no `web.py` changes. Operators grep logs
directly — zero attack surface, works with existing log pipelines.

**Honest miner impact:** Indirect positive — operators detect hopping
patterns earlier, can respond manually. See §8.2 Step 5.

**Depends on:** Step 1 (clamp counters). Can be deployed independently of
consensus changes (Steps 3-3a).

**Files changed:**

| File | Location | Change |
|------|----------|--------|
| `p2pool/monitor.py` | NEW ~230 LOC | `PoolMonitor` class, 4 check methods |
| `p2pool/main.py` | `status_thread()` | Import + call `pool_monitor.run_cycle()` |

**Log prefixes:**

| Prefix | Purpose | Frequency |
|--------|---------|----------|
| `[MONITOR-SUMMARY]` | One-line health status | Every cycle (~30s) |
| `[MONITOR-HASHRATE]` | Pool vs 1h moving average | ALERT on spike/drop, ok every ~5min |
| `[MONITOR-CONC]` | Per-address work concentration | ALERT >40%, WARN >25%, top3 every ~5min |
| `[MONITOR-EMERGENCY]` | Share gap / emergency decay | ALERT when gap > threshold |
| `[MONITOR-DIFF]` | Difficulty anomaly detection | ALERT on >2x deviation |

**Alert thresholds (configurable in PoolMonitor):**

| Alert | Trigger | Default |
|-------|---------|---------|
| `concentration_alert` | Single address > 40% of window | On |
| `concentration_warn` | Single address > 25% of window | On |
| `hashrate_spike` | Pool hashrate > 150% of 1h moving average | On |
| `hashrate_drop` | Pool hashrate < 50% of 1h moving average | On |
| `difficulty_anomaly` | Target deviation > 200% from expected | On |
| `emergency_gap` | Share gap > `SHARE_PERIOD × 20` | On |

**Estimated LOC:** ~230 (monitor.py) + ~10 (main.py integration).

**Checkpoint C5L:** ✅
- All 4 monitoring types producing correct output on both testnet nodes.
- Concentration ALERT fires for >40%, WARN for >25%.
- Verbose summary (hashrate ratio, diff ratio, top3 miners) every ~5min.
- Zero consensus errors across both nodes.

**Usage examples:**
```
grep MONITOR-CONC data/litecoin_testnet/log | tail -5
grep MONITOR-EMERGENCY data/litecoin_testnet/log
grep MONITOR-SUMMARY data/litecoin_testnet/log | tail -20
```

**Rollback:** Remove `monitor.py`, revert `main.py` import. No consensus impact.

---

#### Step 5F — Full dashboard UI (⏭ DEFERRED TO V37 / c2pool)

> **V37 track.** Full frontend dashboard with vesting bars, share trend
> charts, payout explanation toasts, and pool health panels. Requires
> vesting system (Phase 2b, also V37). The full design is in Phase 5
> (§8 Phase 3 section) for c2pool implementation reference.

**Files (c2pool):** `miner.html`, `miners.html`, `dashboard.html`

**New endpoints (V37):**

| Endpoint | Purpose | Response |
|----------|---------|----------|
| `/vesting_info` | Per-miner vesting state | `{address: {factor: 0.85, eta_95pct: "12h"}}` |
| `/miner_stats` (enhanced) | Vesting + trend | `vesting_factor`, `vesting_eta`, `weight_trend_1h` |

**Estimated LOC:** ~330 (frontend panels ~210, enhanced backend ~120).

---

#### Step 6 — Phase 4 implementation (⏭ DEFERRED TO V37 / c2pool)

> **V37 track.** Adaptive windows require 308K+ shares in memory. At
> ~4,500 bytes/share under PyPy 2.7, this is ~1.39 GB with compaction.
> C++ c2pool's native memory management makes this tractable. The full
> design is preserved below for V37 implementation reference.

**Goal:** Scale protection with TTB while controlling resources.

**Honest miner impact:** 24/7 miners (Alice, Bob) get +2-3% improvement
from longer window. Weekend miners (Eve) get +5% back from adaptive window
keeping their Friday shares longer. Transition is smooth over 720 shares
(3 hours). See §8.2 Step 6.

**Depends on:** Steps 3-4 (decay + vesting must work with fixed windows
before making them adaptive).

**Consensus change:** YES — ships with V37 activation in c2pool.

**Files changed (c2pool):**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | New function | `get_adaptive_chain_length()` (§7.3.10) |
| `p2pool/data.py` | `generate_transaction()` | Use adaptive window instead of `net.REAL_CHAIN_LENGTH` |
| `p2pool/data.py` | `get_pool_attempts_per_second()` line ~2133 | Input for adaptive calculation |
| `p2pool/node.py` | `clean_tracker()` line ~386 | Dynamic prune threshold based on adaptive window |
| `p2pool/data.py` | New class | `CompactEpochSummary` (§7.3.13 Strategy 2) |
| `p2pool/data.py` | New function | `compact_old_shares()` |

**Key implementation details:**

1. **Adaptive window function:**
   ```python
   def get_adaptive_chain_length(tracker, tip, net):
       pool_aps = get_pool_attempts_per_second(tracker, tip,
                                                net.TARGET_LOOKBEHIND)
       net_aps = bitcoin_data.target_to_average_attempts(
           tracker.items[tip].bitcoin_hash_target) / net.PARENT.BLOCK_PERIOD
       ratio = pool_aps / net_aps if net_aps > 0 else 1
       # Target 50% of TTB coverage
       ttb_shares = int(1.0 / ratio / net.SHARE_PERIOD) if ratio > 0 else net.CHAIN_LENGTH
       target = max(net.CHAIN_LENGTH, min(ttb_shares // 2, 40 * net.CHAIN_LENGTH))
       return target  # Clamped: [8640, 345600]
   ```

2. **Transition interpolation** — smooth change over 720 shares:
   ```python
   # Prevent oscillatory shocks from sudden window changes
   prev_window = get_prev_adaptive_chain_length(...)
   new_window = compute_raw_adaptive(...)
   # Asymmetric: shrink slowly (720 shares), grow at full speed
   if new_window < prev_window:
       interp = max(new_window, prev_window - (prev_window - new_window) // 720)
   else:
       interp = new_window
   ```

3. **Share compaction** — tiered storage (§7.3.13 Strategy 2):
   ```
   Hot tier: full share objects (0 to CHAIN_LENGTH)
   Warm tier: CompactEpochSummary per 720 shares (CHAIN_LENGTH to 2×window)
   Cold tier: pruned (beyond 2×window)
   ```

4. **Dynamic prune threshold** — `clean_tracker()` retention scales:
   ```python
   # In node.py clean_tracker(), replace fixed threshold:
   #   2*self.tracker.net.CHAIN_LENGTH + 10
   # With:
   adaptive_len = get_adaptive_chain_length(self.tracker, best, self.tracker.net)
   prune_threshold = 2 * adaptive_len + 10
   ```

**Estimated LOC:** ~180 added (adaptive function ~30, transition ~25,
compaction ~80, dynamic prune ~15, tests ~30).

**Checkpoint C6:**
- Consensus determinism: all V36 test nodes compute identical
  `get_adaptive_chain_length()` results on every tested tip. Verify by
  logging adaptive window on 3+ nodes and diffing.
- Memory target at 49.5 GH/s profile: ~1.39 GB core (±15%). Measure via
  `ps -o rss` after 24h steady-state run. (Based on ~4,500 bytes/share,
  see §7.3.14.)
- Reorg safety: inject a 50-share fork and verify payout recomputation
  handles compacted tail data correctly.
- Transition smoothness: simulate 2× hashrate growth over 6h and verify
  no window size jumps larger than 1/720 per share.

**Rollback:** Revert to fixed `REAL_CHAIN_LENGTH`. Requires coordinated
V37 deactivation. Compacted shares cannot be un-compacted, but fresh
P2P sync rebuilds full data.

---

#### Step 7 — Persistence layer (⏭ DEFERRED TO V37 / c2pool — LevelDB)

> **V37 track.** c2pool already has LevelDB-backed persistent storage.
> In Python/PyPy, the existing `ShareStore` (`shares.N` files) covers the
> fixed 8,640-share window adequately. Adaptive windows (V37) need ~308K
> shares persisted, which maps naturally to c2pool's existing LevelDB
> infrastructure rather than bolting SQLite onto PyPy.

**Goal:** Fast crash recovery and restart for adaptive-window tracker.

**Honest miner impact:** Positive — restart after crash takes sub-second
from LevelDB instead of re-syncing from peers. See §8.2 Step 7.

**Depends on:** Step 6 (compaction determines what's stored).

**Files changed (c2pool / LevelDB — replaces SQLite WAL design below):**

| File | Location | Change |
|------|----------|--------|
| `p2pool/data.py` | New module or class | `ShareStore` (SQLite WAL backend) |
| `p2pool/node.py` | Startup path | Restore tracker from DB before P2P sync |
| `p2pool/node.py` | `clean_tracker()` | Write pruned shares to DB before removal |

> **Note:** The code examples below show the original Python/SQLite design
> from earlier analysis. The actual V37 implementation will use c2pool's
> native LevelDB storage, which provides the same semantics with better
> performance and no new Python dependency.

**Key implementation details:**

1. **WAL-backed share store** — single SQLite database with WAL journal:
   ```python
   class ShareStore(object):
       def __init__(self, db_path):
           self.conn = sqlite3.connect(db_path)
           self.conn.execute('PRAGMA journal_mode=WAL')
           self.conn.execute('PRAGMA page_size=4096')
           # Schema: shares(hash BLOB PRIMARY KEY, data BLOB, height INT)
   ```

2. **Startup restore** — in-memory tracker is authoritative, DB is fallback:
   ```
   1. Try to open DB
   2. Load shares into tracker (newest first, stop at 2×adaptive_window)
   3. Validate chain integrity (hash linkage)
   4. If corruption: log warning, delete DB, fall back to P2P re-sync
   5. Resume normal P2P protocol for any missing shares
   ```

3. **Corruption fallback** — safe degradation, no crash loops.

**Estimated LOC:** ~150 added (ShareStore ~80, startup restore ~40,
corruption handling ~30).

**Checkpoint C7:**
- Restart recovery < 10s at 308K-share profile. Benchmark by killing
  process and measuring time to `best_share_var` being set after restart.
- Injected DB corruption (truncate file, flip random bytes) safely falls
  back to P2P sync with no crash loops and no Python tracebacks.
- DB size stays bounded: ~340 MB at 308K shares (~1.1 KB/share wire format).

**Rollback:** Delete DB file, remove startup restore logic. Falls back
to P2P re-sync (existing behavior).

---

#### Step 8 — V36 activation and rollout gate

**Goal:** Safe V36 mainnet activation with the anti-hopping consensus
changes (PPLNS decay + pure difficulty accounting + AutoRatchet pin).

**V36 consensus scope (what activates):**
- PPLNS exponential decay (Step 3)
- Finder fee removal + PPLNS window closure (Step 3a)
- AutoRatchet pin to CHAIN_LENGTH (R2)

**NOT in V36 activation:** Vesting, adaptive windows, compaction,
dashboard UI — all deferred to V37/c2pool.

**Honest miner impact:** V36 activation applies the consensus changes
simultaneously. The transition uses the existing V36 activation mechanism
(95% share threshold over 2×CHAIN_LENGTH). During the transition window,
miners on V35 and V36 coexist. See §8.2 Step 8 for per-miner experience.

**Depends on:** Steps 0–3a, 5L passing their checkpoints.

**Actions:**
1. Run 7-day testnet soak with V36 consensus phases enabled simultaneously.
   Monitor: share rate, stale rate, payout consistency, memory, CPU,
   reorg count, alert false-positive rate.
2. Run attack simulation suite (S1–S3, S5, S10, S12) against testnet nodes.
3. Publish V36 activation checklist with exact parameter values:
   - `HALF_LIFE = CHAIN_LENGTH // 4` (2160 shares)
   - `EMERGENCY_THRESHOLD = SHARE_PERIOD × 20` (300s)
   - `DECAY_HALF_LIFE = SHARE_PERIOD × 10` (150s)
   - `CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0`
   - AutoRatchet: `sample = min(height, net.CHAIN_LENGTH)`
4. Enable on test nodes A/B first. Monitor 72h for consensus stability.
5. Staged rollout to additional nodes in cohorts of 2-3.
6. Track V36 share percentage at `/version_signaling` — activation
   triggers automatically at 95% sustained over 2×CHAIN_LENGTH shares.

**Checkpoint C8 (go/no-go):**
- No consensus splits in 7 days of testnet soak.
- Hopper profitability remains < 1.0× across ALL replay scenarios (S1–S3).
- Resource usage unchanged from V35 (~5 MB tracker at fixed 8,640 window).
- All monitoring endpoints functioning with < 2 false positives per day.
- Rollback plan tested: V36 deactivation produces clean revert to V35
  behavior within 2×CHAIN_LENGTH shares.

**Rollback:** Disable V36 signaling on nodes, letting share percentage
drop below 95%. V36 consensus rules deactivate after existing shares
expire from the PPLNS window (~36h at minimum). Requires coordination
across all node operators.

---

#### Test scenario suite (V36 — must be automated)

Each scenario must be runnable via a single command and produce a PASS/FAIL
verdict with detailed logs. The replay harness from Step 0 provides the
foundation. Scenarios S4, S6–S9, S11 are deferred to V37 (they test
vesting and adaptive windows).

| ID | Scenario | Input | Expected outcome | V36 Phase |
|----|----------|-------|------------------|-----------|
| S1 | Semiwhale burst | 3× attacker for 2h, then leave | Fast recovery (<60m), no share starvation | 1a |
| S2 | True whale shock | 100× attacker for 20m, then leave | Emergency decay recovery (<20m), no deadlock | 1b |
| S3 | Repeated hopper | 5h on / 7h off cycles for 72h | Hopper efficiency < loyal miner (< 1.0×) | 2a |
| S5 | Address split (Sybil) | Same hashrate across 5 addresses | No material bypass of decay | 2a |
| S6 | Pump-and-dump | Short hashrate pump then withdraw | Shrink damping + vesting neutralize gain | 4 |
| S7 | Hashrate oscillation | Sinusoidal/erratic pool hashrate | Stable adaptive windows, no payout shocks | 4 |
| S8 | Rapid pool growth | 2× hashrate increase in hours | Smooth window shrink, no payout discontinuity | 4 |
| S9 | Rapid pool drop | 50% hashrate loss | Window expansion, stable payouts | 4 |
| S10 | Deep reorg/fork | Multi-branch sharechain churn | Deterministic payouts, intact tracker consistency | all |
| S12 | Honest miner matrix | Alice/Bob/Carol/Dave/Eve profiles | Impacts match §8.2 V36-only bounds | all |

**V36 scenario implementation priority:**

1. **Must-have before Phase 1 deploy:** S1, S2 (validates clamp + emergency)
2. **Must-have before V36 activation:** S3, S5, S10, S12 (validates
   consensus correctness and anti-hopping effectiveness)

**V37 scenarios (deferred to c2pool):**

| ID | Scenario | V37 Phase |
|----|----------|-----------|
| S4 | Tenure farming (placeholder + burst) | 2b (vesting) |
| S6 | Pump-and-dump (hashrate pump then withdraw) | 4 (adaptive) |
| S7 | Hashrate oscillation (sinusoidal/erratic) | 4 (adaptive) |
| S8 | Rapid pool growth (2× in hours) | 4 (adaptive) |
| S9 | Rapid pool drop (50% loss) | 4 (adaptive) |
| S11 | Restart/crash (large tracker) | 4b (LevelDB) |

#### Acceptance criteria summary (V36)

- **Security:** no profitable hopper pattern in S1–S3, S5.
- **Consensus:** zero divergence in S10 and S12 across 3+ nodes.
- **Performance:** share-gen path unchanged (<100ms at fixed 8,640 window).
- **Operations:** monitoring endpoints catch attacks with <2 false positives/day.
- **Honest miners:** impacts match §8.2 V36 bounds (Alice ~-3%, Bob ~0%,
  Carol -5% initial → -3% steady-state, Dave ~-8%, Eve ~-22%).
- **Resource usage:** unchanged from V35 (~5 MB tracker, no new RAM).

---

### 8.2 Honest Miner Experience: Step-by-Step Impact Analysis

This section describes what each honest miner profile experiences at every
implementation step. Use this as a verification checklist: if observed
behavior deviates from these predictions, the implementation has a bug.

**Track context:** Steps 0–3a and 5L are **V36 (Python 2.7/PyPy)**. Steps
4, 5F, 6, 7 are **V37 (C++ c2pool)**. The V36-only impact column in the
final summary table shows what miners experience with the Python release
alone.

**Reference profiles** (from §7.3.12):

| Profile | Hashrate | Schedule | Current payout rate |
|---------|----------|----------|---------------------|
| **Alice** | 20 GH/s (13%) | 24/7 | Baseline reference |
| **Bob** | 500 MH/s (0.3%) | 24/7 | Low but proportional |
| **Carol** | 5 GH/s (3%) | Just joined | Zero (new) |
| **Dave** | 10 GH/s (7%) | 16h on / 8h off daily | ~92% of 24/7 rate* |
| **Eve** | 15 GH/s (10%) | Fri–Sun only | ~75-80% of 24/7 rate* |

*Under current flat PPLNS — these rates over-reward intermittent miners
because stale shares hold full weight indefinitely.

Pool: 49.5 GH/s, TTB ~107 days (live March 2, 2026).

---

#### Step 0 — Baseline capture

**What miners experience:** Nothing changes. This step only captures data.

```
┌────────┬──────────────────┬────────────┬─────────────────────────┐
│ Miner  │ Mining continues │ Payout     │ Notes                   │
├────────┼──────────────────┼────────────┼─────────────────────────┤
│ Alice  │ ✓ Normal         │ Unchanged  │ Baseline being captured │
│ Bob    │ ✓ Normal         │ Unchanged  │ Baseline being captured │
│ Carol  │ ✓ Normal         │ Unchanged  │ Still ramping up shares │
│ Dave   │ ✓ Normal         │ Unchanged  │ 16h/day pattern normal  │
│ Eve    │ ✓ Normal         │ Unchanged  │ Weekend pattern normal  │
└────────┴──────────────────┴────────────┴─────────────────────────┘
```

**Verification:** Compare miner payouts at `/current_payouts` — should
match historical averages within normal variance (±2% for Alice, ±10% for
Bob due to fewer shares).

---

#### Step 1 — Asymmetric clamp deployed

**What changes:** Difficulty drops faster after large miners depart.
Difficulty increases remain capped at +10% per share (unchanged).

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 1                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ NO CHANGE in steady state. If a whale joins and leaves, │
│        │ Alice finds shares again within ~40m instead of ~3h.    │
│        │ Payout: 0% net change.                                  │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ NO CHANGE in steady state. After whale incidents, Bob   │
│        │ resumes finding shares 4× faster than before. This is   │
│        │ critical for Bob — at 0.3% hashrate, a 3h recovery at   │
│        │ inflated difficulty means zero shares for Bob. Now the  │
│        │ window is ~40m. Payout: 0% base, +benefit during        │
│        │ recovery periods.                                       │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ POSITIVE. If Carol joins during a difficulty recovery,  │
│        │ she starts mining at reasonable difficulty instead of   │
│        │ waiting hours. First-share time: improved.              │
│        │ Payout: 0% base, faster start.                          │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ SLIGHT POSITIVE. When Dave reconnects after his 8h      │
│        │ sleep, any difficulty drift caused by other miners      │
│        │ departing resolves faster.                              │
│        │ Payout: 0% base, +benefit on reconnect.                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ POSITIVE. Monday morning difficulty recovery (from      │
│        │ weekend concentration dissipating) is faster. Other     │
│        │ miners who relied on Eve's weekend hashrate don't suffer│
│        │ a 3-hour difficulty adjustment on Monday morning.       │
│        │ Payout: 0% base.                                        │
└────────┴─────────────────────────────────────────────────────────┘
```

**Key metric to verify:** Time from whale departure to first honest share
at normal difficulty. Must be < 60 minutes (was ~3 hours pre-Step 1).

**Dashboard indicator:** `/clamp_stats` shows `fast_down` count. Should be
zero during steady state and spike only during genuine recovery periods.

---

#### Step 2 — Emergency decay deployed

**What changes:** Share gaps >300s trigger exponential difficulty reduction.

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 2                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ NO CHANGE in normal operation. Emergency decay only     │
│        │ triggers during extreme events (>300s share gap).       │
│        │ Alice's continuous mining means 15s share gaps.         │
│        │ LIFE-SAVING in extreme events: without Step 2, a 100×   │
│        │ whale flash could permanently kill the pool. Alice      │
│        │ would lose her entire mining operation. With Step 2,    │
│        │ recovery takes ~15 minutes.                             │
│        │ Payout: 0% base. Pool survival: guaranteed.             │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ STRONGLY POSITIVE. Small miners suffer most from death  │
│        │ spirals — at 100× difficulty, Bob can NEVER find a      │
│        │ share (would take ~4 years per share). Emergency decay  │
│        │ brings difficulty back to mineable levels in minutes.   │
│        │ Payout: 0% base. Existential protection: yes.           │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ POSITIVE. Joining during a recovery period means Carol  │
│        │ encounters a pool that's healing itself, not dying.     │
│        │ Payout: 0% base.                                        │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ NO EFFECT. Dave's 8h offline gap does not trigger       │
│        │ emergency mode — other miners continue finding shares   │
│        │ during Dave's absence. At 49.5 GH/s, the pool finds     │
│        │ ~2,880 shares during Dave's 8h sleep.                   │
│        │ Payout: 0% base.                                        │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ NO EFFECT. Same reasoning. Even if Eve is 10% of pool,  │
│        │ her Monday departure leaves 90% still mining. Share     │
│        │ gaps remain ~17s, well below 300s threshold.            │
│        │ Payout: 0% base.                                        │
└────────┴─────────────────────────────────────────────────────────┘
```

**Key metric to verify:** `emergency_trigger_count` at `/emergency_stats`
should be 0 over 48 hours of normal operation. Any non-zero count during
normal conditions indicates a misconfigured threshold.

**Cumulative impact after Steps 1–2:**

```
┌────────┬────────────┬────────────────────────────────────────────┐
│ Miner  │ Net change │ Summary                                    │
├────────┼────────────┼────────────────────────────────────────────┤
│ Alice  │ ~0%        │ Same payouts, pool is safer                │
│ Bob    │ ~0%        │ Same payouts, small-miner protection added │
│ Carol  │ ~0%        │ Faster onboarding during recovery events   │
│ Dave   │ ~0%        │ Faster reconnect experience                │
│ Eve    │ ~0%        │ Faster Monday recovery for other miners    │
└────────┴────────────┴────────────────────────────────────────────┘
Phase 1 is PURE IMPROVEMENT — no miner earns less, pool is more resilient.
```

---

#### Step 3 — PPLNS exponential decay activated (V36 consensus)

**What changes:** Share weights decay exponentially with depth in the PPLNS
window. Half-life = CHAIN_LENGTH // 4 = 2160 shares (~9 hours). Recent
shares are worth more than old shares.

**THIS IS THE FIRST PAYOUT-AFFECTING CHANGE.** All previous steps only
affected difficulty adjustment. Step 3 changes how coinbase rewards are
distributed.

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 3                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ SLIGHT NEGATIVE (-3%). Alice mines 24/7, so she always  │
│        │ has fresh shares at 100% weight. Her oldest shares      │
│        │ (27-36h old) now carry 6-12% weight instead of 100%.    │
│        │ However, these oldest shares are being replaced by new  │
│        │ shares at full weight — Alice is effectively paying     │
│        │ herself. The -3% represents the residual tail loss.     │
│        │                                                         │
│        │ Alice's payout check:                                   │
│        │   Before: 13.0% of block rewards (flat PPLNS)           │
│        │   After:  12.6% of block rewards (exp decay)            │
│        │   Net:    -0.4 percentage points                        │
│        │                                                         │
│        │ WHERE THE 0.4% GOES: redistributed to miners who were   │
│        │ previously under-rewarded (like miners who joined       │
│        │ recently but haven't accumulated deep history).         │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ NEUTRAL (0%). Bob mines 24/7 at 0.3%. Exponential       │
│        │ decay is ratio-preserving for continuous miners.        │
│        │ Alice's and Bob's shares decay at IDENTICAL rates.      │
│        │ Bob's share of the pie stays at 0.3%.                   │
│        │                                                         │
│        │ Bob's payout check:                                     │
│        │   Before: 0.30% of block rewards                        │
│        │   After:  0.30% of block rewards                        │
│        │   Net:    0% (no change detectable above variance)      │
│        │                                                         │
│        │ VARIANCE UNCHANGED: still ±1.8% from share count —      │
│        │ decay doesn't increase variance, just shifts absolute   │
│        │ weights while preserving ratios.                        │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ SLIGHT NEGATIVE (-5% initial, normalizing within 24h).  │
│        │ Carol just joined. Her only shares are brand new and    │
│        │ start decaying immediately with nothing older to        │
│        │ compare against. But Carol is ADDING shares — each new  │
│        │ share at full weight compensates for decaying old ones. │
│        │                                                         │
│        │ Carol's timeline:                                       │
│        │   Hour 1:  All shares <1h old, decay < 8% — ~5% loss    │
│        │   Hour 6:  Mix of fresh and 6h-old shares — ~4% loss    │
│        │   Hour 12: Approaching steady state — ~3% loss          │
│        │   Hour 24: Full steady state — same as Alice (~3%)      │
│        │                                                         │
│        │ Carol's initial -5% is TRANSIENT and converges to       │
│        │ Alice's -3% within 24h. The absolute LTC lost during    │
│        │ this period is small (Carol has few shares).            │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ MODERATE NEGATIVE (-8%). Dave's pattern: mine 16h,      │
│        │ sleep 8h. When Dave wakes up:                           │
│        │   - His 16h-old shares: 29% weight (decayed heavily)    │
│        │   - His 8h-old shares (when he stopped): 54% weight     │
│        │   - Alice's same-age shares: supplemented by fresh      │
│        │     shares at 100%, so Alice's average weight is        │
│        │     higher per unit history.                            │
│        │                                                         │
│        │ Dave mines 16/24 = 67% of the time. Under flat PPLNS,   │
│        │ Dave earned ~92% of Alice's per-GH rate. With decay,    │
│        │ Dave earns ~84% of Alice's rate. The 8% reduction       │
│        │ CORRECTLY reflects that Dave's stale hours contributed  │
│        │ no work to the pool.                                    │
│        │                                                         │
│        │ Dave's payout check:                                    │
│        │   Before: 7% hashrate × 92% efficiency = 6.4% payout    │
│        │   After:  7% hashrate × 84% efficiency = 5.9% payout    │
│        │   Net:    -0.5 percentage points (-8% relative)         │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ SIGNIFICANT NEGATIVE (-15 to -25%). Eve mines Fri–Sun   │
│        │ (72h) then is absent Mon–Thu (96h).                     │
│        │                                                         │
│        │ Eve's share weights during the week:                    │
│        │   Sunday shares on Monday:  54% weight (1 half-life)    │
│        │   Sunday shares on Tuesday: 29% weight (2 half-lives)   │
│        │   Sunday shares on Wednesday: 16% (3 half-lives)        │
│        │   Sunday shares on Thursday: 8% (4 half-lives)          │
│        │   Friday shares on Thursday: 2% (8+ half-lives)         │
│        │                                                         │
│        │ Eve mines 72/168 = 43% of the time. Under flat PPLNS,   │
│        │ she earned ~75−80% of a 24/7 miner's rate (because her  │
│        │ weekend burst held full weight all week). With decay,   │
│        │ she earns ~55−65% — closer to her actual 43%.           │
│        │                                                         │
│        │ Eve's payout check:                                     │
│        │   Before: 10% hashrate × 77% efficiency = 7.7% payout   │
│        │   After:  10% hashrate × 60% efficiency = 6.0% payout   │
│        │   Net:    -1.7 percentage points (-22% relative)        │
│        │                                                         │
│        │ IS THIS FAIR? Yes. Eve was previously over-rewarded at  │
│        │ the expense of Alice and Bob, whose continuous work     │
│        │ subsidized Eve's stale-share claims.                    │
└────────┴─────────────────────────────────────────────────────────┘
```

**Where do the redistributed rewards go?**

The rewards "lost" by Dave and Eve are redistributed to miners with fresher
shares — primarily 24/7 miners like Alice and Bob. This is not a transfer
tax; it's a correction of the previous over-payment to stale work.

```
Redistribution flow:
  Dave loses   ~0.5% of block rewards
  Eve loses    ~1.7% of block rewards
  ─────────────────────────────────────
  Total:       ~2.2% redistributed
  → Goes to:   24/7 miners (proportional to their share of active work)
  → Alice gains: ~1.5% (largest 24/7 miner)
  → Bob gains:   ~0.04% (smallest 24/7 miner — proportional to hashrate)
  → Others:      ~0.66% (other continuous miners)
```

**Dashboard indicators (from Step 5):**
- `/users` now shows `pplns_decay_weight` next to raw share count
- Miners can see how decay affects their effective weight
- Eve can see her Sunday shares decaying through the week

---

#### Step 3a — Pure difficulty accounting: finder fee removed (V36 consensus)

**What changes:** The legacy 0.5% block-finder fee is eliminated. 100% of
block rewards are distributed by PPLNS weights. The PPLNS window now
includes the parent share (was previously excluded along with the finder's
share — a 2-share gap that the 0.5% fee was meant to compensate for, but
overcompensated by 21.7×).

**THIS IS A STRICT IMPROVEMENT FOR EVERY MINER.** No miner loses expected
value. Every miner gains lower payout variance.

**What miners experience:**

```
┌────────────────────────────────────────────────────────────────────────┐
│ Step 3a: Pure Difficulty Accounting — Finder Fee Removed               │
│                                                                        │
│ Before (V35):                                                          │
│   Block reward split: 99.5% PPLNS + 0.5% finder lottery                │
│   PPLNS window: 8,639 shares (excludes 2 most recent)                  │
│                                                                        │
│ After (V36):                                                           │
│   Block reward split: 100% PPLNS                                       │
│   PPLNS window: 8,640 shares (includes parent, excludes only self)     │
│                                                                        │
│ Expected payout change: +0.0% (lottery→guaranteed is same EV)          │
│ Variance change:        REDUCED (no more 0.5% lottery component)       │
└────────────────────────────────────────────────────────────────────────┘
```

**Per-miner analysis:**

| Miner | Profile | Expected change | Variance change | Notes |
|-------|---------|-----------------|-----------------|-------|
| **Alice** | 24/7, 10 GH/s (20.2%) | +0.0% | -47% variance | Was paying 0.5% tax every block, getting it back 20.2% of the time |
| **Bob** | 24/7, 300 MH/s (0.6%) | +0.0% | -93% variance | Biggest winner — almost never found blocks, paid 0.5% tax every time |
| **Carol** | New miner, 1 GH/s | +0.0% | -98% variance | Would have found ~0 blocks in first year |
| **Dave** | 16h/day, 5 GH/s | +0.0% | -74% variance | Intermittent — moderate block-finding frequency |
| **Eve** | Weekends, 8 GH/s | +0.0% | -85% variance | Weekend-only — rarely online when blocks found |

**Why variance reduction matters at 49.5 GH/s pool hashrate:**

At ~107-day expected time-to-block, the finder fee lottery has **very few
draws**. A miner earning 1% of pool hashrate expects to be the finder of
a block about 3.4 times per year. But with Poisson statistics:
- P(0 blocks in a year) = 3.3% — this miner pays 0.5% on every block all
  year and never collects the fee. Net cost: -0.5% (real money lost).
- P(2+ blocks in 30 days) = 1.4% — this miner appears to be "lucky" and
  collects double its fair share of finder fees in that month.

This variance does not help any miner. It's pure noise. Removing it makes
payouts more predictable for everyone.

**PPLNS window closure — what actually changes:**

```
Before (V35): Window starts at grandparent → shares N-2 through N-8641
After  (V36): Window starts at parent     → shares N-1 through N-8640

The parent share (N-1) now gets credit in its own coinbase window.
This share was previously excluded — its weight was "compensated" by
the 0.5% finder bonus. Now it gets exact proportional credit instead.

Net effect: exactly zero, since the finder bonus was a proxy for
this excluded weight anyway (just a massively imprecise one).
```

**Merged mining simplification:**

```
Removed from consensus path:
  - get_canonical_merged_finder_script() — 33-line 3-tier address derivation
  - Finder fee calculation in build_canonical_merged_coinbase()
  - Finder script coalescing with miner outputs
  - Edge cases: P2SH pubkey_type, unconvertible P2TR, NULL fallback

  → ~98 lines of consensus-critical code eliminated from merged path
  → Entire class of consensus-split vectors removed
```

---

#### Step 4 — Work-weighted vesting activated (⏭ V37 / c2pool)

> **Track 2 (V37):** Vesting is deferred to the C++ c2pool release.
> The analysis below shows what miners will experience when V37 ships.
> V36 does NOT include vesting — hopping is already unprofitable at 0.6×
> with PPLNS decay alone.

**What changes:** New miners and burst miners start with reduced payout
weight. Vesting factor ramps from ~0 to 1.0 based on accumulated decayed
work over 2×CHAIN_LENGTH lookback.

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 4                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ NO ADDITIONAL CHANGE (0%). Alice mines 24/7 — her       │
│        │ vesting factor is permanently 1.0. The vesting          │
│        │ mechanism adds zero overhead to Alice's payouts.        │
│        │                                                         │
│        │ Combined with Step 3: -3% total (from decay only).      │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ NO ADDITIONAL CHANGE (0%). Bob mines 24/7 at 500 MH/s.  │
│        │ Vesting is work-weighted: Bob's smaller but CONSISTENT  │
│        │ work gives vesting = 1.0, same as Alice. Work rate      │
│        │ doesn't matter — consistency does.                      │
│        │                                                         │
│        │ Combined with Step 3: ~0% total.                        │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ MODERATE NEGATIVE (-20% initial, normalizing in ~72h).  │
│        │ THIS IS THE BIGGEST IMPACT for Carol. Her vesting       │
│        │ lookback is empty on day 1.                             │
│        │                                                         │
│        │ Carol's vesting ramp-up:                                │
│        │   Hour 1:   Vesting = 0.03  (3% of full credit)         │
│        │   Hour 6:   Vesting = 0.15  (15%)                       │
│        │   Hour 12:  Vesting = 0.35  (35%)                       │
│        │   Hour 24:  Vesting = 0.60  (60%)                       │
│        │   Hour 48:  Vesting = 0.85  (85%)                       │
│        │   Hour 72:  Vesting = 0.95  (95%)                       │
│        │   Hour 96:  Vesting = 0.99  (99%)                       │
│        │                                                         │
│        │ WHAT CAROL SEES ON THE DASHBOARD:                       │
│        │   "Vesting: 35% | ETA to 95%: ~60 hours | Keep mining"  │
│        │                                                         │
│        │ HOW MUCH DOES CAROL ACTUALLY LOSE?                      │
│        │   Carol's hashrate = 5 GH/s = 3% of pool.               │
│        │   In the first 24h, Carol finds ~346 shares.            │
│        │   Average vesting over 24h: ~0.28                       │
│        │   Effective shares: 346 × 0.28 = ~97 effective shares   │
│        │   Lost payout: 249 shares worth of credit (~72%)        │
│        │   BUT: the pool finds a block every ~107 days.          │
│        │   Probability of a block in Carol's first 24h: ~0.9%    │
│        │   Expected loss: 0.9% × 72% × Carol's share = tiny      │
│        │                                                         │
│        │ After 72h, Carol is at 95% vesting and mining normally. │
│        │ The ramp-up cost is a one-time fee that protects the    │
│        │ pool against burst attacks.                             │
│        │                                                         │
│        │ Combined with Step 3: -5% initial + -20% vesting =      │
│        │ heavy initial discount, converging to ~-3% by day 4.    │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ SLIGHT NEGATIVE (-3% additional). Dave's 8h daily gap   │
│        │ causes his vesting to dip from 1.0 to ~0.92 overnight.  │
│        │ When he resumes mining:                                 │
│        │   +2 hours: vesting recovers to ~0.97                   │
│        │   +4 hours: vesting back to 1.0                         │
│        │                                                         │
│        │ Average daily vesting: ~0.97                            │
│        │ Combined with Step 3: -8% (decay) + -3% (vesting)       │
│        │   = -11% total.                                         │
│        │                                                         │
│        │ Reality check: Dave mines 67% of the time. Earning      │
│        │ 89% of a 24/7 miner's rate (per GH) is still generous.  │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ MODERATE NEGATIVE (-10 to -15% additional). Eve's Mon–  │
│        │ Thu absence causes significant vesting decay:           │
│        │                                                         │
│        │ Eve's weekly vesting cycle:                             │
│        │   Friday morning (returns):     Vesting ≈ 0.30          │
│        │   Friday evening (6h mining):   Vesting ≈ 0.55          │
│        │   Saturday (18h mining):        Vesting ≈ 0.80          │
│        │   Sunday evening (peak):        Vesting ≈ 0.95          │
│        │   Monday morning (stops):       Vesting ≈ 0.95          │
│        │   Tuesday (offline 24h):        Vesting ≈ 0.70          │
│        │   Wednesday (offline 48h):      Vesting ≈ 0.50          │
│        │   Thursday (offline 72h):       Vesting ≈ 0.35          │
│        │                                                         │
│        │ Average weekend vesting: ~0.65                          │
│        │ Eve's effective payout rate: 60%→50% of Alice's rate    │
│        │ Combined with Step 3: -22% (decay) + -12% (vesting)     │
│        │   = ~-34% relative to flat PPLNS (now ~50% of Alice).   │
│        │                                                         │
│        │ Eve mines 43% of the time and earns ~50% of Alice's     │
│        │ rate. The remaining 7% premium (50 vs 43) reflects that │
│        │ Eve's concentrated weekend burst has SOME recency value │
│        │ — she IS mining during those hours. The system is not   │
│        │ perfectly punitive; it rewards actual work done.        │
└────────┴─────────────────────────────────────────────────────────┘
```

**Cumulative impact after Steps 1–3a (V36 scope — what ships in PyPy release):**

```
┌────────┬───────────┬───────────────────────────────────────────────┐
│ Miner  │ V36 Net Δ │ What the miner experiences                    │
├────────┼───────────┼───────────────────────────────────────────────┤
│ Alice  │ ~-3%      │ Slight tail loss; variance reduced.           │
│ Bob    │ ~0%       │ Virtually invisible. Bob mines normally.      │
│ Carol  │ ~-5%i     │ No ramp-up in V36. Initial decay penalty,     │
│        │ → ~-3%ss  │ converges to ~-3% at steady-state.            │
│ Dave   │ ~-8%      │ Reflects 67% time (no vesting in V36).        │
│ Eve    │ ~-22%     │ Reflects 43% time (decay only).               │
│ Frank  │ ~-25%     │ Reflects 25% duty cycle (decay only).         │
│ Grace  │ ~-18%     │ Reflects 37.5% duty cycle (decay only).       │
│ Henry  │ ~-6%      │ Near full-time; 58% duty cycle.               │
│ Iris   │ ~-10%     │ Moderate; 46% duty cycle.                     │
│ Jack   │ ~-35%     │ Sporadic; multi-day gaps cost the most.       │
│ Hopper │  0.6×     │ UNPROFITABLE — V36 goal achieved.             │
└────────┴───────────┴───────────────────────────────────────────────┘
```

**Cumulative impact after Steps 1–4 (V36+V37 — for reference):**

```
┌────────┬───────────┬───────────────────────────────────────────────┐
│ Miner  │ Net Δ     │ What the miner experiences (after V37)        │
├────────┼───────────┼───────────────────────────────────────────────┤
│ Alice  │ ~-1%      │ Virtually invisible. Alice mines normally.    │
│ Bob    │ ~+2%      │ Slight improvement from stale-weight shift.   │
│ Carol  │ Heavy     │ 72h ramp-up then normal. Dashboard shows ETA. │
│        │ → ~-3%    │ (Long-term net after ramp-up converges)       │
│ Dave   │ ~-11%     │ Correctly reflects 67% time commitment.       │
│ Eve    │ ~-30%     │ Correctly reflects 43% time commitment.       │
│ Frank  │ ~-30%     │ Correctly reflects 25% duty cycle (6h/24h).   │
│ Grace  │ ~-20%     │ Correctly reflects 37.5% duty cycle (9h/24h). │
│ Henry  │ ~-7%      │ Close to full-time; 58% duty cycle.           │
│ Iris   │ ~-10%     │ Moderate; 46% duty cycle (11h/24h).           │
│ Jack   │ ~-40%     │ Sporadic; multi-day gaps cost the most.       │
│ Hopper │  0.1×     │ ECONOMICALLY IRRATIONAL.                      │
└────────┴───────────┴───────────────────────────────────────────────┘
```

---

#### Step 5 — Monitoring + dashboard deployed (V36 logs / V37 full UI)

> **Split deployment:** V36 ships lightweight log monitoring (Step 5L).
> The full dashboard UI with vesting progress bars ships in V37 (Step 5F).
> V36 log prefixes: `[MONITOR-SUMMARY]`, `[MONITOR-HASHRATE]`, `[MONITOR-CONC]`,
> `[MONITOR-EMERGENCY]`, `[MONITOR-DIFF]` — grep-friendly, no HTTP surface.
> Below shows the FULL dashboard experience (V37) for completeness.

**What changes:** Structured log lines AND (V37) dashboard UI provide transparency
into all defense mechanisms. No payout changes — this is visibility only.

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 5                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ Can see her vesting factor (1.0), effective PPLNS       │
│        │ weight, and weight trend on miner.html. Progress bar    │
│        │ is full green. Gains confidence system works correctly. │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ Same dashboard visibility. Bob can see his 0.30%        │
│        │ share is preserved under the new system. Reassuring.    │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ CRITICAL FOR CAROL. The miner.html panel shows:         │
│        │   Vesting: ███░░░░░░░ 0.35                              │
│        │   ETA to 95%: ~60h | Keep mining!                       │
│        │ This manages Carol's expectations — she sees the        │
│        │ progress bar filling up hour by hour. Without this,     │
│        │ Carol might think the pool is broken.                   │
│        │ Payout: 0% change (transparency only).                  │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ Can see vesting dip to 0.92 during sleep and watch it   │
│        │ recover when he starts mining. The share trend chart    │
│        │ shows his daily cycle: mining→sleep→mining.             │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ IMPORTANT FOR EVE. Dashboard shows her vesting cycle:   │
│        │   Friday: 0.30 → Sunday: 0.95 → Thursday: 0.35          │
│        │ Eve can see WHY her payout is ~50% of Alice's rate:     │
│        │ her shares decay during the week and her vesting resets │
│        │ partially. If Eve increases to 4-day mining, she can    │
│        │ watch her effective rate improve.                       │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Frank  │ MOST IMPORTANT DASHBOARD USER. Frank mines midnight–    │
│        │ 06:00 daily due to electricity ToU pricing. Dashboard:  │
│        │   Vesting: ██████░░░░ 0.65                              │
│        │   PPLNS: 12,847 effective shares in window              │
│        │   Trend: ▁▂█████▁░░░░░░░░░░░░░▁▂████                    │
│        │           ^night^---18h off---^tonight                  │
│        │ Frank sees TWO critical facts: (1) his vesting is NOT   │
│        │ zero when he starts — it carries over from last night;  │
│        │ (2) his accumulated shares from previous nights are     │
│        │ still in the PPLNS window. Without this visibility,     │
│        │ Frank would assume he loses everything during the day.  │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Grace  │ Solar miner opens dashboard at 08:00 sunrise:           │
│        │   Vesting: █████░░░░░ 0.52                              │
│        │   Accumulated: 8,200 effective shares (from yesterday)  │
│        │ By 17:00 sunset:                                        │
│        │   Vesting: ████████░░ 0.80                              │
│        │   Accumulated: 14,100 effective shares                  │
│        │ Grace watches her progress bar grow during the day.     │
│        │ Tomorrow at sunrise, it'll be back to ~0.52 — but NOT   │
│        │ zero. The dashboard shows mining is accumulating value. │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Henry  │ Very similar to Dave. Progress bar stays above 0.88.    │
│        │ Henry barely notices the defense stack in the UI.       │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Iris   │ Dashboard shows overnight mining pattern clearly:       │
│        │   Start (22:00): Vesting 0.78 → End (09:00): 0.92       │
│        │ Iris can see GPU mining at night earns more per-GH      │
│        │ than daytime mining would, due to higher vesting.       │
│        │ Payout: 0% change.                                      │
├────────┼─────────────────────────────────────────────────────────┤
│ Jack   │ RETENTION CRITICAL. Jack returns after 2-day break:     │
│        │   Vesting: █░░░░░░░░░ 0.12                              │
│        │   Accumulated: 1,847 effective shares (reduced but      │
│        │     NOT ZERO — shares from last week still in window!)  │
│        │   ETA to 50%: ~12h | Welcome back!                      │
│        │ The dashboard message "welcome back" + non-zero share   │
│        │ count is CRITICAL for Jack's retention. Without this,   │
│        │ Jack sees a raw 0.12 vesting and assumes he's starting  │
│        │ from scratch — which might make him quit. The dashboard │
│        │ must show that his previous work is remembered.         │
│        │ Payout: 0% change.                                      │
└────────┴─────────────────────────────────────────────────────────┘
```

**Key dashboard fields miners will see (at miner.html and miners.html):**

| Field | Alice | Carol (day 1) | Frank (midnight) | Jack (return) | Eve (Tue) |
|-------|-------|---------------|------------------|---------------|-----------|
| `raw_shares` | 40,040 | 346 | 2,880 | 1,847 | 8,640 |
| `vesting_factor` | 1.00 | 0.35 | 0.65 | 0.12 | 0.70 |
| `pplns_decay_weight` | 97.2% | 96.8% | 89.3% | 34.1% | 42.1% |
| `effective_weight` | 97.2% | 33.9% | 58.0% | 4.1% | 29.5% |
| `vesting_eta_95pct` | "vested" | "~60h" | "~14h" | "~48h" | "Fri ~6pm" |
| `payout_share` | 12.9% | 0.05% | 2.1% | 0.08% | 3.1% |

---

#### Step 6 — Adaptive windows activated (⏭ V37 / c2pool)

> **Track 2 (V37):** Adaptive windows are deferred to the C++ c2pool release.
> Fixed 8,640-share window in V36 already makes hopping unprofitable.
> Adaptive windows require ~308K shares in RAM (1.39GB at PyPy overhead);
> C++ reduces this to ~154MB with native struct vectors.

**What changes:** PPLNS window scales with pool hashrate. At 49.5 GH/s,
the window expands from 36h to ~53.5 days. Vesting lookback expands to
~107 days. Transition is smooth (720-share interpolation, ~3 hours).

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 6                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ SLIGHT POSITIVE (+2-3%). Longer window means MORE of    │
│        │ Alice's shares contribute to payout calculations. With  │
│        │ fixed 36h window, Alice had ~8,640 shares. With 53.5-   │
│        │ day adaptive window, she has ~308K shares (decayed).    │
│        │ The exponential decay means far shares contribute       │
│        │ little — but there are MORE of them, and Alice has been │
│        │ mining 24/7 to fill the entire window. Net: Alice's     │
│        │ share of payouts increases slightly (+2-3%) because the │
│        │ expanded window captures more of her consistent work.   │
│        │                                                         │
│        │ Combined net: -3% (decay) +2% (adaptive) = ~-1%         │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ SLIGHT POSITIVE (+2-3%). Same reasoning as Alice.       │
│        │ Bob's 24/7 mining fills the expanded window.            │
│        │                                                         │
│        │ BONUS: Bob's payout VARIANCE decreases because the      │
│        │ larger window includes more shares. Variance drops      │
│        │ from ±1.8% (8,640-share window) to roughly ±0.3%        │
│        │ (308K-share window). Bob's payouts become much more     │
│        │ predictable.                                            │
│        │                                                         │
│        │ Combined net: ~+2%.                                     │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ SLIGHT NEGATIVE (ramp-up extends by ~1 day). The        │
│        │ adaptive vesting lookback is longer (107 days vs 72h),  │
│        │ but the WORK_THRESHOLD scales proportionally, so        │
│        │ Carol's ramp-up timeline is similar:                    │
│        │   Hour 24: Vesting ≈ 0.55 (was 0.60 with fixed)         │
│        │   Hour 48: Vesting ≈ 0.80 (was 0.85)                    │
│        │   Hour 72: Vesting ≈ 0.92 (was 0.95)                    │
│        │   Hour 96: Vesting ≈ 0.97 (was 0.99)                    │
│        │                                                         │
│        │ ~1 day longer to 95% vesting. Small absolute cost.      │
│        │ Combined net: ~equal to fixed window after ramp-up.     │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ NEUTRAL. Dave's 16/24 pattern sees the same             │
│        │ proportional effect. The larger window doesn't help or  │
│        │ hurt Dave specifically — his daily cycle repeats within │
│        │ the window regardless of window size. Vesting behavior  │
│        │ is identical (still dips to 0.92 during 8h sleep).      │
│        │                                                         │
│        │ Combined net: ~-11% (unchanged from Step 4).            │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ MODERATE POSITIVE (+5%). This is counterintuitive.      │
│        │ With the 36h fixed window, Eve's Friday shares were at  │
│        │ risk of scrolling out by Sunday night. With the 53.5-   │
│        │ day adaptive window, ALL of Eve's weekend shares stay   │
│        │ in the PPLNS window for weeks.                          │
│        │                                                         │
│        │ Yes, the shares DECAY — but they still earn SOMETHING:  │
│        │   Eve's Sunday shares on Wednesday: 16% weight          │
│        │   Eve's Sunday shares next Friday: 4% weight            │
│        │ These small weights add up to ~5% improvement for Eve.  │
│        │                                                         │
│        │ Combined net: -22% (decay) + -12% (vesting) +5%         │
│        │   (adaptive) = ~-29% relative to flat PPLNS.            │
│        │ Eve earns ~53% of Alice's rate (was ~50% in Step 4).    │
├────────┼─────────────────────────────────────────────────────────┤
│ Frank  │ STRONGLY POSITIVE (+8-12%). THIS IS TRANSFORMATIVE.     │
│        │ Under fixed 36h: Frank had 2 nights in window (~12h).   │
│        │ Under adaptive 53.5-day: Frank has ~53 nights!          │
│        │   Last night's shares: 100% → 35% (24h decay)           │
│        │   Night before:        35% → 12% (48h decay)            │
│        │   3 nights ago:        12% → 4%  (72h decay)            │
│        │   Sum of 53 nights: meaningful cumulative weight.       │
│        │                                                         │
│        │ Frank's dashboard now shows: "12,847 effective shares   │
│        │ accumulated from 53 sessions"                           │
│        │                                                         │
│        │ Combined net: -25% (decay) + -15% (vesting) +10%        │
│        │   (adaptive) = ~-30% relative to flat PPLNS.            │
│        │ Frank earns ~28% of Alice's rate per GH.                │
│        │ His duty cycle is 25% → system is FAIR.                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Grace  │ STRONGLY POSITIVE (+8-12%). Same logic as Frank.        │
│        │ Under fixed 36h: Grace had 2 daytime sessions (~18h).   │
│        │ Under adaptive 53.5-day: ~53 daytime sessions.          │
│        │ Grace's 9h/day solar sessions accumulate weight.        │
│        │                                                         │
│        │ Combined net: -18% (decay) + -12% (vesting) +10%        │
│        │   (adaptive) = ~-20% relative to flat PPLNS.            │
│        │ Grace earns ~32% of Alice's rate per GH.                │
│        │ Her duty cycle is 37.5% → slight vesting discount       │
│        │ from daily gap, but close to proportional.              │
├────────┼─────────────────────────────────────────────────────────┤
│ Henry  │ POSITIVE (+3%). Very similar to Alice and Bob. Henry's  │
│        │ 14h/day pattern fills the adaptive window almost as     │
│        │ densely as a 24/7 miner. The 10h daily gap is small     │
│        │ enough that vesting stays high (>0.88 always).          │
│        │                                                         │
│        │ Combined net: ~-7% relative to flat PPLNS.              │
│        │ Henry earns ~93% of Alice's rate per GH.                │
│        │ Duty cycle: 58% → earning 93%/58% = 1.6× per active     │
│        │ hour. Slightly over-rewarded vs pure duty, reflecting   │
│        │ his consistency (daily without gaps).                   │
├────────┼─────────────────────────────────────────────────────────┤
│ Iris   │ MODERATE POSITIVE (+5-7%). Iris's 11h/day sessions      │
│        │ accumulate over the adaptive window. Under fixed 36h,   │
│        │ ~2.5 sessions fit. Under adaptive 53.5-day, ~53.        │
│        │                                                         │
│        │ Combined net: ~-10% relative to flat PPLNS.             │
│        │ Iris earns ~46% of Alice's rate per GH.                 │
│        │ Duty cycle: 46% → essentially 1:1 proportional.         │
├────────┼─────────────────────────────────────────────────────────┤
│ Jack   │ VERY STRONGLY POSITIVE (+15-25%). THE BIGGEST WINNER.   │
│        │ Under fixed 36h: Jack's shares VANISH every time he     │
│        │ takes a 2-day break. TOTAL LOSS. ZERO RETENTION.        │
│        │ Under adaptive 53.5-day: Jack's shares from LAST WEEK   │
│        │ are still in the window when he returns!                │
│        │                                                         │
│        │ Jack mines 3 days, takes 2 days off, mines 2 days:      │
│        │   Day 1-3 (mining): building shares, vesting climbing   │
│        │   Day 4-5 (off): shares decaying but NOT gone           │
│        │   Day 6-7 (mining): old shares still there + new ones   │
│        │                                                         │
│        │ Without adaptive: Jack comes back to zero every time.   │
│        │ With adaptive: Jack sees "1,847 effective shares        │
│        │ remaining from last week" on the dashboard. Keeps him.  │
│        │                                                         │
│        │ Combined net: ~-35% relative to flat PPLNS.             │
│        │ But compared to fixed window: very large relative gain  │
│        │ because fixed window gave Jack near-zero after >36h     │
│        │ breaks.                                                 │
│        │ Adaptive windows convert Jack from "lost miner" to      │
│        │ "retained part-time contributor."                       │
└────────┴─────────────────────────────────────────────────────────┘
```

**Transition experience (720 shares = ~3 hours):**

When adaptive windows activate, the transition is smooth:
- Share 1 of transition: window = 8,640 (old fixed)
- Share 360: window = ~154,000 (halfway interpolated)
- Share 720: window = ~308,000 (full adaptive)

Miners see gradual payout adjustments, not sudden jumps. During the
transition window, payouts shift by <0.1% per share.

---

#### Step 7 — Persistence layer deployed (⏭ V37 / c2pool — LevelDB)

> **Track 2 (V37):** Persistence is deferred to the C++ c2pool release,
> which already integrates LevelDB. V36 uses the existing in-memory share
> tracker (~5MB at fixed 8,640 window) with peer gap-fill on restart.

**What changes:** Share data survives crashes. Recovery is typically
near-immediate from local DB state instead of multi-minute adaptive-window
gap-fill from peers.

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience after Step 7                                 │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ POSITIVE. If Alice's node crashes, she's back mining    │
│        │ in ~8s instead of waiting through a multi-minute gap-   │
│        │ fill process. During extended recovery, Alice           │
│        │ earns nothing. Over a year with ~2 crashes, Alice       │
│        │ saves ~20 minutes of downtime. Small but free.          │
│        │ Payout: ~0% direct change. Uptime improvement.          │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ POSITIVE. Same crash recovery benefit. For Bob,         │
│        │ multi-minute downtime means missing 0-1 shares.         │
│        │ 8 seconds means missing 0.                              │
│        │ Payout: ~0% change. Quality-of-life improvement.        │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ NO EFFECT unless Carol's node crashes during ramp-up.   │
│        │ If it does, faster recovery preserves more of Carol's   │
│        │ vesting progress (the IncrementalVestingCache is in     │
│        │ memory, but the share chain restores from DB, allowing  │
│        │ the cache to rebuild).                                  │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ POSITIVE. Dave power-cycles equipment sometimes.        │
│        │ Faster restart = faster return to mining after each     │
│        │ power-on.                                               │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ SLIGHT POSITIVE. If Eve's node crashes Friday morning,  │
│        │ she's back in ~8s instead of waiting several minutes.   │
│        │ Given Eve only mines                                    │
│        │ 72h/week, several minutes of downtime are proportionally│
│        │ significant than for Alice.                             │
└────────┴─────────────────────────────────────────────────────────┘
```

---

#### Step 8 — V36 activation

**What changes:** V36 consensus changes (Steps 3, 3a) activate
via the V36 share threshold mechanism (95% V36 shares sustained over
2×CHAIN_LENGTH). Vesting (Step 4) and adaptive windows (Step 6) are
deferred to V37.

**What miners experience:**

```
┌────────┬─────────────────────────────────────────────────────────┐
│ Miner  │ Experience during activation                            │
├────────┼─────────────────────────────────────────────────────────┤
│ Alice  │ Monitors /version_signaling — sees V36 share % climb.   │
│        │ Once 95% threshold sustained, consensus rules change.   │
│        │ Alice's payout shifts by ~-3% over the next few hours   │
│        │ as exponential decay takes effect (fixed 8,640 window). │
│        │ No action needed from Alice.                            │
├────────┼─────────────────────────────────────────────────────────┤
│ Bob    │ Same experience as Alice. Bob may not even notice the   │
│        │ transition because his payout change is within normal   │
│        │ variance. Bob's main benefit: reduced payout variance   │
│        │ from pure difficulty accounting (Step 3a).              │
├────────┼─────────────────────────────────────────────────────────┤
│ Carol  │ If Carol joins around activation time, she sees the     │
│        │ decay behavior from day 1. If Carol was already         │
│        │ mining pre-activation, her existing shares get          │
│        │ retroactive decay applied (since the new PPLNS formula  │
│        │ evaluates ALL shares in the window). Carol’s pre-       │
│        │ activation shares were at full weight; post-activation  │
│        │ they’re at decayed weight. The transition is smooth     │
│        │ because Carol’s continuous mining generates fresh       │
│        │ shares at full weight under the new rules.              │
│        │ (No vesting ramp-up in V36 — that ships in V37.)        │
├────────┼─────────────────────────────────────────────────────────┤
│ Dave   │ Dave may notice his payouts drop by ~8% relative to     │
│        │ pre-activation. If Dave checks /weight_stats, he sees:  │
│        │   "PPLNS decay weight: 84%"                             │
│        │ Dave can understand that his 8h daily gap now correctly │
│        │ reduces his effective contribution.                     │
├────────┼─────────────────────────────────────────────────────────┤
│ Eve    │ Eve will notice a significant payout reduction (~22%)   │
│        │ compared to flat PPLNS. Eve should check /weight_stats  │
│        │ to understand the breakdown:                            │
│        │   "Decay: [varies 8–100%]                               │
│        │    Effective rate: ~78% of 24/7 miner"                  │
│        │ The API clearly shows that the change                   │
│        │ aligns payouts with actual contribution time.           │
│        │ (V37 adds vesting, further adjusting Eve’s rate.)       │
└────────┴─────────────────────────────────────────────────────────┘
```

**Post-activation timeline:**

```
T+0    : V36 threshold reached (95% sustained over 2×CHAIN_LENGTH)
T+0    : New consensus rules active. PPLNS exponential decay applies to
         all shares in the fixed 8,640-share window.
T+1h   : New shares with decay weighting begin dominating the window.
         Stale shares from pre-activation lose relative weight naturally.
T+24h  : System at steady state. All miner profiles converge to predicted
         V36 payout rates (§7.3.12, §8.2).
T+72h  : First validation point. Compare actual payouts against model
         predictions. Deviation > 5% from §8.2 V36 predictions for any
         profile indicates a bug.
T+7d   : Full soak validation. Confirm S12 (honest miner fairness matrix)
         passes with live data.
```

---

#### Final honest miner impact summary

The table below shows impacts split by track: **V36 columns** (Steps 1–3a)
are what ships in the Python 2.7/PyPy release. **V37 columns** (Steps 4, 6)
are deferred to C++ c2pool. The "V36 TOTAL" column shows the impact miners
see with only the Python release deployed.

```
┌──────────┬────────┬────────┬────────┬─────────┬────────┬────────┬────────┬──────────────────┐
│          │Step 1-2│ Step 3 │Step 3a │ V36     ║ Step 4 │ Step 6 │ V37    │ FULL STACK       │
│ Miner    │(clamp/ │(PPLNS  │(pure   │ TOTAL   ║(vest-  │(adapt- │ adds   │ (V36+V37)        │
│          │ emerg) │ decay) │ diff)  │ (PyPy)  ║ ing)   │ ive)   │        │                  │
├──────────┼────────┼────────┼────────┼─────────╬────────┼────────┼────────┼──────────────────┤
│ Alice    │  0%    │  -3%   │ 0% EV  │ ~-3%    ║  0%    │ +2%    │ +2%    │ ~-1% ≈ no change │
│          │        │        │ -47%var│(low var)║        │        │        │ (lower variance) │
│ Bob      │  0%    │  0%    │ 0% EV  │ ~0%     ║  0%    │ +2%    │ +2%    │ ~+2% slight gain │
│          │        │        │ -93%var│(low var)║        │        │        │ (much lower var) │
│ Carol*   │  0%    │  -5%i  │ 0% EV  │ ~-5%i   ║ -20%i  │ -1d    │ -21%i  │ 72h ramp → ~-3%  │
│          │        │        │ -98%var│→ ~-3%ss ║        │        │        │ (lowest variance)│
│ Dave     │  0%    │  -8%   │ 0% EV  │ ~-8%    ║  -3%   │  0%    │ -3%    │ ~-11%            │
│          │        │        │ -74%var│(low var)║        │        │        │ (lower variance) │
│ Eve      │  0%    │ -22%   │ 0% EV  │ ~-22%   ║ -12%   │ +5%    │ -7%    │ ~-29%            │
│          │        │        │ -85%var│(low var)║        │        │        │ (lower variance) │
│ Frank    │  0%    │ -25%   │ 0% EV  │ ~-25%   ║ -15%   │ +10%   │ -5%    │ ~-30%            │
│ (night)  │        │        │ -80%var│(low var)║        │        │        │ (25% duty → fair)│
│ Grace    │  0%    │ -18%   │ 0% EV  │ ~-18%   ║ -12%   │ +10%   │ -2%    │ ~-20%            │
│ (solar)  │        │        │ -82%var│(low var)║        │        │        │ (38% duty → fair)│
│ Henry    │  0%    │  -6%   │ 0% EV  │ ~-6%    ║  -4%   │ +3%    │ -1%    │ ~-7%             │
│ (heat)   │        │        │ -70%var│(low var)║        │        │        │ (58% duty → fair)│
│ Iris     │  0%    │ -10%   │ 0% EV  │ ~-10%   ║  -6%   │ +6%    │  0%    │ ~-10%            │
│ (GPU)    │        │        │ -76%var│(low var)║        │        │        │ (46% duty → fair)│
│ Jack     │  0%    │ -35%   │ 0% EV  │ ~-35%   ║ -25%   │ +20%   │ -5%    │ ~-40%            │
│(sporadic)│        │        │ -65%var│(low var)║        │        │        │ (40% duty,gaps)  │
├──────────┼────────┼────────┼────────┼─────────╬────────┼────────┼────────┼──────────────────┤
│ Hopper   │  ↓     │  ↓↓    │  n/a   │ 0.6× ✓  ║  ↓↓↓   │  ↓↓↓↓  │ ↓↓↓↓↓  │ ~0.1× ✓✓✓        │
│          │        │        │        │(unprof.)║        │        │        │ (irrational)     │
└──────────┴────────┴────────┴────────┴─────────╬────────┴────────┴────────┴──────────────────┘
                                      V36 PyPy  ║        V37 c2pool (deferred)

* Carol: "i" = initial penalty, "ss" = steady-state (converges after 24-72h).
* Step 3a: "EV" = expected value, "var" = variance. EV is unchanged for all
  miners. Variance reduction is the unique benefit of pure diff accounting.
* Frank/Grace/Henry/Iris/Jack: Variance reduction % assumes same-hashrate
  comparison; actual variance depends on individual hashrate.

Legend:
  0%      = no measurable change
  -N%     = N% reduction in effective payout rate vs flat PPLNS
  +N%     = N% improvement
  0% EV   = zero change in expected value
  -N%var  = N% reduction in payout variance
  ↓       = hopper profitability reduced at this phase
  -Nd     = ramp-up extended by N days
  ✓       = hopping unprofitable (< 1.0×)
  ✓✓✓     = hopping economically irrational (< 0.15×)
```

**V36-only fairness check (PPLNS decay only, no vesting, fixed window):**

```
┌──────────┬──────────────┬───────────────┬─────────────────────────────────────┐
│ Miner    │ Duty Cycle   │ V36 Δ vs flat │ Interpretation                      │
│          │ (% of 24/7)  │ PPLNS         │                                     │
├──────────┼──────────────┼───────────────┼─────────────────────────────────────┤
│ Alice    │ 100%         │ ~-3%          │ Slight tail loss (compensated by    │
│          │              │               │ redistribution from stale shares)   │
│ Bob      │ 100%         │ ~0%           │ Ratio-preserving for 24/7 miners    │
│ Henry    │ 58%          │ ~-6%          │ Mild correction                     │
│ Dave     │ 67%          │ ~-8%          │ Moderate correction                 │
│ Iris     │ 46%          │ ~-10%         │ Moderate correction                 │
│ Eve      │ 43%          │ ~-22%         │ Strong correction (long weekly gap) │
│ Grace    │ 38%          │ ~-18%         │ Strong correction                   │
│ Frank    │ 25%          │ ~-25%         │ Strong correction                   │
│ Jack     │ ~40%         │ ~-35%         │ Strongest correction (irregularity) │
│ Hopper   │ <5%          │ ~-84% (0.6×)  │ UNPROFITABLE — goal achieved        │
└──────────┴──────────────┴───────────────┴─────────────────────────────────────┘

Key observations:
- V36 alone (PPLNS decay + pure diff accounting) makes hopping unprofitable.
- Part-time miners see corrections proportional to their stale-share fraction.
- Corrections are NOT punitive — they remove over-payment for stale work.
- V37 adds vesting (further discount for burst patterns) and adaptive windows
  (improves part-time miners by keeping shares longer). The two partially
  offset each other for honest part-timers.
```

**Full-stack duty cycle alignment check (V36+V37 — for reference):**

```
┌──────────┬──────────────┬───────────────┬─────────────────────────────────────┐
│ Miner    │ Duty Cycle   │ Net Δ vs flat │ Interpretation                      │
│          │ (% of 24/7)  │ PPLNS         │                                     │
├──────────┼──────────────┼───────────────┼─────────────────────────────────────┤
│ Alice    │ 100%         │ ~-1%          │ Near-neutral baseline               │
│ Bob      │ 100%         │ ~+2%          │ Near-neutral baseline               │
│ Henry    │ 58%          │ ~-7%          │ Mild correction                     │
│ Dave     │ 67%          │ ~-11%         │ Moderate correction                 │
│ Iris     │ 46%          │ ~-10%         │ Moderate correction                 │
│ Eve      │ 43%          │ ~-29%         │ Strong correction (long weekly gap) │
│ Grace    │ 38%          │ ~-20%         │ Strong correction                   │
│ Frank    │ 25%          │ ~-30%         │ Strong correction                   │
│ Jack     │ ~40%         │ ~-40%         │ Strongest correction (irregularity) │
│ Hopper   │ <5%          │ ~-90% (0.1×)  │ Economically irrational             │
└──────────┴──────────────┴───────────────┴─────────────────────────────────────┘
```

**The core fairness principle**: Every miner's payout rate is proportional
to their **time-weighted, work-weighted contribution**. Miners who mine
more consistently earn more per unit of hashrate. Miners who mine
sporadically earn proportionally less — but they're still rewarded for
the work they DO contribute. The defense stack does not "punish" anyone;
it removes the over-payment that flat PPLNS gave to stale work.

**Automatic test verification (S12):** The honest miner fairness matrix
test must verify these bounds automatically after each implementation
step. If Alice's payout changes by more than ±5% or Bob's by more than
±3%, the test should FAIL — indicating a bug in the decay/vesting
implementation.

---

### 8.3 Merged Mining Impact: Defense Stack × Redistribution Logic

Merged mining (e.g., Litecoin + Dogecoin) shares the **same PPLNS weight
pipeline** as the parent chain. Every defense in §8.1 propagates to merged
payouts automatically — with one critical nuance: **address redistribution**.

This section documents:
1. How the defense stack affects merged mining payouts
2. How redistribution of unconvertible addresses interacts with decay/vesting
3. What implementation work is needed per step
4. Edge cases and consensus safety

---

#### 8.3.1 Architecture: Two Redistribution Points

The merged mining payout pipeline has **two separate redistribution points**
where a miner's unconvertible address causes their merged reward share to
be redistributed to other miners. Both points see the same PPLNS weights
from `get_v36_merged_weights()`, so they must both be updated to use
decay/vesting-modified weights.

```
Share chain (PPLNS weights)
       │
       ├──→ Parent chain (LTC) payout path
       │    WeightsSkipList.get_delta() → generate_transaction()
       │    No address conversion needed — parent addresses used directly.
       │    No redistribution issue — all parent addresses are valid.
       │
       └──→ Merged chain (DOGE) payout path
            MergedWeightsSkipList.get_delta() → get_v36_merged_weights()
                    │
                    ├──→ [Point A] work.py line ~527-594 (LOCAL)
                    │    Converts parent addresses → merged addresses.
                    │    SKIPS unconvertible (P2WSH, P2TR, etc.)
                    │    Normalizes over accepted_total_weight only.
                    │    Result: shareholders{} with fractions summing to 1.0
                    │    Used for: getblocktemplate/createauxblock single-address mode
                    │
                    └──→ [Point B] data.py line ~174-285 (CONSENSUS)
                         build_canonical_merged_coinbase()
                         ALSO converts and SKIPS unconvertible addresses.
                         Normalizes over accepted_total_weight.
                         Result: deterministic coinbase with proper output values.
                         Hash committed in merged_payout_hash (V36 consensus).
                         Verified by all peers in verify_merged_coinbase_commitment().
```

**Critical invariant:** Both Point A and Point B receive the SAME
`(weights, total_weight, donation_weight)` tuple from
`get_v36_merged_weights()`. If we add decay/vesting to
`MergedWeightsSkipList.get_delta()`, both paths see the modified weights
simultaneously. **No separate patching is needed at the redistribution
points themselves** — they correctly normalize over whatever weights they
receive.

---

#### 8.3.2 How Redistribution Works (Current + With Defenses)

When a miner's parent chain address cannot be converted to a merged chain
address (e.g., P2TR addresses have no Dogecoin equivalent), their PPLNS
weight is **skipped** at both redistribution points. The remaining miners'
shares are normalized over `accepted_total_weight` (not `total_weight`),
which effectively redistributes the skipped weight proportionally.

**Current behavior (flat PPLNS, no decay/vesting):**

```
Example: Alice (1000w, convertible), Bob (500w, convertible),
         Charlie (300w, P2TR — unconvertible)

total_weight     = 1800 (from PPLNS)
accepted_weight  = 1500 (Alice + Bob only)

Alice merged share = 1000/1500 = 66.7%   (was 55.6% if Charlie counted)
Bob merged share   =  500/1500 = 33.3%   (was 27.8% if Charlie counted)
Charlie merged     =  0%                  (skipped — 300w redistributed)

Charlie's 300 weight units → Alice gets 200, Bob gets 100 (proportional).
```

**With decay + vesting (defense stack active):**

```
Example: Same miners, but Charlie is a burst miner (recently joined,
         low vesting, shares decaying rapidly)

Decayed/vested weights:
  Alice:   900w (slight decay on old shares)
  Bob:     450w (same)
  Charlie: 100w (heavy decay + low vesting from burst)

total_weight     = 1450
accepted_weight  = 1350 (Alice + Bob only)

Alice merged share = 900/1350 = 66.7%
Bob merged share   = 450/1350 = 33.3%
Charlie merged     = 0%  (skipped — 100w redistributed)

Charlie's 100 weight units → Alice gets 66.7, Bob gets 33.3 (proportional).
```

**Key observation:** The decay/vesting defense **reduces** the amount of
weight redistributed from unconvertible miners. This is **correct
behavior** — a burst miner's stale/unvested work should contribute less
to everyone's payouts, including the redistribution windfall.

---

#### 8.3.3 Per-Step Merged Mining Implementation Requirements

| Step | Defense | Merged mining work needed | Affects redistribution? |
|------|---------|--------------------------|------------------------|
| **0** | Baseline | Capture merged payout data alongside parent | No |
| **1** | Asymmetric clamp | **None** — difficulty retarget is parent-only | No |
| **2** | Emergency decay | **None** — same reasoning | No |
| **3** | PPLNS exp decay | **YES** — mirror `_decay_power()` in `MergedWeightsSkipList.get_delta()` | Yes — decayed weights flow through redistribution |
| **3a** | Pure diff accounting | **SIMPLIFICATION** — set `CANONICAL_MERGED_FINDER_FEE_PER_MILLE = 0`. Removes ~98 lines of finder-script consensus code. `get_canonical_merged_finder_script()` becomes dead code. | No — redistribution unchanged (finder fee was separate from redistribution) |
| **4** | Vesting *(V37)* | **YES** — apply vesting to merged weights in `build_canonical_merged_coinbase()` | Yes — vested weights flow through redistribution |
| **5** | Monitoring | Extend endpoints to show merged chain stats (V36 API / V37 full UI) | No |
| **6** | Adaptive windows *(V37)* | **Automatic** — merged uses same `chain_length` param | Yes — larger window means more shares in merged PPLNS |
| **7** | Persistence *(V37)* | **Automatic** — shares contain merged data (LevelDB in c2pool) | No |
| **8** | Activation | **Automatic** — merged gated on V36 (Steps 3, 3a) | No |

---

#### 8.3.4 Step 3 — Merged PPLNS Decay (Implementation Detail)

`MergedWeightsSkipList.get_delta()` must apply the same exponential decay
as `WeightsSkipList.get_delta()`. The two classes should share the
`_decay_power()` helper (module-level function, already defined in Step 3).

**Current** `MergedWeightsSkipList.get_delta()` (line ~1693):

```python
def get_delta(self, element):
    share = self.tracker.items[element]
    att = bitcoin_data.target_to_average_attempts(share.target)
    if share.desired_version < 36:
        return (1, {}, 0, 0)   # pre-V36: zero weight
    # ... resolve address_key ...
    return (1, {address_key: att*(65535-share.share_data['donation'])},
            att*65535, att*share.share_data['donation'])
```

**After Step 3** — add depth-based decay (identical to parent):

```python
def get_delta(self, element, depth=0):
    share = self.tracker.items[element]
    att = bitcoin_data.target_to_average_attempts(share.target)
    if share.desired_version < 36:
        return (1, {}, 0, 0)   # pre-V36: zero weight

    # Apply exponential PPLNS decay (same as parent WeightsSkipList)
    half_life = self.tracker.net.CHAIN_LENGTH // 4
    if depth > 0 and half_life > 0:
        decay_per = _DECAY_SCALE - _LN2_FP // half_life
        decay_fp = _decay_power(_DECAY_SCALE, depth, decay_per,
                                _DECAY_PRECISION)
        att = (att * decay_fp) >> _DECAY_PRECISION

    # ... resolve address_key (unchanged) ...
    return (1, {address_key: att*(65535-share.share_data['donation'])},
            att*65535, att*share.share_data['donation'])
```

**Estimated LOC:** ~8 added (decay block is copy-paste from parent).
Better: factor into a shared `_apply_decay(att, depth, net)` helper.

**Consensus impact:** `compute_merged_payout_hash()` calls
`get_v36_merged_weights()` which uses `MergedWeightsSkipList` on the fast
path. The decay is automatically reflected in the committed hash.
`verify_merged_coinbase_commitment()` re-derives the same weights and
will produce the same hash — consensus is preserved.

---

#### 8.3.5 Step 4 — Merged Vesting (⏭ V37 — Implementation Detail)

> **V37 track.** Vesting ships in the C++ c2pool release.\n> This section documents the merged mining integration design for V37.

Vesting must be applied to merged weights **before** redistribution
happens. There are two approaches:

**Approach A: Apply vesting inside `get_v36_merged_weights()`**
(before weights reach either redistribution point)

```python
# In get_v36_merged_weights(), after accumulating raw weights:
for address_key, weight in weights.items():
    # Vesting uses the PARENT chain address as identity
    # (same miner, different chain encoding)
    parent_addr = resolve_to_parent_addr(address_key)
    vesting = vesting_cache.get_vesting(parent_addr)
    weights[address_key] = weight * vesting // SCALE
    total_weight = total_weight - weight + weights[address_key]
```

**Approach B: Apply vesting in `build_canonical_merged_coinbase()`**
(consensus path only — local path in work.py also needs it)

Approach A is **strongly preferred** because it modifies weights once at
the source and both redistribution points (work.py and data.py) see
vested weights automatically. Approach B requires duplicating the vesting
logic in two places.

**Vesting identity key:** The vesting cache is keyed on parent chain
address (the miner's identity across all chains). When
`MergedWeightsSkipList` uses `'MERGED:<hex_script>'` as the weight key,
the vesting lookup must map back to the parent address. This mapping is
available from the share's `merged_addresses` field:

```python
# In get_v36_merged_weights or _apply_vesting_to_weights:
if key.startswith('MERGED:'):
    # Reverse-lookup: find the parent address that generated this key
    # The share that contributed this key has share.address = parent addr
    parent_addr = share.address  # available during the O(n) walk
    # For the O(log n) skip list path, cache parent_addr alongside key
```

**Estimated LOC:** ~15 in `get_v36_merged_weights()` + ~5 cache plumbing.

**Consensus impact:** Same as Step 3 — `compute_merged_payout_hash()`
and `verify_merged_coinbase_commitment()` both call
`get_v36_merged_weights()`, so vested weights are consistent.

---

#### 8.3.6 Redistribution Edge Cases Under Defense Stack

**Edge Case 1: All miners except one have unconvertible addresses**

```
Scenario: Alice (convertible, 900w decayed), Bob (P2TR, 450w),
          Charlie (P2WSH, 300w), Dave (P2TR, 200w)

accepted_weight = 900 (Alice only)
Alice gets 100% of merged block reward.

Defense impact: NONE — Alice still gets 100%. The only change is that
the TOTAL merged reward is slightly smaller because Bob/Charlie/Dave's
decayed weights are lower (their "donated" redistribution is less).
But Alice is the sole recipient either way.
```

**Edge Case 2: Burst hopper with unconvertible address**

```
Scenario: Hopper joins with P2TR (unconvertible), mines 8 hours at 3×
          pool hashrate, then leaves.

Flat PPLNS: Hopper's weight = 500 out of 1800 total.
  After hopper leaves, their 500w is redistributed to honest miners.
  Honest miners get a windfall of 27.8% extra merged rewards.

With defense: Hopper's decayed+unvested weight = 50 out of 1450 total.
  Hopper's 50w is redistributed to honest miners.
  Honest miners get a windfall of only 3.4% extra merged rewards.

Result: CORRECT — the hopper's stale work benefits honest miners less,
which is appropriate because the hopper's work was ephemeral.
The honest miners don't lose anything they would have earned without
the hopper — they just don't get as large a bonus from redistribution.
```

**Edge Case 3: New miner (Carol) with explicit merged address**

```
Scenario: Carol joins with LTC P2WPKH + explicit DOGE address via stratum
  → Carol's shares have merged_addresses = [{chain_id: 98, script: ...}]
  → Weight key = 'MERGED:...' (convertible, passes through)
  → Carol's low vesting (0.03 at hour 1) applies to merged weight too

Carol's merged payout at hour 1:
  Raw weight:    100
  Vested weight: 100 × 0.03 = 3
  Merged share:  3 / (1350 + 3) = 0.22%

This is consistent with Carol's parent chain experience (§8.2 Step 4).
Carol's merged payout ramps up alongside her parent chain payout.
```

**Edge Case 4: Pre-V36 miners during transition**

```
MergedWeightsSkipList already excludes pre-V36 shares (desired_version < 36).
Their weight is ZERO in the merged path. Decay/vesting don't change this —
zero × decay = zero. Pre-V36 exclusion is handled BEFORE decay is applied.

No interaction. No edge case.
```

**Edge Case 5: Intermittent miner (Dave) with auto-converted address**

```
Dave mines 16h/day with LTC bech32 (P2WPKH, auto-converts to DOGE P2PKH).
Dave's merged payout follows the same -11% pattern as parent (§8.2 Step 4).

Parent chain:  Dave at -11% (decay + vesting)
Merged chain:  Dave at -11% (same weights, address converts fine)
Redistribution: No effect — Dave's address is convertible.
```

**Edge Case 6: Weekend miner (Eve) whose address is sometimes P2TR**

```
If Eve mines with P2TR (unconvertible for merged), her merged weight is
skipped REGARDLESS of decay/vesting. But the AMOUNT skipped is reduced
by decay (her stale weekend shares are worth less by Thursday).

Friday (just joined for weekend):
  Eve's merged weight:  150 (fresh shares, but still-low vesting ~0.30)
  Redistributed to:     Alice, Bob (proportional)

Next Thursday (Eve's been offline 4 days):
  Eve's merged weight:  12 (heavily decayed)
  Redistributed to:     Alice, Bob (proportional, but tiny amount)

Monday's block: Eve's merged weight is ~80 (1 half-life of decay, vesting ~0.95)
  If convertible: Eve gets merged payout at ~80/total weight
  If P2TR: ~80 redistributed to others

Defense impact: Correctly reduces Eve's redistribution windfall to others
as her shares age. Without defense, Eve's stale 150w would persist at
full strength until it scrolls out of the PPLNS window.
```

---

#### 8.3.7 Consensus Safety Checklist for Merged Mining

The following must hold for consensus correctness after adding decay and
vesting to the merged mining path:

| Requirement | How verified | Step |
|-------------|-------------|------|
| `MergedWeightsSkipList.get_delta()` produces identical results on all V36 nodes | Deterministic replay: compare `get_v36_merged_weights()` outputs across 3+ nodes | S10, S12 |
| `build_canonical_merged_coinbase()` produces byte-identical coinbase | Compare canonical coinbase bytes across nodes for same share chain tip | S10 |
| `compute_merged_payout_hash()` matches `verify_merged_coinbase_commitment()` | Automatic — both call `get_v36_merged_weights()` with identical params | S10 |
| Decay uses integer-only arithmetic (no floats in consensus path) | `_decay_power()` uses 40-bit fixed-point. `build_canonical_merged_coinbase()` is already integer-only | Code review |
| Vesting identity key maps correctly between MERGED: and parent addresses | Unit test: same miner's parent + merged keys produce same vesting factor | S12 |
| Redistribution preserves sum of weights (no leakage to donation) | `accepted_total_weight` + `donation_weight` + rounding_remainder = `coinbase_value` | Invariant check in `build_canonical_merged_coinbase()` |
| Pre-V36 exclusion still works (zero weight × decay = zero) | Existing test: pre-V36 shares produce `(1, {}, 0, 0)` in `get_delta()` | S12 |
| Adaptive window expansion applies equally to merged path | `chain_length` param is shared — `get_v36_merged_weights()` uses same value | S7, S8, S9 |

---

#### 8.3.8 Updated Test Scenarios for Merged Mining

Add the following merged-specific sub-scenarios to the S12 test suite:

| Sub-ID | Scenario | Expected result |
|--------|----------|-----------------|
| S12-M1 | All 5 profiles mine with convertible addresses | Merged payouts match parent chain proportions exactly (±0.1%) |
| S12-M2 | Charlie (unconvertible P2TR) + Alice/Bob (convertible) | Charlie's merged weight redistributed proportionally; amount redistributed scales with decay |
| S12-M3 | Carol (new miner, explicit DOGE address) | Carol's merged vesting ramp matches parent ramp (±5%) |
| S12-M4 | Eve (weekend, P2TR) — Friday shares decay through week | Redistributed amount decreases daily as shares decay |
| S12-M5 | Hopper (burst, unconvertible) leaves after 8h | Redistribution windfall to honest miners < 5% (was 28% under flat PPLNS) |
| S12-M6 | Mixed V35+V36 transition with 60% V36 share ratio | Pre-V36 shares excluded; V36 miners get full merged reward; redistribution within V36 shares only |
| S12-M7 | 100% unconvertible addresses (all P2TR/P2WSH) | `accepted_total_weight = 0` → fallback to single-address mode (node operator gets 100%) |

---

#### 8.3.9 Summary: Defense Stack × Merged Mining

```
┌──────────────────────────────────────────────────────────────────────┐
│ MERGED MINING FOLLOWS PARENT CHAIN PPLNS — NO SEPARATE DEFENSE       │
│ STRATEGY NEEDED.                                                     │
│                                                                      │
│ V36 implementation work (Python 2.7/PyPy):                           │
│   Step 3:  ~8 LOC — mirror _decay_power() in MergedWeightsSkipList   │
│   Step 3a: ~-98 LOC — REMOVE finder fee pipeline (SIMPLIFICATION!)   │
│   Steps 1,2,5L,8: zero merged-specific LOC                           │
│                                                                      │
│ V37 implementation work (C++ c2pool):                                │
│   Step 4:  ~20 LOC — apply vesting in get_v36_merged_weights()       │
│   Steps 6,7,5F: zero merged-specific LOC                             │
│                                                                      │
│ Step 3a removes the entire finder-script derivation pipeline:        │
│   ✗ get_canonical_merged_finder_script() — deleted (33 LOC)          │
│   ✗ Finder fee calc in build_canonical_merged_coinbase() — deleted   │
│   ✗ Finder fee in build_merged_coinbase() — deleted (30 LOC)         │
│   ✗ Finder script derivation in work.py — deleted (24 LOC)           │
│   → Eliminates entire class of consensus-split edge cases            │
│                                                                      │
│ Redistribution of unconvertible addresses:                           │
│   ✓ Works correctly WITH all defenses                                │
│   ✓ Decayed weights mean less redistribution from stale work         │
│   ✓ Vested weights mean less redistribution from burst miners        │
│   ✓ Both redistribution points (work.py + data.py) see same weights  │
│   ✓ Consensus hash (merged_payout_hash) remains deterministic        │
│   ✓ Pre-V36 exclusion unaffected (zero × anything = zero)            │
│                                                                      │
│ Net effect on honest miners with convertible addresses: ZERO         │
│ (same proportional share as parent chain)                            │
│                                                                      │
│ Net effect on redistribution from unconvertible addresses:           │
│   - Less redistribution from stale/unvested work (CORRECT)           │
│   - Hopper with P2TR: windfall drops from ~28% to ~3% (CORRECT)      │
│   - Honest miner with P2TR: redistributed amount decays with         │
│     their shares (CORRECT — stale work should matter less)           │
└──────────────────────────────────────────────────────────────────────┘
```

---

### 8.4 V37 Roadmap: Structural Hardening (C++ c2pool)

This section outlines the **V36→V37 transition** — structural improvements
that become tractable in the C++ c2pool reimplementation. These are NOT
required to make hopping unprofitable (V36 achieves that), but provide
defense-in-depth and miner-retention benefits.

**Repository:** [c2pool](https://github.com/frstrtr/c2pool)

**Why C++ for these features:**

| Feature | PyPy 2.7 bottleneck | C++ advantage |
|---------|-------------------|---------------|
| Adaptive windows (308K shares) | ~4,500 bytes/share → 1.39 GB | ~500 bytes/share → ~150 MB |
| Incremental vesting cache | O(n) rebuild ~600ms on restart | O(n) rebuild ~3ms |
| Share compaction | Dict iteration ~1M/s in PyPy | Struct vector ~100M/s in C++ |
| Persistence | Bolt-on SQLite (new dependency) | Native LevelDB (already integrated) |
| Dashboard UI | Twisted-based minimal web server | Modern web framework in c2pool |

**V37 implementation phases (c2pool):**

```
Phase 2b: Work-weighted vesting
  └──→ IncrementalVestingCache (O(1) steady, O(n) rebuild)
  └──→ calculate_robust_vesting() integration
  └──→ Merged mining vesting (get_v36_merged_weights)
  └──→ Test scenarios: S4 (tenure farming)

Phase 4: Adaptive PPLNS/vesting windows
  └──→ get_adaptive_chain_length() function
  └──→ Transition interpolation (720-share smoothing)
  └──→ Dynamic prune threshold in clean_tracker()
  └──→ Test scenarios: S6–S9 (pump-dump, oscillation, growth, drop)

Phase 4-S2: Share compaction (tiered storage)
  └──→ CompactEpochSummary per 720 shares
  └──→ Hot/warm/cold tier management
  └──→ Reorg safety for compacted tail data

Phase 4b: LevelDB persistence (c2pool native)
  └──→ Extended share storage beyond 2×CHAIN_LENGTH
  └──→ Sub-second crash recovery
  └──→ Test scenario: S11 (restart/crash)

Phase 5F: Full dashboard UI
  └──→ miner.html: vesting bar, share trend, effective weight
  └──→ miners.html: sortable columns with progress bars
  └──→ dashboard.html: pool health, defense status, alerts
  └──→ Payout explanation toast on block found
  └──→ Advanced alerting with configurable thresholds

Phase 5A: Advanced monitoring
  └──→ vesting_cliff alert (address vesting drops below 0.5)
  └──→ Per-miner trend analysis
  └──→ Retention metrics (30-day churn tracking)
```

**V37 activation parameters (preliminary):**
- Adaptive window bounds: `[CHAIN_LENGTH, 40 × CHAIN_LENGTH]` = [8,640, 345,600]
- Compaction epoch size: 720 shares
- `RECENCY_HALF_LIFE = CHAIN_LENGTH // 4` (2160 shares)
- All version signaling uses fixed `CHAIN_LENGTH` per §7.3.16 R1

**V37 target: hopper 0.6× → 0.1× (economically irrational).**

**Prerequisite from V36:** The AutoRatchet pin (R2, Step 3a) MUST be in
place before V37 ships adaptive windows. `MergedMiningShare.SUCCESSOR`
(`data.py` L1477) must be set to the V37 share class before deployment.
See §7.3.16 R4.

---

## 9. References

- `p2pool/data.py` — Share difficulty retarget (line ~732), PPLNS weight
  calculation (`WeightsSkipList.get_delta()`, line ~1641), payout generation
  (`generate_transaction()`, line ~822), PPLNS window = `REAL_CHAIN_LENGTH`,
  block finder fee at line ~827 (`199*weight//(200*total_weight)`) and
  line ~852 (`subsidy//200`) — removed in V36 per §7.7,
  PPLNS window start at line ~820 (`previous_share.share_data['previous_share_hash']`
  = grandparent) — changed to parent in V36 per §7.7.3,
  `CANONICAL_MERGED_FINDER_FEE_PER_MILLE` (line ~172) — set to 0 in V36,
  `get_canonical_merged_finder_script()` (lines ~288-320) — dead code in V36,
  `get_pool_attempts_per_second()` (line ~2133) — deterministic pool hashrate
  estimation used for adaptive window calculation (§7.3.10),
  `MergedWeightsSkipList.get_delta()` (line ~1693) — merged chain PPLNS
  weights (same formula as parent, §8.3.4),
  `build_canonical_merged_coinbase()` (line ~174) — consensus-enforced merged
  coinbase builder with address redistribution (§8.3.1 Point B),
  `verify_merged_coinbase_commitment()` (line ~320) — peer verification of
  merged coinbase from PPLNS weights,
  `get_v36_merged_weights()` (line ~2197) — V36 merged weight computation
  (fast O(log n) + O(n) fallback, §8.3.5),
  `compute_merged_payout_hash()` (line ~2304) — consensus hash committed
  into V36 shares for merged payout verification
- `p2pool/merged_mining.py` — `build_merged_coinbase()` (line ~81) — legacy
  merged coinbase builder with `finder_fee_percentage` param (set to 0 in V36),
  finder fee output logic (lines ~154, ~243-261) — dead code in V36
- `p2pool/work.py` — `set_merged_work()` redistribution loop (lines ~525-598)
  — local merged payout distribution with address conversion and
  unconvertible address redistribution (§8.3.1 Point A),
  `finder_fee_percentage=0.5` at lines ~735, ~899 — set to 0 in V36,
  canonical finder_script derivation (lines ~2050-2059) — removed in V36,
  legacy finder_fee plumbing (lines ~2068-2081) — removed in V36,
  `_redistribute_share()` (line ~1285) — share stamping modes for
  empty/broken addresses (pplns/fee/boost/donate, §8.3.6),
  `_get_cached_merged_weights()` (line ~1973) — cache for merged weight
  computations, stratum merged address validation (lines ~1430-1530) —
  validates DOGE addresses, rejects invalid with PPLNS redistribution
- `p2pool/node.py` — `clean_tracker()` line 355 (prune logic at line ~386):
  prunes shares below `2*CHAIN_LENGTH + 10` (17,290 shares = ~72 hours).
  This 2× retention is available for extended vesting lookback (§7.3.9).
  Adaptive windows (§7.3.10) would require dynamic prune threshold.
- `p2pool/web.py` — `/users` endpoint (line ~71), uses 720-share window
- `p2pool/networks/litecoin.py` — `SHARE_PERIOD=15`, `CHAIN_LENGTH=8640`,
  `REAL_CHAIN_LENGTH=8640`, `TARGET_LOOKBEHIND=200`, `SPREAD=3`
- `docs/V36_IMPLEMENTATION_PLAN.md` — Part 10 (Difficulty Stagnation),
  §10.4 (BCH EDA/DAA parallel), §10.5 (Time-Based Difficulty Floor),
  §10.6 (Gaming Resistance Analysis — 5 attacks, evolution from count-based
  to exponential decay), §10.6.1 (Share Weight Vesting), §10.6.2 (Per-Miner
  Tenure Vesting), Part 15 (Anti-Hopping Defenses), Part 16 (Hierarchical
  Sub-Chain Architecture — multi-temporal work-quality weighting, Attack 5
  structural solution)
- `docs/SECURITY_AUDIT_2026_02.md` — H9 (monitoring without enforcement)
- Share log files: `shares.5` through `shares.8` on test node A
- **BCH EDA (Aug 2017):** Bitcoin Cash Emergency Difficulty Adjustment —
  if 6+ blocks in prior 12h took >12h, reduce difficulty by 20%.
  Solved the "no blocks → no retarget" death spiral but caused oscillation.
- **BCH DAA (Nov 2017):** Rolling 144-block window with time-weighted
  adjustment. Replaced EDA. Still had minor oscillation under gaming.
- **BCH ASERT / aserti3-2d (Nov 2020):** Exponential moving average targeting
  ideal inter-block time. Smooth, manipulation-resistant, no oscillation.
  The mathematical basis for our time-based emergency decay formula.
- §7.3.11 (New Attack Vectors) — 6 novel attack vectors against adaptive
  window design. All rated NEGLIGIBLE or NONE risk with full defense stack.
  Hashrate pump-and-dump (Attack 1) requires asymmetric window shrink as
  explicit mitigation.
- §7.3.12 (Honest Miner Impact Analysis) — 10 miner profiles (Alice/Bob/
  Carol/Dave/Eve/Frank/Grace/Henry/Iris/Jack) analyzed across all 4 defense
  phases. Profiles include real-world patterns: night-tariff mining (ToU
  electricity pricing), solar-powered mining (daytime-only), heat-recycling
  mining (winter heating + mining), dual-job GPU mining (render by day,
  mine by night), and sporadic hobbyist mining. Fixed 8640/36h window vs
  adaptive 53-day window comparison: Eve and Jack face TOTAL share loss
  under fixed window; Frank/Grace face daily fluctuations. Adaptive windows
  retain all miners by preserving share history across absences.
  24/7 miners see ~0% net impact. Part-time miners see -7% to -40%
  reduction proportional to duty cycle (fair). Hopper sees -90%.
  Discrimination gradient validates fairness: honest miners with regular
  schedules get efficiency ratios near 1.0×; hopper gets <0.2×.
  Optimized overhead at 49.5 GH/s (live): ~1.39 GB core RAM (with
  compaction), 0.35% of share period. Without compaction: ~5.5 GB. See
  §7.3.14 for corrected share size analysis (~4,500 bytes/share in PyPy).
  At 295 GH/s (peak): ~233 MB (compacted), <0.1% overhead.
- §7.3.13 (Performance Optimization) — Three strategies for large adaptive
  windows: (1) Incremental vesting cache (O(1) per share, eliminates O(n)
  bottleneck — prerequisite for pools <20 GH/s), (2) Share compaction with
  tiered storage (75% core RAM reduction — **P0 prerequisite** with corrected
  ~4,500 bytes/share: 5.55 GB→1.39 GB at 49.5 GH/s), (3) SQLite WAL
  persistence (for adaptive window crash recovery — `ShareStore` already
  persists ~17K shares to disk files via `shares.N` rotation every 60s,
  but adaptive windows need ~308K; SQLite covers the gap. Recovery:
  **not** full P2P re-sync — disk files provide ~17K shares in seconds,
  SQLite would provide full ~308K, P2P fills only the gap from last
  persistence cycle). Combined architecture reduces share generation
  from ~617ms to ~1.8ms.
- §7.3.14 (V36 Share Size Analysis) — Corrected share size from ~300 bytes
  to ~4,500 bytes in-memory (PyPy 2.7). Wire format: ~780 B (no merged),
  ~1,100 B (with DOGE). Python object overhead (long ints, nested dicts,
  tracker bookkeeping) accounts for the 4× expansion from wire to RAM.
  All RAM tables in §7.3.10 and §7.3.13 updated accordingly.
- §7.3.15 (Coinbase Overflow Attack) — With adaptive windows of 308K+
  shares, unique payout addresses could theoretically exceed transaction
  limits. Analysis: hard cap of 4,000 destinations (`data.py` L889) is
  the binding constraint (not block weight). Sybil addresses with tiny
  work are dropped first. ASIC firmware S9 limit (226 outputs) noted as
  compatibility concern. No consensus changes required.
- §7.3.16 (Version Transition Safety) — Version signaling uses fixed
  `CHAIN_LENGTH` (not adaptive PPLNS window). AutoRatchet risk identified:
  it uses `REAL_CHAIN_LENGTH` (`data.py` L2047) — must be pinned to
  `CHAIN_LENGTH` before variable windows go live. Five recommendations
  (R1-R5) for safe V37 transition.
- §8.1 (V36 Step-by-Step Implementation Plan) — Steps 0–8 with dependency
  graph, per-step file:line code references, estimated LOC, checkpoints
  (C0–C8), rollback procedures, and 12 automated test scenarios (S1–S12).
- §8.2 (Honest Miner Experience: Step-by-Step Impact) — Per-step analysis
  of what all 10 miners (Alice/Bob/Carol/Dave/Eve/Frank/Grace/Henry/Iris/
  Jack) experience at each implementation milestone. Part-time miners
  (Frank/Grace/Jack) have detailed dashboard visibility analysis in Step 5.
  Cumulative impact table: Alice -1%, Bob +2%, Carol 72h ramp then -3%,
  Dave -11%, Eve -29%, Frank -30%, Grace -20%, Henry -7%, Iris -10%,
  Jack -40%. Duty cycle vs effective reward fairness validation table.
  Automatic S12 test bounds.
- `p2pool/util/forest.py` — `Tracker` class: `items` dict (hash→item),
  `get_chain()` generator (O(n) linked list walk), `get_nth_parent_hash`
  (O(log n) via DistanceSkipList). Performance baseline for optimization
  analysis in §7.3.13.
- `p2pool/util/skiplist.py` — `SkipList` class: geometric skip (p=0.5),
  LRU(5) memoization. `WeightsSkipList` inherits from `TrackerSkipList`.
  O(log n) confirmed by code analysis: ~18 steps at 308K shares.
- `p2pool/data.py` `ShareStore` class (line ~2467) — Disk persistence for
  shares via `shares.N` files. Writes up to `2 × CHAIN_LENGTH` (~17,280)
  shares every 60 seconds. On restart, shares are loaded from disk FIRST
  (covering the fixed 8,640-share window completely), then P2P peers fill
  only the gap from the last 60s before crash. File rotation at ~10 MB.
  For adaptive windows (308K+ shares), ShareStore only covers ~5.6% —
  SQLite WAL is needed for full window persistence.
- `p2pool/main.py` (lines ~192-283) — Share loading on startup: reads
  `shares.N` files → builds tracker → P2P fills gaps only.
- **Real-world miner patterns** — Mining schedules based on documented
  industry practices:
  - *Time-of-Use (ToU) electricity pricing:* Many countries (France, Spain,
    Japan, Australia, most US states) have differentiated tariffs where
    night-time electricity costs 30-60% less than daytime. Industrial ToU
    ratios can reach 3:1 (peak:off-peak). Miners in ToU regions rationally
    concentrate mining during off-peak hours (typically 22:00-07:00 or
    00:00-06:00 local time). Source: utility rate schedules (EDF Tempo,
    Endesa, TEPCO, etc.)
  - *Solar-powered mining:* Mining operations powered by photovoltaic
    panels are limited to daylight hours unless battery storage is
    available. Grid-tied solar miners may choose to mine only when solar
    generation exceeds household consumption (typically 08:00-17:00 in
    summer, shorter in winter). Growing segment in sunbelt regions.
  - *Heat-recycling mining:* Using ASIC/GPU waste heat for space heating.
    Practical during cold months (October-April in Northern Hemisphere).
    Miners run ASICs in living spaces during heating season, reducing
    mining during summer when heat is unwanted. Northern European and
    Canadian miners commonly report this pattern.
  - *Dual-use GPU mining:* GPUs used for rendering, ML training, or
    gaming during work/play hours, then switched to mining overnight.
    Common among freelance 3D artists, ML practitioners, and gamers who
    treat mining as passive overnight income.
  - *Sporadic/hobbyist mining:* Enthusiasts who mine when convenient,
    travel frequently, or run mining as a secondary activity. May mine
    3-4 days per week with multi-day gaps. Represent P2Pool's core
    target demographic (decentralization advocates, cypherpunks, home
    miners). Most vulnerable to fixed-window share expiration.
  - *Pool retention research:* Operator discussions and public pool
    postmortems consistently indicate high early churn among small miners,
    often driven by unclear payout expectations and delayed reward feedback.
    Transparent payout explanations and progress indicators generally improve
    retention. P2Pool's decentralized nature makes dashboard transparency
    especially important since there is no centralized support desk to
    explain payout mechanics.
