# V36 AutoRatchet Transition Test Results

**Date:** 2026-03-03  
**Duration:** ~3.5 hours (19:30–23:11 UTC)  
**Environment:** 4-node LTC testnet cluster with CPU mining  

## Test Infrastructure

| Node | IP | Role | Software | Address |
|------|----|------|----------|---------|
| node29 | 192.168.86.29 | V36 primary | p2pool-merged (V36) | mn79n2WvPXZJRYrkszYb3umrZ5dEPtcCsm |
| node30 | 192.168.86.30 | V35 peer | p2pool (V35) | tltc1q98qmmw559wlpeecgxuzfjge98dljjxnsamltav |
| node31 | 192.168.86.31 | V36 secondary | p2pool-merged (V36) | (not used this test) |
| node33 | 192.168.86.33 | V35 peer | p2pool (V35) | tltc1qz8lpthuhdvt9vjc7rxnr2u9gxg2py7hue6ddck |

**Mining:** CPU miner on 192.168.86.24, ~120kH/s, targeting node29 (V36) and nodes 30/33 (V35)  
**Testnet Parameters:** CHAIN_LENGTH=400, SHARE_PERIOD=4s, SANE_TARGET_RANGE max=2^256//500000

## Test Phases & Results

### Phase 1: Initial Activation ✅
**Objective:** V36 reaches 95% vote threshold with full 400-share window  
**Time:** 19:38:52 UTC  
**Result:**
```
[AutoRatchet] VOTING -> ACTIVATED (100% of 400 shares vote V36, window=400)
```
- Node29 ran in isolation (no peers)
- All shares produced by V36 miner → 100% vote
- State persisted: `{"state": "activated", "activated_at": ..., "activated_height": 400}`

### Phase 2: Fallback Deactivation ✅
**Objective:** Vote drops below 50% → ACTIVATED reverts to VOTING  
**Time:** 19:59:58 UTC  
**Method:** `force_v35_test` flag file triggers V35 non-voting share production in ACTIVATED state  
**Result:**
```
[AutoRatchet] ACTIVATED -> VOTING (0% votes < 50% threshold)
[AutoRatchet] Reset MINIMUM_PROTOCOL_VERSION 3500 -> 3301 (allow V35 peers)
```
- Deactivation correctly reset MINIMUM_PROTOCOL_VERSION to allow V35 peers
- V35 shares (PaddingBugfixShare) correctly followed V36 shares (MergedMiningShare) after Bug #3 fix
- State persisted: `{"state": "voting", "activated_at": null, "activated_height": null}`

### Phase 3: Re-activation ✅
**Objective:** After deactivation, vote climbs back to 95% for re-activation  
**Time:** 21:11:11 UTC  
**Challenge:** V35 node33 reconnected (MINIMUM_PROTOCOL_VERSION reset allowed it) and produced V35 shares diluting the vote to 59%  
**Resolution:** Killed V35 processes, V35 shares aged out of window  
**Result:**
```
[AutoRatchet] VOTING -> ACTIVATED (95% of 400 shares vote V36, window=400)
```
- Vote climbed: 59% → 64% → 80% → 95% as V35 shares aged out
- Re-activation confirmed at height 1447

