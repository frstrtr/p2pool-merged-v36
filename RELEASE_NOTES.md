# v36-0.13-alpha Release Notes

**Release date:** 2026-03-10

## Highlights

### Discovered Merged Blocks — Heuristic Cross-Chain Block Finder (NEW)

P2Pool V36 dashboards now automatically discover merged-mined blocks by scanning the `fabe6d6d` coinbase marker in parent chain (LTC) blocks. This works on **any** V35 or V36 node, even without a merged chain daemon running:

- **Backend**: New `extract_aux_hash_from_coinbase()` helper extracts the 32-byte aux chain block hash from the coinbase scriptSig's merged mining commitment
- **API**: New `/discovered_merged_blocks` endpoint returns parent blocks enriched with aux hash, miner address, and source node info
- **Dashboard**: New **🔗 Discovered Merged Blocks** table section shows:
  - Parent block height and hash (links to LTC block explorer)
  - Aux block hash (links to blockchair.com/dogecoin)
  - Miner payout address
  - Source node IP (⭐ local for blocks found by this node)
  - Block confirmation status
- **Backfill**: Existing blocks in history are retroactively enriched with aux hashes and peer addresses from the share tracker
- **Auto-refresh**: Section updates every 30s alongside other dashboard panels; hidden when no merged blocks exist

### Node Tracking for Block Finders

Every block in history now records which P2Pool node relayed the share that found it:
- `peer_addr` field tracks the source node IP:port (or "local" for blocks found by this node's stratum workers)
- Backfilled for existing blocks from the share tracker's `s.peer_addr` attribute
- Displayed in the Discovered Merged Blocks table with visual distinction (⭐ local in green, remote IPs in secondary color)

### DigiByte (DGB) Parent Chain Support

Full P2Pool network definitions for DigiByte (Scrypt):
- DGB mainnet & testnet network configs with complete bitcoin-level parameters
- DGB subsidy calculation — three-phase reward schedule with halvings
- `scripts/dgb_sniffer.py` — P2Pool protocol diagnostic tool for DGB
- Bootstrap mode with `PERSIST=False` for fresh sharechain startup

### Multichain Dashboard & Proxy

- **Multi-pool reverse proxy** (`multipool/multipool_proxy.py`) — aggregates multiple P2Pool instances behind a single web interface
- **Pool selector tabs** — clickable chain tabs in dashboard header with localStorage persistence
- **Chain-agnostic dashboard** — dynamic parent chain names, configurable address hints
- **`multipool.js`** — transparent d3.json() override for cross-origin multi-pool support
- **`scripts/start_multichain.sh`** — turnkey startup for DGB + LTC + DOGE merged mining

### Vardiff Fixes — ASIC Miner Support

Three critical fixes for hardware miners:
- Removed `min_share_target` clamping that made work impossibly hard for small ASICs
- Fixed network object reference for `SANE_TARGET_RANGE` bounds
- Use `share_target` for initial difficulty instead of easiest sane target

### Additional Fixes

- **AutoRatchet**: Tick on share arrival (non-mining nodes advance), retroactive confirmation from sharechain depth, hide transition dialog when confirmed
- **Merged mining**: Target byte-order bug fix + empty sharechain guard
- **Dashboard**: Hide inactive miners by default with "Show historical" checkbox
- **DOA detection**: Uses parent-block-only counter; boost uses conn.user (string)
- **ltc_scrypt**: Fix load order — C extension first, py-scrypt fallback
- **Whale departure recovery**: Non-consensus local heuristic for hashrate departure events

## Upgrade Notes

Drop-in replacement for v36-0.12-alpha. No breaking changes. The new discovered merged blocks section appears automatically when parent chain blocks contain merged mining commitments.

## Full Changelog

See [CHANGELOG.md](CHANGELOG.md) for the complete change history.
