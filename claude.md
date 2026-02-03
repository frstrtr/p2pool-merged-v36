# P2Pool Dogecoin Testnet4Alpha - Reference Documentation

## CRITICAL NOTES

### ⚠️ Python2 is REMOVED from Ubuntu 24.04+
**ALWAYS use `pypy` instead of `python2` for all operations:**
```bash
# Syntax checking
pypy -m py_compile p2pool/data.py

# Running p2pool
pypy run_p2pool.py ...

# NEVER use python2 - it's not available on modern Ubuntu!
```

---

## Overview

Created a private Dogecoin testnet called **testnet4alpha** to work around the official testnet's block storm bug (PR #3967). This allows proper merged mining testing with stable difficulty.

**Status:** ✅ WORKING - Multiaddress merged mining operational as of 2026-01-27

---

## MAINNET Configuration (Litecoin + Dogecoin Merged Mining)

### Infrastructure
| VM | IP | Purpose |
|---|---|---|
| Litecoin Node | 192.168.86.26 | Parent chain (LTC mainnet) |
| Dogecoin Node | 192.168.86.27 | Merged chain via MM-Adapter |
| MM-Adapter | 192.168.86.31:44556 | Dogecoin multiaddress bridge |
| P2Pool Server | 192.168.86.31 | P2Pool merged mining |

### Project Location on P2Pool Server
```
~/p2pool-merged/
```

### Deploy Files from Dev Machine
```bash
scp /home/user0/Github/p2pool-dash/p2pool/web.py user0@192.168.86.31:~/p2pool-merged/p2pool/web.py
scp /home/user0/Github/p2pool-dash/p2pool/work.py user0@192.168.86.31:~/p2pool-merged/p2pool/work.py
scp /home/user0/Github/p2pool-dash/web-static/*.html user0@192.168.86.31:~/p2pool-merged/web-static/
```

### Start P2Pool Mainnet (Litecoin + Dogecoin)
```bash
ssh user0@192.168.86.31 'cd ~/p2pool-merged && export PATH="$HOME/pypy2.7-v7.3.20-linux64/bin:$PATH" && nohup pypy run_p2pool.py --net litecoin --coind-address 192.168.86.26 --coind-rpc-port 9332 --coind-p2p-port 9333 --merged-coind-address 127.0.0.1 --merged-coind-rpc-port 44556 --merged-coind-rpc-user dogecoinrpc --merged-coind-rpc-password dogecoinrpc_mainnet_2026 --address LbxJe7Nf59gv2vK7Mw8kEa6aWFDHjwsf2E --give-author 1 -f 1 --disable-upnp --max-conns 0 litecoinrpc litecoinrpc_mainnet_2026 > /dev/null 2>&1 &'
```

### Stop P2Pool Mainnet
```bash
ssh user0@192.168.86.31 "pkill -f 'pypy run_p2pool.py'"
```

### Monitor P2Pool Logs
```bash
ssh user0@192.168.86.31 "tail -f ~/p2pool-merged/data/litecoin/log"
```

### Web Dashboard
- Dashboard: http://192.168.86.31:9327/static/
- Miners: http://192.168.86.31:9327/static/miners.html
- API: http://192.168.86.31:9327/current_payouts
- Merged Payouts: http://192.168.86.31:9327/current_merged_payouts

---

## The Problem

Dogecoin official testnet has a critical bug where `fPowAllowDigishieldMinDifficultyBlocks` check is COMMENTED OUT in `dogecoin.cpp`. This allows unlimited minimum difficulty blocks via timestamp manipulation, causing:
- 31M+ blocks (should be ~5M)
- Blocks every 1-10 seconds instead of 60 seconds
- All merged mined blocks orphaned due to stale work

**Official fix:** PR #3967 by @cf - Patrick Lodder (Dogecoin maintainer) is "concept ACK on doing this in a testnet4"

## Quick Start Guide

### 1. Start Dogecoin testnet4alpha (192.168.86.27)
```bash
ssh user0@192.168.86.27

# Start daemon with txindex for block explorer support
cd ~/dogecoin-auxpow-gbt
./src/dogecoind -testnet4alpha -txindex=1 -rpcuser=dogecoinrpc -rpcpassword=testpass -rpcallowip=192.168.86.0/24 -rpcbind=0.0.0.0 -daemon

# Verify it's running
./src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass getblockcount
./src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass getblocktemplate '{"rules": ["segwit"], "capabilities": ["auxpow"]}' | jq '.auxpow'
# Should show: {"chainid": 98, "target": "..."}
```

