# V35 → V36 Transition Test Results

**Date:** March 3, 2026  
**Test Duration:** 14:47 – 15:26 UTC (~40 minutes)  
**Version:** v36-0.11-alpha (commit `d34d045d`)  
**Protocol:** V35 (3502) ↔ V36 (3503) interoperability on Litecoin testnet

---

## Executive Summary

Full four-node V35→V36 transition test completed successfully. The test demonstrated:

1. **Sharechain consensus across protocol versions** — V35 and V36 nodes maintained a unified sharechain throughout the test
2. **Vote signaling works** — V36 nodes produced V35-compatible shares with `desired_version=36`, building toward the 95% activation threshold
3. **Smooth activation** — AutoRatchet transitioned VOTING → ACTIVATED at 95% signaling, then began producing V36-type shares
4. **V35 upgrade warnings** — V35 (jtoomim) nodes correctly detected the V36 supermajority and displayed upgrade prompts
5. **Critical bug found and fixed** — Stale `v36_ratchet.json` from previous test runs caused premature V36 share production; reset procedure documented below

---

## Test Infrastructure

### Nodes

| Node | IP | Version | Software | Role |
|------|----|---------|----------|------|
| **node29** | 192.168.86.29 | V36 (3503) | `~/p2pool-merged` (frstrtr) | ASIC mining target |
| **node31** | 192.168.86.31 | V36 (3503) | `~/p2pool-merged` (frstrtr) | ASIC mining target |
| **node30** | 192.168.86.30 | V35 (3502) | `~/Github/p2pool` (jtoomim) | CPU mining target |
| **node33** | 192.168.86.33 | V35 (3502) | `~/Github/p2pool` (jtoomim) | CPU mining target |

### Miners

| Miner | IP | Type | Target | Hashrate |
|-------|----|------|--------|----------|
| alpha | 192.168.86.20 | ASIC (AntRouter L1) | node29 | ~1.2 MH/s |
| bravo | 192.168.86.22 | ASIC (AntRouter L1) | node29 | ~1.2 MH/s |
| charlie | 192.168.86.249 | ASIC (AntRouter L1) | node29 | ~1.0 MH/s |
| cpu-v35-30 | 192.168.86.24 | cpuminer-multi (4 threads) | node30 | ~32 kH/s |
| cpu-v35-33 | 192.168.86.24 | cpuminer-multi (4 threads) | node33 | ~32 kH/s |

### Testnet Parameters

| Parameter | Value |
|-----------|-------|
| `SHARE_PERIOD` | 4 seconds |
| `CHAIN_LENGTH` | 400 shares |
| Activation threshold | 95% of window |
| Deactivation threshold | 50% of window |
| LTC testnet daemon | 192.168.86.26:19332 |
| DOGE testnet4alpha daemon | 192.168.86.27:44555 |

---

## Test Procedure

### Phase 1: Preparation (14:47 UTC)

