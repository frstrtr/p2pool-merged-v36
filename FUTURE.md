# Future Enhancements for p2pool-dash

This document outlines potential future improvements for the p2pool-dash mining pool software.

## Current Status (v1.2.0)

The following features are already implemented:

| Feature | Version | Status |
|---------|---------|--------|
| ASIC Support (vardiff, short job IDs, rate limiting) | v1.0.0 | ✅ |
| ASICBoost/Version Rolling (BIP320) | v1.0.0 | ✅ |
| `mining.suggest_difficulty` | v1.2.0 | ✅ |
| `minimum-difficulty` (BIP310) | v1.2.0 | ✅ |
| `subscribe-extranonce` (NiceHash) | v1.0.0 | ✅ |
| Session resumption | v1.2.0 | ✅ |
| `client.reconnect` | v1.2.0 | ✅ |
| Per-worker statistics | v1.2.0 | ✅ |
| Dynamic share rate | v1.2.0 | ✅ |
| Stratum stats web page | v1.2.0 | ✅ |
| `/stratum_stats` API | v1.2.0 | ✅ |
| Pool safeguards (min/max difficulty, rate limiting) | v1.2.0 | ✅ |
| `--bench` flag | v1.1.0 | ✅ |
| Dust threshold protection | v1.0.0 | ✅ |
| Block explorer links | v1.0.0 | ✅ |

---

## High Priority Enhancements

### 1. Command-Line Configuration for Safeguards
**Difficulty:** Easy | **Impact:** High

Expose pool safeguard values as command-line arguments instead of hardcoded values:

```bash
--min-difficulty DIFF      # Minimum difficulty floor (default: 0.001)
--max-difficulty DIFF      # Maximum difficulty ceiling (default: 1000000)
--max-connections NUM      # Maximum concurrent connections (default: 10000)
--session-timeout SECS     # Session expiration time (default: 3600)
```

**Files to modify:** `p2pool/main.py`, `p2pool/dash/stratum.py`

### 2. `mining.ping` Support
**Difficulty:** Easy | **Impact:** Medium

Add ping/pong support for connection health monitoring:

```python
def rpc_ping(self):
    """Handle mining.ping - connection health check"""
    return "pong"
```

**Benefits:**
- Detect dead connections faster
- Reduce stale work submissions
- Better network quality monitoring

**Files to modify:** `p2pool/dash/stratum.py`

### 3. Worker Auto-Banning
**Difficulty:** Medium | **Impact:** High

Automatically ban misbehaving workers temporarily:

- High reject rate (>50% over N submissions)
- Rapid reconnection attempts (DoS prevention)
- Invalid authorization attempts

```python
class WorkerBanList:
    def __init__(self, ban_duration=300):  # 5 minute default ban
        self.banned = {}  # {ip: ban_expiry_time}
    
    def ban(self, ip, reason):
        self.banned[ip] = time.time() + self.ban_duration
        log.msg("BANNED %s for %s" % (ip, reason))
    
    def is_banned(self, ip):
        if ip in self.banned:
            if time.time() < self.banned[ip]:
                return True
            del self.banned[ip]
        return False
```

**Files to modify:** `p2pool/dash/stratum.py`

### 4. Prometheus Metrics Endpoint
**Difficulty:** Medium | **Impact:** High

Add `/metrics` endpoint in Prometheus format for Grafana dashboards:

```
# HELP p2pool_connected_workers Number of connected workers
# TYPE p2pool_connected_workers gauge
p2pool_connected_workers 6

# HELP p2pool_pool_hashrate Pool hashrate in H/s
# TYPE p2pool_pool_hashrate gauge
p2pool_pool_hashrate 948000000000

# HELP p2pool_shares_accepted Total accepted shares
# TYPE p2pool_shares_accepted counter
p2pool_shares_accepted 15234

# HELP p2pool_shares_rejected Total rejected shares
# TYPE p2pool_shares_rejected counter
p2pool_shares_rejected 12

# HELP p2pool_blocks_found Total blocks found
# TYPE p2pool_blocks_found counter
p2pool_blocks_found 3
```

**Files to modify:** `p2pool/web.py`

### 5. SSL/TLS Stratum Support
**Difficulty:** Hard | **Impact:** High

Add encrypted stratum connections:

```bash
--stratum-ssl-port PORT    # SSL stratum port (e.g., 3334)
--ssl-cert FILE            # Path to SSL certificate
--ssl-key FILE             # Path to SSL private key
```