### 2. Verify Litecoin testnet (192.168.86.26)
```bash
ssh user0@192.168.86.26
litecoin-cli -testnet getblockcount
```

### 3. Start P2Pool (192.168.86.31)
```bash
ssh user0@192.168.86.31
cd ~/p2pool-merged

# Sync latest code from development machine
# (Run from dev machine): rsync -av --exclude='.git' --exclude='*.pyc' /home/user0/Github/p2pool-dash/ user0@192.168.86.31:~/p2pool-merged/

# Start P2Pool with merged mining
nohup /snap/bin/pypy run_p2pool.py \
    --net litecoin --testnet \
    --coind-address 192.168.86.26 \
    --coind-rpc-port 18332 \
    --coind-p2p-port 18333 \
    --merged-coind-address 192.168.86.27 \
    --merged-coind-rpc-port 44555 \
    --merged-coind-rpc-user dogecoinrpc \
    --merged-coind-rpc-password testpass \
    --address tltc1qgz3ung5t2n6z7dkd3gvemrx3fgg0qwztkq6rfn \
    --give-author 1 -f 1 \
    litecoinrpc litecoinrpc_testnet_1767955680 \
    > /tmp/p2pool.log 2>&1 &

# Monitor logs
tail -f /tmp/p2pool.log | grep -E 'Merged Chain|MERGED CHECK|rejected|GOT SHARE'
```

### 4. Verify Merged Mining is Working
Look for these log messages:
```
Detected auxpow-capable merged mining daemon at http://192.168.86.27:44555/ (multiaddress support enabled)
[MERGED-REFRESH] Template height=X prev=... hash=...
Merged Chain: Dogecoin Testnet (tDOGE)  <- Block accepted!
```

Check block counts are increasing:
```bash
ssh user0@192.168.86.27 "~/dogecoin-auxpow-gbt/src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass getblockcount"
```

## Testnet4Alpha Network Parameters

### Genesis Block (mined 2025-01-26)
```
Timestamp: 1737907200 (Sun Jan 26 20:00:00 2025 UTC)
Nonce: 1812121
Bits: 0x1e0ffff0
PoW Hash (scrypt): 000005b78b201bb5e9d115cf18d55e8688480cb48bb7c9cf890d45d5ae9f785b
Block Hash (SHA256): de2bcf594a4134cef164a2204ca2f9bce745ff61c22bd714ebc88a7f2bdd8734
Merkle Root: 5b2a3f53f605d62c53e62932dac6925e3d74afa5a4b459745c36d42d0ed26a69
```

### Network Configuration
```
Network ID: testnet4alpha
Network Magic: 0xd4, 0xa1, 0xf4, 0xa1
P2P Port: 44557
RPC Port: 44555
Address Version: 113 (addresses start with 'n')
P2SH Version: 196 (addresses start with '2')
AuxPoW Chain ID: 0x0062 (98)
Data Directory: ~/.dogecoin/testnet4alpha/
```

### Key Consensus Parameters
```cpp
fPowAllowMinDifficultyBlocks = false       // CRITICAL: No min-diff!
fPowAllowDigishieldMinDifficultyBlocks = false
fEnforceStrictMinDifficulty = true         // CRITICAL: Use PR #3967 fix!
fMiningRequiresPeers = false               // Solo mining OK
nPowTargetSpacing = 60                     // 1 minute blocks
nAuxpowChainId = 0x0062                    // Same as testnet
fAllowLegacyBlocks = true                  // Allow non-auxpow initially
```

## VM Infrastructure

| VM | IP | Purpose |
|---|---|---|
| Litecoin Node | 192.168.86.26 | Parent chain (LTC testnet) |
| Dogecoin Node | 192.168.86.27 | Merged chain (testnet4alpha) |
| P2Pool Server | 192.168.86.31 | P2Pool merged mining |

## Dogecoin Node Setup (192.168.86.27)

### Source Location
```
~/dogecoin-auxpow-gbt/
```