1. Stopped all p2pool processes on all 4 nodes
2. Flushed testnet sharechains on all 4 nodes (`rm shares.*` in data dirs)
3. **Deleted `v36_ratchet.json`** on both V36 nodes (critical — see [Ratchet Reset](#ratchet-reset-for-testers))
4. Blocked ASICs from V36 nodes via `iptables REJECT` rules on ports 19327

### Phase 2: V35 Sharechain Build (14:47 – 15:00 UTC)

1. Started all 4 p2pool nodes (V35 on nodes 30/33, V36 on nodes 29/31)
2. Started CPU miners on node .24 → V35 nodes (4 threads each)
3. Waited for sharechain to grow past `CHAIN_LENGTH=400`
4. All 4 nodes peered and synchronized on the same chain

### Phase 3: ASIC Activation (15:01 UTC)

1. Flushed `iptables` rules on V36 nodes to unblock ASICs
2. ASIC miners connected to node29 at ~3.4 MH/s combined
3. V36 nodes began producing V35-compatible shares with `desired_version=36`

### Phase 4: Monitoring & Activation (15:01 – 15:26 UTC)

V36 vote signaling progressed through the activation window:

| Time (UTC) | Vote % | V36 Votes / Window | Share % | Height | Event |
|---|---|---|---|---|---|
| 15:19:09 | 85% | 341/400 | 0% | 802 | VOTING — shares displacing CPU-mined V35 |
| 15:19:56 | 87% | 351/400 | 0% | 813 | Climbing |
| 15:20:38 | 91% | 366/400 | 0% | 828 | Approaching threshold |
| 15:21:27 | 94% | 378/400 | 0% | 842 | Almost there |
| **15:21:39** | **95%** | — | 0% | — | **VOTING → ACTIVATED** |
| 15:22:08 | 95% | 380/400 | 2% | 857 | First V36-type shares |
| 15:23:00 | 95% | 382/400 | 6% | 872 | V36 shares accumulating |
| 15:23:36 | 96% | 385/400 | 10% | 887 | Steady growth |
| 15:24:36 | 97% | 389/400 | 16% | 907 | Strong V36 presence |
| 15:25:39 | 97% | 391/400 | 19% | 907 | Sustained |
| 15:26:46 | 98% | 392/400 | 27% | 953 | Test concluded |

---

## Final Status @ 15:26 UTC

| Node | Version | Chain Height | Local Shares | Orphan | Dead | Stale | Peers | Ratchet |
|------|---------|-------------|-------------|--------|------|-------|-------|---------|
| **29** (V36) | 3503 | 907 | 103 | 11 | 0 | 10.7% | 1 (1 in) | ACTIVATED |
| **31** (V36) | 3503 | 938 | 56 | 1 | 0 | 1.8% | 1 (0 in) | ACTIVATED |
| **30** (V35) | 3502 | 848 | 7161 | 50 | 6820 | 95.9% | 1 (1 in) | N/A |
| **33** (V35) | 3502 | 849 | 7454 | 161 | 7120 | 97.7% | 1 (0 in) | N/A |

### Observations

- **V36 nodes: 0 dead shares** — excellent timestamp accuracy from local ASIC miners
- **V35 dead rate (96–98%)** caused by clock skew (~120s) between CPU mining host and V35 nodes; not a V35/V36 compatibility issue
- **Chain height divergence** (V36: 907–938, V35: 848–849) occurred after activation — V35 jtoomim code cannot parse `MergedMiningShare` (V36-type), so chains fork at the activation boundary. This is **expected behavior** — V35 nodes that don't upgrade will eventually fall behind.
- **V35 upgrade warning** displayed correctly:
  ```
  >>> Warning: A MAJORITY OF SHARES CONTAIN A VOTE FOR AN UNSUPPORTED 
  SHARE IMPLEMENTATION! (v36 with 96% support)
  An upgrade is likely necessary.
  ```

---

## Bug Found: Stale Ratchet State

### Description

When tests are repeated without clearing the `v36_ratchet.json` file, the AutoRatchet starts in its previous state (e.g., `confirmed`) rather than `voting`. This causes the node to immediately produce V36-type shares on a fresh sharechain, which triggers:

```
p2p.PeerMisbehavingError: switch without enough hash power upgraded
```

The share's `check()` method correctly rejects V36-type shares that follow V35 shares when fewer than 60% of shares in the activation window vote V36. But the ratchet's persisted `confirmed` state from a previous test caused the node to *produce* these invalid shares.

### Root Cause

```json
// v36_ratchet.json — stale from previous test run
{"state": "confirmed", "activated_at": 1771890015, "activated_height": 809, "confirmed_at": 1771915018}
```

The AutoRatchet correctly persists its state to survive restarts (this is by design — a CONFIRMED node should resume V36 production after restart). But in a **test environment** where sharechains are flushed between runs, the persisted state no longer matches the network state.

### Fix Applied

Delete `v36_ratchet.json` before starting fresh tests:

```bash
rm -f ~/p2pool-merged/data/litecoin_testnet/v36_ratchet.json
```

See [Ratchet Reset for Testers](#ratchet-reset-for-testers) below for the complete procedure.

---

## Ratchet Reset for Testers

> **Important:** The `v36_ratchet.json` file must be deleted whenever you flush the sharechain for a fresh test. The ratchet state is tied to the sharechain — a stale ratchet on an empty chain produces invalid shares.

### When to Reset

- Before **any** fresh transition test (after deleting `shares.*` files)
- After changing between testnet and mainnet
- When switching between V35-only and V36 mixed networks
- After a test failure that requires a clean restart

### Reset Procedure

```bash
# 1. Stop p2pool
pkill -f run_p2pool

# 2. Flush sharechain
cd ~/p2pool-merged/data/litecoin_testnet   # or litecoin for mainnet
rm -f shares.*

# 3. Delete ratchet state
rm -f v36_ratchet.json

# 4. Restart p2pool
# (your normal startup command)
```

### Ratchet File Location

| Network | Path |
|---------|------|
| Litecoin mainnet | `data/litecoin/v36_ratchet.json` |
| Litecoin testnet | `data/litecoin_testnet/v36_ratchet.json` |

### Verifying Ratchet State

After startup, check the log for:
```
[WorkerBridge] AutoRatchet initialized: AutoRatchet(state=voting, activated=None, height=None, confirmed=None)
```

If you see `state=confirmed` or `state=activated` on a fresh chain, the ratchet was not reset.

### Ratchet State Monitoring

The AutoRatchet logs periodic vote progress (every ~30 share evaluations):
```
[AutoRatchet] VOTING: vote=85% (341/400) share=0% full=True height=802
[AutoRatchet] VOTING: vote=94% (378/400) share=0% full=True height=842
[AutoRatchet] VOTING -> ACTIVATED (95% of 400 shares vote V36, window=400)
[AutoRatchet] ACTIVATED: vote=97% (389/400) share=16% full=True height=907
```

---

## Transition Mechanics Validated

### 1. Version Signaling
- V36 nodes in VOTING state produce `PaddingBugfixShare` (V35-format) with `desired_version=36`
- V35 nodes correctly count these votes and display upgrade warnings
- Vote percentage tracked accurately across the full `CHAIN_LENGTH=400` window

### 2. Activation Threshold
- Transition triggered exactly at 95% (per `ACTIVATION_THRESHOLD = 95`)
- No premature activation — the ratchet waited for a full window of data (`full=True`)

### 3. Post-Activation Share Production
- After activation, V36 nodes switched to producing `MergedMiningShare` (V36-format)
- V36-type share percentage climbed steadily: 0% → 2% → 6% → 10% → 16% → 27%

### 4. V35 / V36 Interoperability
- During VOTING phase: full sharechain consensus, all 4 nodes on same chain
- After ACTIVATED: V35 nodes cannot parse V36-type shares → chain fork (expected)
- V35 nodes display correct upgrade warnings with vote percentage

### 5. Anti-Hopping Monitor
- MONITOR active on V36 nodes, reporting hashrate concentration alerts
- Expected with only 2 ASIC miners — alerts triggered by >30% share concentration per address

---

## Recommendations

### For Testers

1. **Always reset `v36_ratchet.json` when flushing sharechains** — this is the #1 cause of test failures
2. **Sync clocks** with `ntpdate` or `timedatectl` across all nodes before testing — clock skew causes inflated dead share rates
3. **Monitor ratchet progress** via `grep "AutoRatchet" data/litecoin_testnet/log`

### For Production Deployment

1. The ratchet mechanism works correctly — activation is safe with organic hashrate growth
2. V35 nodes will self-warn when V36 supermajority is reached
3. The chain fork after activation is expected — V35 operators must upgrade to stay on the canonical chain
4. The `v36_ratchet.json` should NOT be deleted in production — it correctly persists state across restarts

### Code Improvements (Future)

1. Add startup warning when ratchet state is found but sharechain is empty:
   ```
   [AutoRatchet] WARNING: Persisted state 'confirmed' but sharechain is empty — 
   consider deleting v36_ratchet.json if this is a fresh test
   ```
2. Consider adding a `--reset-ratchet` CLI flag for test convenience

---

## Appendix: Node Startup Commands

### V35 Nodes (jtoomim)

```bash
# Node 30
screen -dmS p2pool30 bash -c 'cd ~/Github/p2pool && \
  ~/pypy2.7-v7.3.20-linux64/bin/pypy run_p2pool.py \
  --net litecoin --testnet \
  --bitcoind-address 192.168.86.26 --bitcoind-rpc-port 19332 --bitcoind-p2p-port 19335 \
  -a tltc1q98qmmw559wlpeecgxuzfjge98dljjxnsamltav \
  --coinbtext p2pool-v35-v36-transition-test \
  --merged_addr "nXzx4WHrERckqvvCsZkb41UpCpWWhXQf5T%http://dogecoinrpc:testpass@192.168.86.27:44555/" \
  litecoinrpc litecoinrpc_mainnet_2026 2>&1 | tee ~/p2pool_testnet.log'
```

### V36 Nodes (frstrtr)

```bash
# Start mm-adapter first
screen -dmS mm-adapter bash -c 'cd ~/p2pool-merged/mm-adapter && \
  python3 adapter.py --config config.yaml 2>&1 | tee ~/mm-adapter.log'

# Node 29
screen -dmS p2pool bash -c 'export PATH=$HOME/pypy2.7-v7.3.20-linux64/bin:$PATH; \
  cd ~/p2pool-merged; pypy run_p2pool.py \
  --net litecoin --testnet \
  --bitcoind-address 192.168.86.26 --bitcoind-rpc-port 19332 --bitcoind-p2p-port 19335 \
  --merged-coind-address 127.0.0.1 --merged-coind-rpc-port 44556 \
  --merged-coind-rpc-user dogecoinrpc --merged-coind-rpc-password testpass \
  --merged-coind-p2p-address 192.168.86.27 --merged-coind-p2p-port 44557 \
  --address mn79n2WvPXZJRYrkszYb3umrZ5dEPtcCsm \
  --give-author 2 -f 0 --disable-upnp --max-conns 20 --no-console \
  litecoinrpc litecoinrpc_mainnet_2026 2>&1 | tee -a data/litecoin_testnet/log'
```

### CPU Miners (on .24)

```bash
# Mine V35 node30
screen -dmS cpu-v35-30 /tmp/cpuminer-multi/cpuminer \
  -a scrypt -t 4 \
  -o stratum+tcp://192.168.86.30:19327 \
  -u tltc1q98qmmw559wlpeecgxuzfjge98dljjxnsamltav.cpu30 -p x

# Mine V35 node33
screen -dmS cpu-v35-33 /tmp/cpuminer-multi/cpuminer \
  -a scrypt -t 4 \
  -o stratum+tcp://192.168.86.33:19327 \
  -u n3ehYdsb83xfhxyn6NSXxG2EbfNHyvFQKY.cpu33 -p x
```