### Phase 4: Confirmation (800 shares) ✅
**Objective:** Sustained 95%+ V36 for 2×CHAIN_LENGTH=800 shares → CONFIRMED  
**Time:** 22:53:21 UTC  
**Bug Found:** Bug #4 (see below) - confirmation counter was height-based and reset by tracker pruning  
**Result (after Bug #4 fix):**
```
[AutoRatchet] ACTIVATED -> CONFIRMED (800 cumulative shares since activation, 100% V36, window=800)
```
- Monotonic counter: 0 → 274 (15min) → 531 (30min) → 742 (45min) → 800 (48min)
- State persisted: `{"state": "confirmed", "confirmed_at": 1772578401, "confirm_count": 800}`

### Phase 5: CONFIRMED Network Reversal ✅
**Objective:** CONFIRMED state follows network consensus when V35 majority emerges  
**Time:** 23:10:50 UTC  
**Method:** `force_v35_test` flag in CONFIRMED state produces V35 non-voting shares  
**Result:**
```
[AutoRatchet] WARNING: CONFIRMED but network is 51% V35 - following network consensus
[AutoRatchet] CONFIRMED: vote=49% (199/400) share=45% full=True
```
- CONFIRMED state is **permanent** (does not revert to VOTING)
- But follows network consensus: returns PaddingBugfixShare(desired_version=36)
- Still votes V36 to help network re-establish V36 majority

## Bugs Discovered & Fixed

### Bug #1: Protocol Version Ratchet Guard (Prior Session)
**Symptom:** `update_min_protocol_version()` bumped MINIMUM_PROTOCOL_VERSION to 3503 at activation, permanently banning V35 peers  
**Root Cause:** No guard to check if AutoRatchet had reached CONFIRMED state before raising version  
**Fix:** Guard in `update_min_protocol_version()`: only bump to ≥3503 when `_auto_ratchet._state == 'confirmed'`  
**File:** `p2pool/data.py` lines ~2082-2087

### Bug #2: v36_active/GENTX Mismatch (Prior Session)
**Symptom:** `is_v36_active()` returned True from old chain data while AutoRatchet selected V35 format  
**Root Cause:** `v36_active` flag not aligned with AutoRatchet's share type decision  
**Fix:** Override `v36_active` based on AutoRatchet output: `v36_active = (share_type.VERSION >= 36)`  
**File:** `p2pool/work.py` line ~2201

### Bug #3: Share Downgrade Transition
**Symptom:** `PaddingBugfixShare can't follow MergedMiningShare` — deactivation impossible  
**Root Cause:** Share `check()` method only allowed SUCCESSOR (upgrade) transitions, not downgrade  
**Fix:** Added downgrade check: `elif type(previous_share) is type(self).SUCCESSOR: pass`  
**File:** `p2pool/data.py` line ~1307

### Bug #4: Confirmation Counter vs Tracker Pruning
**Symptom:** Confirmation counter oscillated (0→253→46→77→46...), never reaching 800  
**Root Cause:** Counter used `height - activated_height`, but tracker prunes chains to `2*CHAIN_LENGTH+10 = 810` shares max. Since confirmation needs 800 shares, the counter could never sustain growth — pruning would drop height back, triggering a stale height reset.  
**Fix:** Replaced height-based counter with monotonic `_confirm_count` that increments by `delta = height - last_seen_height` when height increases. Persisted to state file. Confirmation now survives tracker pruning cycles.  
**File:** `p2pool/data.py` — `__init__`, `_load`, `_save`, `get_share_version` ACTIVATED state

## AutoRatchet State Machine (Verified)

```
VOTING ──(95% vote, full window)──> ACTIVATED ──(800 shares, 95% share)──> CONFIRMED
  ^                                    │                                       │
  │                                    │                                       │
  └────(<50% vote)─────────────────────┘                                       │
                                                                               │
  Returns PaddingBugfixShare(desired_version=36) <──(<50% network consensus)───┘
  (CONFIRMED permanent, but follows V35 if network reverses)
```

## Design Insights

1. **V35↔V36 P2P Incompatibility:** V35 nodes cannot parse MergedMiningShare and fork off when V36 activates. Fallback mechanism requires V36 nodes to produce V35 shares internally (via AutoRatchet deactivation), not from V35 peers.

2. **Tracker Pruning Impact:** The tracker keeps at most `2*CHAIN_LENGTH+10` shares per chain. Any confirmation or counting mechanism must not depend on tracker height exceeding this limit.

3. **CONFIRMED Permanence:** CONFIRMED state survives restarts and never reverts. It follows network consensus (produces V35 format when V35 majority) but continues voting V36 to help re-establish V36 majority.

4. **MINIMUM_PROTOCOL_VERSION Management:** Reset to 3301 on deactivation to allow V35 peers to reconnect. Only bumped to 3503 upon CONFIRMED state.

## Files Modified

| File | Changes |
|------|---------|
| `p2pool/data.py` | Bug #1 (protocol version guard), Bug #3 (downgrade transition), Bug #4 (monotonic counter), test mode, MINIMUM_PROTOCOL_VERSION reset |
| `p2pool/work.py` | AutoRatchet exposure to network, v36_active alignment |
| `p2pool/bitcoin/networks/litecoin_testnet.py` | SANE_TARGET_RANGE tuning for CPU mining |