### Cherry-picked/Applied Commits
1. `e84bd9fdf` - Add strict minimum difficulty rules (PR #3967 fix)
2. `a9e5ebb57` - Add testnet4alpha private network
3. `0d0c21d6c` - **Add getblocktemplate auxpow capability support** (enables multiaddress mode)

### Build Commands
```bash
cd ~/dogecoin-auxpow-gbt
make -j4
```

### Start Commands
```bash
# Start testnet4alpha daemon with full options
~/dogecoin-auxpow-gbt/src/dogecoind -testnet4alpha \
    -txindex=1 \
    -rpcuser=dogecoinrpc \
    -rpcpassword=testpass \
    -rpcallowip=192.168.86.0/24 \
    -rpcbind=0.0.0.0 \
    -daemon

# CLI commands
~/dogecoin-auxpow-gbt/src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass getblockchaininfo
~/dogecoin-auxpow-gbt/src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass getmininginfo
~/dogecoin-auxpow-gbt/src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass getblockcount

# Check auxpow support in getblocktemplate
~/dogecoin-auxpow-gbt/src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass \
    getblocktemplate '{"rules": ["segwit"], "capabilities": ["auxpow"]}' | jq '.auxpow'
```

## P2Pool Network Configuration

### Network File Location
```
p2pool/bitcoin/networks/dogecoin_testnet4alpha.py
```

### Merged Mining Modes
P2Pool supports two merged mining modes:

1. **Multiaddress Mode** (preferred) - Uses `getblocktemplate` with auxpow capability
   - Enables PPLNS distribution to multiple miners
   - Requires Dogecoin with commit `0d0c21d6c` (auxpow in getblocktemplate)
   - Log shows: "Detected auxpow-capable merged mining daemon"

2. **Single-address Mode** (fallback) - Uses `createauxblock`/`submitauxblock`
   - All rewards go to single address
   - Log shows: "Warning: getblocktemplate succeeded but no auxpow object"

## Important Addresses

### P2Pool Donation Address (testnet)
```
nXBZW6xtYrZwCe4PhEhLDhM3DFLSd1pa1R
```
Derived from pubkey_hash: `20cb5c22b1e4d5947e5c112c7696b51ad9af3c61`

### Default Miner Payout Address
```
Litecoin testnet: tltc1qgz3ung5t2n6z7dkd3gvemrx3fgg0qwztkq6rfn
Auto-converted to Dogecoin: mnNhBYb5gGA7UBwc6tdtbrd2G53Z3qFC7t
```

## Troubleshooting

### Port Conflicts
Kill old dogecoind processes before starting testnet4alpha:
```bash
pkill -9 dogecoind
```

### RPC Connection Issues
Use explicit -testnet4alpha flag and credentials for CLI:
```bash
~/dogecoin-auxpow-gbt/src/dogecoin-cli -testnet4alpha -rpcuser=dogecoinrpc -rpcpassword=testpass <command>
```

### Check Daemon Status
```bash
ps aux | grep dogecoind
tail -f ~/.dogecoin/testnet4alpha/debug.log
```

### Flush Sharechain (if needed)
```bash
ssh user0@192.168.86.31 "rm -rf ~/p2pool-merged/data/litecoin_testnet/shares*"
```

### Flush Dogecoin Blockchain (fresh start)
```bash
ssh user0@192.168.86.27 "pkill -9 dogecoind; rm -rf ~/.dogecoin/testnet4alpha/blocks ~/.dogecoin/testnet4alpha/chainstate ~/.dogecoin/testnet4alpha/indexes"
```

### Common Errors

1. **"bad-cb-height"** - Coinbase height encoding issue (fixed in merged_mining.py using script.create_push_script)
2. **"block hash unknown"** - Stale work, template needs refresh
3. **"duplicate-invalid"** - Block already rejected, will be skipped

## References

- **Dogecoin PR #3967:** https://github.com/dogecoin/dogecoin/pull/3967
- **Bug Documentation:** [DOGECOIN_TESTNET_BUG.md](DOGECOIN_TESTNET_BUG.md)
- **P2Pool Network Config:** [p2pool/bitcoin/networks/dogecoin_testnet4alpha.py](p2pool/bitcoin/networks/dogecoin_testnet4alpha.py)

## Development Notes

### Key Files Modified for Multiaddress Merged Mining
- `p2pool/merged_mining.py` - Coinbase building with correct BIP34 height encoding
- `p2pool/work.py` - Merged work polling and block submission
- `p2pool/bitcoin/networks/dogecoin_testnet4alpha.py` - Network parameters

### Coinbase Height Fix (2026-01-27)
The `build_coinbase_input_script()` function was fixed to use `script.create_push_script([height])` instead of manual encoding. This ensures proper BIP34 height serialization (e.g., height=32 → `0120`).
