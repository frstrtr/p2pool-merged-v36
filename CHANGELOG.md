# Changelog

All notable changes to P2Pool Merged Mining V36 are documented in this file.

## [v36-0.06-alpha] - 2026-02-26

### Critical Fixes
- **fix: restore --coinbtext support in LTC coinbase** - Regression from jtoomim fork that broke parent chain coinbase text injection (`59ae3dd`)

### Security
- **security: fix 6 MEDIUM audit findings** - M1 (localhost-only ban/unban), M4 (rate limiting), M5 (input validation), M8 (safe defaults), M14 (log sanitization), M16 (error handling) (`f2323e3`)

### MM Adapter Overhaul
- **Unified adapter** - Removed legacy `adapter_v2.py`, `adapter_legacy_DEPRECATED.py`, `config_v2.yaml`; single `adapter.py` with multiaddress mode (`2b50d7a`)
- **Log file support** - Added `--log-file` CLI flag and `logging.file` config option for file-based logging (`2b50d7a`)
- **Log spam suppression** - Demoted routine RPC/template/aiohttp.access logs to DEBUG; only new blocks, submissions, errors at INFO (`5f36ed0`)
- **Default coinbase text** - Changed `P2POOL_TAG` and adapter `DEFAULT_COINBASE_TEXT` to `"technocore"` (`2b50d7a`)

### Diagnostics & Logging
- **Merged mining diagnostic logging** - Added `[MERGED-DIAG]` prefixed logs for twin block debugging: parent block detection, merged work state, submission flow (`a9d7998`)
- **Startup checklist** - 7-point `MERGED MINING STARTUP CHECKLIST` printed on launch with parent/merged addresses, P2P broadcaster, fees, stratum format, coinbase text (`5eab5a2`, `6840b14`)
- **CHECK 3: actual DOGE address** - Shows converted Dogecoin address (not parent chain address) using dogecoin network module (`6840b14`)
- **CHECK 6: worker separator clarity** - Uses `<ltc_addr>,<doge_addr>` placeholders to avoid confusion with `_` worker separator (`6840b14`)
- **CHECK 7: coinbase text display** - Shows parent chain `--coinbtext` and merged chain fallback tag on startup (`6840b14`)

### Documentation
- **Worker name separators** - Clarified both `.` and `_` are valid worker separators in README and startup logs (`27f6b4a`)
- **Coinbase text documentation** - Clarified `--coinbtext` (parent chain scriptSig) vs `coinbase_text` (child chain OP_RETURN) (`2b50d7a`)

### Compatibility
- **PyPy 2.7 fix** - Replaced em-dash (U+2014) with ASCII `--` in startup checklist to avoid `UnicodeDecodeError` under PyPy 2.7 (`9640efd`)

---

## [v36-0.05-alpha] - 2026-02-26

- feat: add merged mining startup checklist and startup confirmation logs
- security: fix 6 MEDIUM audit findings (M1,M4,M5,M8,M14,M16)
- Security audit fixes: H1 (localhost-only ban/unban), H2 (64KB POST limit), H4 (assert->raise), H8 (IPv6 crash)

## [v36-0.04-alpha] - 2026-02-25

- fix: log blob read errors + add /msg/diag endpoint

## [v36-0.03-alpha] - 2026-02-24

- Share messaging protocol implementation

## [v36-0.02-alpha] - 2026-02-23

- Multiaddress merged mining support

## [v36-0.01-alpha] - 2026-02-22

- Initial V36 sharechain protocol with merged mining