**Benefits:**
- Encrypted communication
- Man-in-the-middle protection
- Required by some mining software

**Files to modify:** `p2pool/main.py`, `p2pool/dash/stratum.py`

---

## Medium Priority Enhancements

### 6. `mining.set_version_mask` 
**Difficulty:** Easy | **Impact:** Medium

Allow dynamic version mask updates during mining session:

```python
def rpc_set_version_mask(self, mask):
    """Update version rolling mask mid-session"""
    self.pool_version_mask = int(mask, 16)
    return True
```

### 7. Historical Worker Statistics
**Difficulty:** Medium | **Impact:** Medium

Persist worker statistics to disk for historical analysis:

- SQLite database for lightweight storage
- Configurable retention period
- API endpoints for historical queries

```bash
--stats-db FILE            # Path to statistics database
--stats-retention DAYS     # How long to keep historical data (default: 30)
```

### 8. Block Found Webhook
**Difficulty:** Easy | **Impact:** Medium

Notify external services when pool finds a block:

```bash
--block-webhook URL        # URL to POST when block found
```

Payload:
```json
{
  "event": "block_found",
  "block_hash": "00000000...",
  "block_height": 123456,
  "timestamp": 1702300000,
  "value": 1.77
}
```

### 9. Per-Worker Hash Rate Graphs
**Difficulty:** Medium | **Impact:** Medium

Add graphing capability for individual worker hash rates:

- Track hash rate samples over time (memory or disk)
- New web page `/static/worker.html?name=<worker>`
- D3.js line charts similar to `graphs.html`

### 10. Mobile-Responsive Web UI
**Difficulty:** Easy | **Impact:** Medium

Update CSS for responsive design:

```css
@media (max-width: 768px) {
    .stats-grid {
        grid-template-columns: 1fr;
    }
    table {
        font-size: 12px;
    }
}
```

---

## Lower Priority Enhancements

### 11. `client.show_message`
**Difficulty:** Easy | **Impact:** Low

Broadcast messages to all connected miners:

```python
def broadcast_message(self, message):
    """Send message to all connected miners"""
    for conn in pool_stats.connections.values():
        conn.other.svc_client.rpc_show_message(message)
```

Use cases:
- Maintenance announcements
- Pool updates
- Emergency notifications

### 12. WebSocket Real-time Updates
**Difficulty:** Hard | **Impact:** Medium

Replace HTTP polling with WebSocket for live dashboard updates:

- Instant statistics updates
- Reduced server load
- Better user experience

### 13. API Authentication
**Difficulty:** Medium | **Impact:** Low

Add optional API key authentication for sensitive endpoints:

```bash
--api-key KEY              # Required key for API access
--api-key-endpoints LIST   # Comma-separated list of protected endpoints
```

### 14. Email/SMS Alerts
**Difficulty:** Medium | **Impact:** Medium

Configurable alerts for important events:

```bash
--alert-email ADDRESS      # Email for alerts
--alert-smtp-server HOST   # SMTP server
--alert-events LIST        # Events to alert on (block_found,node_down,etc)
```

### 15. `client.get_version`
**Difficulty:** Easy | **Impact:** Low

Request miner software version for diagnostics:

```python
def request_miner_version(self):
    """Request miner version information"""
    return self.other.svc_client.rpc_get_version()
```

---

## Code Quality Improvements

### Known XXX/TODO Comments

1. **`p2pool/util/graph.py`** - Exception handling marked as "XXX blah"
2. **`p2pool/data.py`** - Uses local stale rate instead of global for pool hash calculation
3. **`p2pool/main.py`** - Windows file rename workaround could use better cross-platform handling

### Testing Improvements

- Add unit tests for new stratum extensions
- Integration tests with mock miners
- Performance benchmarks for high-load scenarios

---

## Contributing

To contribute an enhancement:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/enhancement-name`)
3. Implement the enhancement
4. Add/update documentation
5. Submit a pull request

Please reference this document in your PR description.

---

## Version Roadmap

| Version | Target Features |
|---------|-----------------|
| v1.3.0 | CLI safeguard args, `mining.ping`, worker banning |
| v1.4.0 | Prometheus metrics, block webhooks |
| v1.5.0 | SSL/TLS stratum, historical stats |
| v2.0.0 | WebSocket updates, major UI refresh |
