# V36 Release Notes — P2Pool Merged Mining

> **Version:** V36 (share format version 36)
> **Lineage:** [`p2pool/p2pool`](https://github.com/p2pool/p2pool) (forrestv, 2011) → [`jtoomim/p2pool`](https://github.com/jtoomim/p2pool) (V35) → **`frstrtr/p2pool-merged-v36`** (V36)
> **Release date:** March 2026
> **Commits since V35 fork:** 834+
> **Repository:** https://github.com/frstrtr/p2pool-merged-v36

---

## Table of Contents

1. [Why V36 Exists](#1-why-v36-exists)
2. [For Miners: What Changed and Why You're Protected](#2-for-miners-what-changed-and-why-youre-protected)
3. [For Pool Operators: What You Need to Know](#3-for-pool-operators-what-you-need-to-know)
4. [Consensus Changes (V35 → V36)](#4-consensus-changes-v35--v36)
5. [Anti-Pool-Hopping Defense Stack](#5-anti-pool-hopping-defense-stack)
6. [Merged Mining (LTC + DOGE)](#6-merged-mining-ltc--doge)
7. [Dashboard & Monitoring](#7-dashboard--monitoring)
8. [Security Hardening](#8-security-hardening)
9. [Infrastructure & DevOps](#9-infrastructure--devops)
10. [Breaking Changes vs V35](#10-breaking-changes-vs-v35)
11. [Migration from jtoomim/p2pool (V35)](#11-migration-from-jtoomimp2pool-v35)
12. [What's Next: V37 and c2pool](#12-whats-next-v37-and-c2pool)
13. [Full Commit History](#13-full-commit-history)

---

## 1. Why V36 Exists

P2Pool has been the gold standard for decentralized mining since 2011.
However, two critical gaps have emerged:

1. **No merged mining.** Litecoin miners leave ~$50K+/day of Dogecoin rewards
   on the table because jtoomim/p2pool (V35) doesn't support AuxPoW merged
   mining. Centralized pools capture this value.

2. **Pool hopping is profitable.** A strategic miner ("hopper") can join
   P2Pool temporarily with high hashrate, accumulate disproportionate PPLNS
   weight during inflated difficulty, then leave — collecting rewards for
   36+ hours at zero ongoing cost. This was observed on Litecoin mainnet
   in March 2026 with a measured **3.8× reward efficiency** for the attacker.

V36 solves both problems:
- **Merged mining** adds LTC+DOGE simultaneous mining to the decentralized
  share chain, with proper PPLNS reward distribution on both chains.
- **Anti-hopping defenses** make pool hopping unprofitable (3.8× → 0.6×),
  protecting loyal miners' rewards.

> **Detailed threat analysis:** [POOL_HOPPING_ATTACKS.md](POOL_HOPPING_ATTACKS.md)
> **Future roadmap:** [FUTURE.md](FUTURE.md)

---

## 2. For Miners: What Changed and Why You're Protected

### TL;DR

| What | Before (V35) | After (V36) |
|------|-------------|-------------|
| Mining rewards | LTC only | **LTC + DOGE** simultaneously |
| Pool hopper profit | 3.8× (steals your rewards) | **0.6× (unprofitable)** |
| Address types | P2PKH only | **P2PKH, P2SH, Bech32** native outputs |
| Merged chain address | Not applicable | Embedded in share chain — correct on ALL nodes |
| Block finder bonus | 0.5% to lucky finder | **Removed** — pure proportional payouts |
| PPLNS weight | Flat (old = new) | **Exponential decay** — recent work counts more |
| Death spiral protection | None | **Emergency time-based decay** prevents pool death |

### How Pool Hopping Stole Your Rewards (Fixed)

In V35, a "hopper" with 2× the pool's hashrate could:
1. **Join for ~5 hours** — pump share difficulty, accumulate high-weight shares
2. **Leave for ~31 hours** — shares persist in the PPLNS window at inflated weight
3. **Collect rewards on every block found** during those 31 hours, at zero cost

This meant honest miners' payouts were diluted by ~20-40% while the hopper
collected 3.8× their fair share.

**V36 fixes this with three deployed defenses:**

| Defense | How it Protects You |
|---------|-------------------|
| **Exponential PPLNS decay** | Old shares lose weight over time. A hopper's 5-hour burst decays to near-zero within 18 hours. Your fresh shares always outweigh stale ones. |
| **Emergency time-based decay** | If a whale inflates difficulty and leaves, share difficulty auto-recovers within minutes instead of hours. The pool cannot be "killed" by a departing whale. |
| **Pure difficulty accounting** | The 0.5% "finder bonus" (which rewarded luck, not work) is removed. Every satoshi is distributed proportional to actual work contributed via PPLNS. |

**Result:** A hopper using the exact same attack now earns **0.6×** their
fair share — it costs them more to attack than they earn. Hopping is
economically irrational.

### Impact on Your Mining Schedule

The exponential decay aligns payout with actual contribution over time.
If you mine continuously, your payouts are essentially unchanged (~±2%).

| Your Mining Pattern | Impact | Why |
|---|---|---|
| 24/7 continuous | **~0% change** | Your stream of fresh shares replaces decaying old ones 1:1 |
| 16h on / 8h off | **~-8%** | 8-hour gap lets older shares decay; correctly reflects your 67% duty cycle |
| Weekends only (Fri–Sun) | **~-20%** | Weekend shares decay during Mon–Thu absence; correctly reflects 43% duty cycle |
| 6h/day (nights only) | **~-25%** | 18-hour daily gap; correctly reflects 25% duty cycle |
| Sporadic (3-4 days/week) | **~-35%** | Multi-day gaps; correctly reflects ~40% duty cycle |

These numbers look like penalties, but they're actually **corrections**.
V35's flat PPLNS was *over-rewarding* part-time miners relative to their
actual work contribution — at the expense of full-time miners. V36 makes
payouts proportional to real work over time. Additionally, V37's adaptive
windows (see [§12](#12-whats-next-v37-and-c2pool)) will significantly
improve payouts for part-time miners by extending the PPLNS window from
36 hours to ~53 days, so mining history persists across breaks.

### Your DOGE Rewards

With V36 merged mining, you automatically earn Dogecoin alongside Litecoin:

```
Stratum username: YOUR_LTC_ADDRESS,YOUR_DOGE_ADDRESS.worker1
```

- **Explicit DOGE address**: Embedded in every V36 share you produce — all
  nodes in the network pay you correctly
- **No DOGE address?**: P2Pool auto-converts your LTC address hash to a DOGE
  address (P2PKH→P2PKH, Bech32→P2PKH, P2SH→P2SH)
- **Wrong order?**: If you accidentally type `DOGE_ADDR,LTC_ADDR`, P2Pool
  detects and auto-corrects it

---

## 3. For Pool Operators: What You Need to Know

### Upgrade Path

1. **Stop V35 node** — `screen -S p2pool -X quit`
2. **Clone V36** — `git clone https://github.com/frstrtr/p2pool-merged-v36.git`
3. **Delete old share data** — `rm data/litecoin/shares.* data/litecoin/graph_db`
   (V36 shares are wire-incompatible with V35)
4. **Install MM-Adapter** — see [mm-adapter/README.md](../mm-adapter/README.md)
5. **Start with merged mining** — add `--merged-coind-*` flags
6. **Share chain syncs in ~2 minutes** from bootstrap peers

### New CLI Flags

| Flag | Description |
|------|-------------|
| `--merged-coind-address` | MM-Adapter RPC host (usually `127.0.0.1`) |
| `--merged-coind-rpc-port` | MM-Adapter RPC port (e.g., `44556`) |
| `--merged-coind-rpc-user` | DOGE RPC username |
| `--merged-coind-rpc-password` | DOGE RPC password |
| `--merged-coind-p2p-address` | DOGE daemon P2P address for block broadcast |
| `--merged-coind-p2p-port` | DOGE daemon P2P port |
| `--redistribute MODE` | Handle invalid-address shares: `pplns` / `fee` / `boost` / `donate` |
| `--give-author N` | Donation percentage (default: 0) |

### Monitoring

- **Dashboard**: `http://YOUR_IP:9327/static/dashboard.html` — real-time
  pool stats, transition progress, merged mining status
- **Structured logs**: `MONITOR-` prefixed lines for automated parsing
  (hashrate anomalies, difficulty spikes, top miner concentration)
- **API endpoints**: `/local_stats`, `/global_stats`, `/current_payouts`,
  `/current_merged_payouts`, `/recent_blocks`

### Version Transition

V36 activates via **95% share version signaling** with **AutoRatchet**:
- Nodes automatically signal V36 when they detect sufficient network support
- Transition stages: `waiting` → `signaling` → `activated` → `confirmed`
- The dashboard shows real-time signaling progress with version counts
- Once confirmed, the network locks into V36 permanently

> **Detailed guide:** [V36_TRANSITION_GUIDE.md](V36_TRANSITION_GUIDE.md)

---

## 4. Consensus Changes (V35 → V36)

These changes require V36 activation (95% share signaling threshold).
All changes are **backward-compatible during transition** — V35 shares
continue to be accepted until the network reaches consensus.

### 4.1 V36 Share Format

The V36 share extends the V35 `share_info` with:

| Field | Type | Purpose |
|-------|------|---------|
| `pubkey_type` | `IntType(8)` | Address type: 0=P2PKH, 1=P2SH, 2=P2WPKH |
| `merged_payout_hash` | `IntType(256)` | AuxPoW Merkle root commitment for merged chain rewards |
| `merged_addresses` | Explicit per-chain address list | Ensures all nodes know the miner's DOGE address |

The `pubkey_type` field enables native P2SH and bech32 scriptPubKey
generation in the coinbase, replacing V35's P2PKH-only output.

### 4.2 VarStrType for Hash Link Extra Data

V36 uses `VarStrType` for `hash_link.extra_data` instead of the fixed-size
`FixedStrType`. This accommodates variable-length coinbase data from
merged mining AuxPoW commitments.

### 4.3 Combined Donation Script

V36 replaces the dual donation outputs (P2PK author + secondary marker)
with a single **1-of-2 P2SH multisig** output. This:
- Reduces coinbase size (one output instead of two)
- Serves as a blockchain-visible marker for V36 blocks
- Is backward-compatible (the hash is precomputed and constant)

### 4.4 Merged Chain PPLNS

Merged chain (DOGE) rewards are distributed using the **same PPLNS consensus
mechanism** as parent chain (LTC) rewards:
- Same share weights, same exponential decay, same window
- Each miner's DOGE payout address comes from the `merged_addresses` field
  in their shares (V36) or auto-converted from their LTC address (V35 shares)
- Node operator fee (`-f`) applies to both chains proportionally

### 4.5 Exponential PPLNS Decay (Phase 2a)

Share weights in the PPLNS window now decay exponentially by depth:

```
weight(share) = base_weight × 2^(-depth / half_life)
```

Where `half_life = CHAIN_LENGTH / 4` (2160 shares on mainnet, ~9 hours).
This means:
- A share at depth 0 (just mined) has 100% weight
- A share at depth 2160 (~9 hours old) has 50% weight
- A share at depth 4320 (~18 hours old) has 25% weight
- A share at depth 8640 (~36 hours old) has 6.25% weight

**Effect on pool economics:** Continuous miners are unaffected (fresh shares
replace decaying old ones). Hoppers who leave find their shares rapidly
losing value. Measured improvement: hopper arrival advantage **5.27× → 1.52×**
(71.1% reduction).

### 4.6 Emergency Time-Based Decay (Phase 1b)

If no shares are produced for an extended period (>300 seconds on mainnet,
>80 seconds on testnet), share difficulty decays automatically based on
elapsed wall-clock time. This prevents a **difficulty death spiral** where:
1. A whale inflates difficulty to 100×
2. The whale leaves
3. Honest miners cannot find shares at 100× difficulty
4. The pool dies

With Phase 1b, difficulty auto-recovers within minutes of a whale departure.

### 4.7 Pure Difficulty Accounting (Phase 2c)

The legacy 0.5% "block finder fee" is removed. In V35, the miner whose share
happened to solve an LTC block received a 0.5% bonus — this rewarded luck,
not work. V36 distributes 100% of the block reward proportional to PPLNS
weight, reducing payout variance and removing an incentive for strategic
block withholding.

### 4.8 AutoRatchet Pinned to CHAIN_LENGTH (Phase R2)

The AutoRatchet version signaling mechanism uses `CHAIN_LENGTH` (fixed at
8640 on mainnet) for its confirmation window, not `REAL_CHAIN_LENGTH`. This
ensures that if V37 introduces adaptive PPLNS windows (which may change
`REAL_CHAIN_LENGTH`), the signaling window remains predictable.

---

## 5. Anti-Pool-Hopping Defense Stack

V36 implements a multi-layered defense against pool hopping attacks. The full
threat model, attack taxonomy, and defense analysis are documented in
[POOL_HOPPING_ATTACKS.md](POOL_HOPPING_ATTACKS.md).

### Deployed Defenses (V36 — Track 1)

| Phase | Defense | Status | Consensus | Effect |
|-------|---------|--------|-----------|--------|
| **1b** | Emergency time-based decay | **DEPLOYED** | Yes | Survive whale death spiral (300s threshold mainnet) |
| **2a** | Exponential PPLNS decay | **DEPLOYED** | Yes | Hopper arrival advantage 5.27× → 1.52× |
| **2c** | Pure difficulty accounting (finder fee removal) | **DEPLOYED** | Yes | Exact work-proportional payouts |
| **3L** | Log-based pool monitoring | **DEPLOYED** | No | Attack detection via structured log lines |
| **R2** | AutoRatchet pinned to CHAIN_LENGTH | **DEPLOYED** | Yes | V37 transition safety |

**Combined V36 result:** Hopper efficiency **3.8× → 0.6×** (unprofitable).
Approximately 135 net lines of code changed.

### Planned Defenses (V37 — Track 2, C++ c2pool)

| Phase | Defense | Why C++ | Effect |
|-------|---------|---------|--------|
| **2b** | Work-weighted exponential vesting | Complex cache, C++ perf | Burst shares start weak; builds over time |
| **4** | Adaptive PPLNS windows | 308K shares, C++ memory mgmt | Windows scale with time-to-block (~53 days at current hashrate) |
| **4-S2** | Share compaction — tiered storage | Native memory, struct packing | 75% RAM reduction |
| **4b** | LevelDB persistence | c2pool native | Sub-second crash recovery |
| **5** | Full dashboard + payout explanation | c2pool web architecture | Miner retention, transparency |
| **5-A** | Advanced monitoring + alerting | Richer web framework | False-positive-tuned alerts |

**Combined V37 result:** Hopper efficiency **0.6× → 0.1×** (economically
irrational). Adaptive windows also dramatically improve payouts for part-time
miners by extending the PPLNS window from ~36 hours to ~53 days.

> **Full analysis with per-miner impact tables:** [POOL_HOPPING_ATTACKS.md §8.2](POOL_HOPPING_ATTACKS.md#82-honest-miner-experience-step-by-step-impact-analysis)

---

## 6. Merged Mining (LTC + DOGE)

### Architecture

```
┌─────────────┐    Stratum    ┌─────────────┐   JSON-RPC   ┌─────────────┐
│   Miners    │◀─────────────▶│   P2Pool    │◀────────────▶│  Litecoin   │
│  (Scrypt)   │  Port 9327    │  (PyPy 2.7) │  Port 9332   │   Core      │
└─────────────┘               └──────┬──────┘              └─────────────┘
                                     │
                                     │ JSON-RPC (Port 44556)
                                     ▼
                              ┌──────────────┐   JSON-RPC   ┌─────────────┐
                              │  MM-Adapter  │◀────────────▶│  Dogecoin   │
                              │ (Python 3)   │  Port 22555  │   Core      │
                              └──────────────┘              └─────────────┘
```

### Key Features

- **Simultaneous LTC + DOGE mining** on the same decentralized share chain
- **PPLNS consensus** for both chains — same shares, same weights, same window
- **Multiaddress coinbase** — each chain's coinbase pays to the miner's native
  address format (P2PKH, P2SH, or bech32 for LTC; P2PKH or P2SH for DOGE)
- **Cross-chain address conversion** — automatic LTC→DOGE, DOGE→LTC, and
  swapped-order detection
- **MM-Adapter bridge** — translates P2Pool's merged mining protocol to
  standard Dogecoin Core RPC (`createauxblock`/`submitauxblock`), no custom
  daemon patches required
- **Combined donation marker** — single P2SH 1-of-2 multisig output on both
  chains, serving as both a development donation and a blockchain marker

### Stratum Username Format

```
LTC_ADDRESS,DOGE_ADDRESS.worker_name
```

| Format | Example | LTC Payout | DOGE Payout |
|--------|---------|-----------|-------------|
| `LTC,DOGE.worker` | `ltc1q...,DAddr.rig1` | Explicit LTC | Explicit DOGE |
| `LTC.worker` | `ltc1q....rig1` | Explicit LTC | Auto-converted from LTC |
| `DOGE,LTC.worker` | `DAddr,ltc1q..rig1` | Auto-corrected | Auto-corrected |
| Invalid LTC + valid DOGE | `XXX,DAddr.rig1` | Reverse-derived from DOGE | Explicit DOGE |

### Address Conversion Table

| LTC Address Type | → DOGE Payout | Notes |
|---|---|---|
| P2PKH (`L...`) | DOGE P2PKH (`D...`) | Same pubkey hash, safe |
| Bech32 (`ltc1q...`) | DOGE P2PKH (`D...`) | 20-byte witness program → P2PKH |
| P2SH (`M...` / `3...`) | DOGE P2SH (`9...` / `A...`) | ⚠ Only safe if redeem script uses DOGE-compatible opcodes |

> **Recommendation:** Always provide an explicit DOGE address via stratum
> comma syntax to avoid any P2SH cross-chain issues.

---

## 7. Dashboard & Monitoring

### Modern Dashboard

The V36 dashboard (`/static/dashboard.html`) is a complete rewrite:

- **Real-time stats**: Pool hash rate, local hash rate, shares, best share
  (with network difficulty comparison for both LTC and DOGE)
- **V36 transition progress**: Signaling percentage, version counts,
  propagation tracking, confirmation countdown
- **Merged mining**: DOGE block value, merged block history, per-miner
  DOGE payouts with address type indicators
- **Interactive graphs**: Hashrate over time with fullscreen mode, miners/
  workers tracking, luck trend overlay on block finds
- **Active Miners table**: Hash rates, DOA%, efficiency, share counts, with
  drill-down to individual miner pages
- **Current Payouts**: LTC payouts with nested DOGE equivalents, donation
  breakdown, address source indicators

### Structured Log Monitoring (Phase 3L)

V36 emits structured `MONITOR-` prefixed log lines for automated parsing:

```
MONITOR-HASHRATE ok total=4.05MH/s miners=3 shares=83/400
MONITOR-DIFF ok share_diff=0.003488 net_diff=0.000931
```

These can be consumed by external monitoring tools (Prometheus, Grafana,
custom scripts) without parsing unstructured log output.

---

## 8. Security Hardening

### Audit Findings (41 items)

A comprehensive security audit was conducted in February 2026. Key fixes:

| Severity | Finding | Fix |
|----------|---------|-----|
| **HIGH** | Ban/unban endpoints accessible from any IP | Restricted to localhost only |
| **HIGH** | No POST body size limit (OOM via large POST) | 64KB hard limit |
| **HIGH** | `assert` used for input validation (disabled with `-O`) | Replaced with `raise` |
| **HIGH** | IPv6 crash in DOGE broadcaster | Graceful skip |
| **MEDIUM** | Insecure decryption-only authority fallback | Require ECDSA signatures |
| **MEDIUM** | SSL error spam in logs | Suppressed at DefaultObserver level |

> **Full report:** [SECURITY_AUDIT_2026_02.md](SECURITY_AUDIT_2026_02.md)

### Dependency Updates

- **Twisted** 19.10.0 → **20.3.0** (fixes 3 critical + 1 medium CVE)
- **ecdsa** library required for share messaging authentication
- **coincurve** recommended for faster ECDSA operations

### P2P Hardening

- Broadcaster connection pruning (dead peer removal)
- Throttled version logging (once per 5 minutes per IP)
- Rate-limited merged address validation logs
- Connection threat detection (worker-to-connection ratio monitoring)

---

## 9. Infrastructure & DevOps

### Docker Support

```bash
docker pull ghcr.io/frstrtr/p2pool-merged-v36:latest
docker compose up -d
```

- Pre-built images on GitHub Container Registry (ghcr.io)
- `docker-compose.yml` with health checks for both P2Pool and MM-Adapter
- `.env.example` for configuration
- OCI labels for repository linkage

### Platform Support

| Platform | Status | Guide |
|----------|--------|-------|
| Linux (Ubuntu 20.04+) | ✅ Full support | [INSTALL.md](../INSTALL.md) |
| Linux (Ubuntu 24.04+) | ✅ Automated installer | `scripts/install_p2pool_ubuntu_2404.sh` |
| Windows 10/11 (WSL2) | ✅ Tested end-to-end | [WINDOWS_DEPLOYMENT.md](WINDOWS_DEPLOYMENT.md) |
| Windows (Docker) | ✅ Supported | [WINDOWS_DEPLOYMENT.md](WINDOWS_DEPLOYMENT.md) |
| macOS (Intel) | ✅ Tested | [INSTALL.md — macOS section](../INSTALL.md#macos-intel-installation) |

### Share Archival

Automatic share archival system with configurable retention, auto-scaling
storage, and recovery from archive. See [SHARE_ARCHIVE_README.md](SHARE_ARCHIVE_README.md).

---

## 10. Breaking Changes vs V35

| Change | Impact | Mitigation |
|--------|--------|-----------|
| V36 share format | Old share data incompatible | Delete `data/litecoin/shares.*` and `graph_db` before upgrade |
| Protocol version 3600 | V35 peers rejected | All network nodes must upgrade together |
| Combined donation script | Different coinbase structure | Automatic — no operator action needed |
| PPLNS exponential decay | Part-time miner payouts reduced | Correctly reflects actual work contribution |
| Finder fee removed | Block finders lose 0.5% bonus | All miners share the 0.5% proportionally |
| Merged mining fields in shares | Larger share wire format | Negligible bandwidth increase (~40 bytes/share) |

---

## 11. Migration from jtoomim/p2pool (V35)

### Step-by-Step

```bash
# 1. Stop existing V35 node
screen -S p2pool -X quit

# 2. Backup (optional — V35 data is not reusable)
cp -r data/litecoin data/litecoin_v35_backup

# 3. Clone V36
git clone https://github.com/frstrtr/p2pool-merged-v36.git
cd p2pool-merged-v36

# 4. Delete incompatible share data
rm -f data/litecoin/shares.* data/litecoin/graph_db

# 5. Install dependencies
pypy -m pip install twisted==20.3.0 pycryptodome 'scrypt>=0.8.0,<=0.8.22' ecdsa

# 6. Setup MM-Adapter for merged mining (see mm-adapter/README.md)
cd mm-adapter && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && cd ..

# 7. Start V36 with merged mining
pypy run_p2pool.py --net litecoin \
    --coind-address 127.0.0.1 --coind-rpc-port 9332 \
    --merged-coind-address 127.0.0.1 --merged-coind-rpc-port 44556 \
    --merged-coind-rpc-user dogecoinrpc --merged-coind-rpc-password YOUR_PASS \
    --merged-coind-p2p-address 127.0.0.1 --merged-coind-p2p-port 22556 \
    --address YOUR_LTC_ADDRESS --give-author 1 \
    --redistribute pplns --disable-upnp \
    litecoinrpc YOUR_LTC_RPC_PASSWORD
```

### What You Keep from V35

- ✅ Litecoin Core configuration (unchanged)
- ✅ Payout addresses (all types supported)
- ✅ Miner stratum connections (reconnect with optional DOGE address)
- ✅ Block history (`block_history.json` can be migrated)

### What Changes

- ❌ Share data (must be deleted — V36 format is incompatible)
- ❌ Peer connections (re-established automatically from bootstrap nodes)
- ⚡ Coinbase structure (V36 combined donation + merged mining outputs)
- ⚡ PPLNS weights (exponential decay — new payout formula)

---

## 12. What's Next: V37 and c2pool

V36 is the final major release of the Python 2.7/PyPy codebase. The next
generation of P2Pool is being built as [c2pool](https://github.com/frstrtr/c2pool),
a C++ reimplementation that provides:

### V37 Defense Improvements

| Feature | Benefit | Why C++ |
|---------|---------|---------|
| **Work-weighted vesting** | Burst shares start weak and vest over time — a hopper's first-hour shares carry only 3% weight | Complex incremental cache requires C++ performance |
| **Adaptive PPLNS windows** | Window scales from 36h to ~53 days based on pool hashrate. Part-time miners keep history across multi-day breaks | 308K shares × 4.5KB = 1.39GB; feasible in C++, not in PyPy |
| **Share compaction** | 75% RAM reduction via tiered storage and epoch aggregation | Native memory management, struct packing |
| **LevelDB persistence** | Sub-second crash recovery, no share data loss on restart | c2pool already has LevelDB integration |

### V37 Miner Impact (Projected)

With adaptive windows at current pool hashrate (49.5 GH/s):

| Mining Pattern | V35 | V36 | V37 (projected) |
|---|---|---|---|
| 24/7 continuous | baseline | ~0% change | ~+2% (longer window accumulates) |
| 16h on / 8h off | +7% over-reward | ~-8% | ~-8% (unchanged from V36) |
| Weekends only | +15% over-reward | ~-20% | ~-15% (adaptive window helps) |
| 6h/day (nights) | +20% over-reward | ~-25% | ~-17% (prior nights accumulate) |
| Sporadic (3-4 days/wk) | +25% over-reward | ~-35% | ~-15% (history persists across breaks) |
| **Pool hopper** | **+280% profit** | **-40% loss** | **-90% loss** |

> **Full per-miner analysis with 10 miner profiles:**
> [POOL_HOPPING_ATTACKS.md §8.2](POOL_HOPPING_ATTACKS.md#82-honest-miner-experience-step-by-step-impact-analysis)

### c2pool Migration Strategy

Features are implemented in Python first (p2pool-merged-v36), validated on
production, then ported to C++ (c2pool). The Python implementation serves as
both a working reference and a test suite.

**Porting order:**
1. **V36 share format** — wire-compatibility between Python and C++ nodes
2. **Merged mining** — coinbase construction, AuxPoW proofs, multi-daemon RPC
3. **Anti-hopping defenses** — vesting, adaptive windows, monitoring
4. **Stratum enhancements** — SSL/TLS, worker banning, CLI safeguards

> **Full roadmap:** [FUTURE.md](FUTURE.md)
> **c2pool repository:** https://github.com/frstrtr/c2pool
> **Community:** [Telegram](https://t.me/c2pooldev) · [Discord](https://discord.gg/yb6ujsPRsv)

---

## 13. Full Commit History

834 commits since the jtoomim/p2pool V35 fork point (`f0eeb48c`).

### Major Feature Commits (Chronological)

**Merged Mining Foundation:**
- `433323e1` — v1.2.0: Litecoin+Dogecoin merged mining support
- `d5d2aaa5` — Litecoin+Dogecoin merged mining production ready
- `4a3ff725` — Multiaddress merged mining with LTC+DOGE
- `dea95faa` — createauxblock/submitauxblock support for wallet-less daemons
- `4d06616e` — Reliable block propagation with parallel P2P broadcasting

**V36 Share Format & Consensus:**
- `c220282a` — MergedMiningShare class with merged_addresses support
- `cab78903` — Add merged_payout_hash to V36 share_info
- `d5c8397b` — AutoRatchet for automated V35→V36 version management
- `8a65daa8` — Native bech32/P2SH outputs in parent chain coinbase
- `918a5ceb` — VarStrType for hash_link extra_data + wire format optimizations
- `457d7c3d` — Enforce merged coinbase consensus verification (anti-theft)

**Anti-Hopping Defense Stack:**
- `f57f6cfe` — Phase 2a: Exponential PPLNS decay — implemented & tested
- `3ba0dc8f` — Phase 1b: Emergency time-based decay (death spiral prevention)
- `069690c0` — Phase 2c: Pure difficulty accounting — remove finder fee
- `d831a045` — Phase 3L: Log-based pool monitoring
- `420f384d` — Phase R2: Pin AutoRatchet to CHAIN_LENGTH

**Dashboard & UX:**
- `499fd1b9` — Best share card redesign with fancy percentage display
- `55262703` — Golden border + transition status for version signaling
- `fe528138` — BitAxe-style best share tracking with dashboard layout
- `cc68fa6b` — Case 4 DOGE→LTC reverse-conversion + address indicators
- `8f185aaf` — Fix DOGE address display for P2SH addresses

**Security:**
- `a95e4186` — Security audit fixes: H1, H2, H4, H8
- `f2323e3d` — Fix 6 MEDIUM audit findings
- `172afabf` — Bump Twisted 19.10.0 → 20.3.0 (CVE fixes)
- `58242aad` — Remove insecure decryption-only authority fallback

**Share Redistribution:**
- `de76224a` — `--redistribute` flag with 4 modes (pplns/fee/boost/donate)
- `7b9a3c77` — Inverse-weighted PPLNS redistribution favoring small miners
- `598f0765` — Preserve valid merged address when parent is invalid (Case 4)

**Infrastructure:**
- `0a5a13ac` — Docker deployment: Dockerfile, docker-compose.yml
- `7d1d5bf4` — Docker publish workflow for ghcr.io
- `0425dfde` — Windows 10/11 deployment guide (WSL2, Docker, Native)
- `9431fb42` — macOS (Intel) install guide

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [POOL_HOPPING_ATTACKS.md](POOL_HOPPING_ATTACKS.md) | Full threat model, attack taxonomy, defense analysis, per-miner impact tables |
| [FUTURE.md](FUTURE.md) | Roadmap — stratum enhancements, redistribution system, c2pool migration plan |
| [V36_TRANSITION_GUIDE.md](V36_TRANSITION_GUIDE.md) | AutoRatchet stages, dashboard legend, transition monitoring |
| [V35_V36_TRANSITION_TEST_RESULTS.md](V35_V36_TRANSITION_TEST_RESULTS.md) | V35→V36 transition test report, ratchet reset procedure for testers |
| [SECURITY_AUDIT_2026_02.md](SECURITY_AUDIT_2026_02.md) | 41-finding security audit report with fix status |
| [MULTIADDRESS_MINING_GUIDE.md](MULTIADDRESS_MINING_GUIDE.md) | Stratum username formats, address conversion, redistribution policies |
| [CHANGELOG.md](../CHANGELOG.md) | Per-release changelog (v36-0.01-alpha through v36-0.10-alpha) |
| [INSTALL.md](../INSTALL.md) | Complete installation guide (Linux, macOS, Windows) |

---

*V36 development by [frstrtr](https://github.com/frstrtr). Built on the
foundations of [forrestv/p2pool](https://github.com/p2pool/p2pool) (2011) and
[jtoomim/p2pool](https://github.com/jtoomim/p2pool) (V35).*
