# P2Pool Share Messaging — Wire Protocol Specification

## Version

Protocol Version: 1  
Share Version: 36 (V36 `ref_type` extension)

## Terminology

| Term | Definition |
|------|-----------|
| **Master PK** | Miner's payout address private key (never transmitted) |
| **Signing Key** | HMAC-derived key used to sign messages |
| **signing_id** | HASH160(compressed signing pubkey) — 20-byte identifier |
| **key_index** | uint32 rotation counter for signing key derivation |
| **ref_type** | Share structure hashed into ref_hash (PoW-protected, not in coinbase) |
| **message_data** | VarStr field in ref_type carrying the message envelope |

## Share ref_type Extension

### Current V36 ref_type (before messaging)

```
ref_type = ComposedType([
    ('identifier',  FixedStrType(8)),
    ('share_info',  share_info_type),
])
```

### Extended V36 ref_type (with messaging)

```
ref_type = ComposedType([
    ('identifier',    FixedStrType(8)),
    ('share_info',    share_info_type),
    ('message_data',  PossiblyNoneType(b'', VarStrType())),   # NEW
])
```

The `message_data` field:
- Default value: `b''` (empty bytes) — no messages
- Wrapped in `PossiblyNoneType` for backward compatibility
- Included in `ref_hash` computation → PoW-protected
- NOT included in coinbase/gentx → never written to blockchain

## Message Envelope Format

The `message_data` bytes contain a structured envelope:

```
Offset  Size  Field               Description
------  ----  ------------------  ------------------------------------------
0       1     version             Protocol version (currently 1)
1       1     envelope_flags      Bit flags for envelope
2       1     msg_count           Number of messages (0-3)
3       1     announcement_len    Length of signing key announcement (0 or 57)
4       N     announcement        Signing key announcement (if announcement_len > 0)
4+N     ...   messages[]          Packed messages (msg_count entries)
```

### Envelope Flags

```
Bit 0 (0x01): Has signing key announcement
Bits 1-7:     Reserved (must be 0)
```

### Total Size Budget

```
Envelope header:                     4 bytes
Signing key announcement (optional): 57 bytes
Per message overhead (minimum):      29 bytes (header + signing_id + sig_len)
Per message payload:                 0-220 bytes
Per message signature:               0-73 bytes (DER ECDSA)

Maximum total:                       512 bytes
```

## Signing Key Announcement Format

Carried in the envelope when a miner announces or rotates their signing key.

```
Offset  Size  Field            Description
------  ----  ---------------  ------------------------------------------
0       20    signing_id       HASH160(compressed signing pubkey)
20      4     key_index        Little-endian uint32 rotation counter
24      33    signing_pubkey   Compressed secp256k1 public key (02/03 prefix)
                               
Total:  57 bytes
```

### Verification

Recipients verify the announcement by checking:
```
HASH160(signing_pubkey) == signing_id
```

If this check fails, the announcement is silently discarded.

### Key Index Semantics

- `key_index = 0`: First signing key (initial announcement)
- `key_index = N`: N-th rotation
- When a node sees `key_index > previous_key_index` for a miner:
  - All signing keys with lower key_index are marked **REVOKED**
  - Messages signed with revoked keys fail verification
- A miner should include the announcement in every share (not just on rotation)
  to ensure new nodes learn the current signing key

## Message Wire Format

Each message in the `messages[]` array:

```
Offset  Size  Field            Description
------  ----  ---------------  ------------------------------------------
0       1     msg_type         Message type (see table below)
1       1     msg_flags        Per-message flags
2       4     timestamp        Unix timestamp (little-endian uint32)
6       2     payload_len      Payload length (little-endian uint16)
8       N     payload          Message payload (0-220 bytes)
8+N     20    signing_id       HASH160 of signer's pubkey (or 20 zero bytes)
28+N    1     sig_len          Signature length (0-73)
29+N    M     signature        DER-encoded ECDSA signature (if sig_len > 0)
```

### Message Types

```
Value  Name            Signed?     Persistent?  Description
-----  --------------  ----------  -----------  ---------------------------
0x01   NODE_STATUS     Optional    No           Node health report
0x02   MINER_MESSAGE   Required    Yes          Miner-to-miner text
0x03   POOL_ANNOUNCE   Required    Yes          Operator announcement
0x04   VERSION_SIGNAL  Optional    No           Extended version info
0x05   MERGED_STATUS   Optional    No           Merged chain status
0x10   EMERGENCY       Required    Yes          Security alert

0x00, 0x06-0x0F, 0x11-0xFF: Reserved for future use
```

### Message Flags

```
Bit 0 (0x01): FLAG_HAS_SIGNATURE  — Message is signed
Bit 1 (0x02): FLAG_BROADCAST      — Relay to peers
Bit 2 (0x04): FLAG_PERSISTENT     — Store in history
Bits 3-7:     Reserved (must be 0)
```

### Unsigned Messages

For unsigned messages (e.g., NODE_STATUS without FLAG_HAS_SIGNATURE):
- `signing_id` is set to 20 zero bytes (`\x00` × 20)
- `sig_len` is 0
- `signature` is empty

The message is still PoW-protected (embedded in ref_hash via the share)
but is not cryptographically attributed to a specific signing key.
The sender is identified only by the share's payout address.

## Signature Scheme

### What is Signed

```
message_hash = SHA256d(msg_type || msg_flags || timestamp || payload)
             = SHA256(SHA256(pack('<BBI', type, flags, timestamp) + payload))
```

The signature does NOT cover signing_id — this prevents circular dependency
(signing_id is used to look up the verification key, not as signed data).

### Signing

```
signature = ECDSA_sign_secp256k1(signing_privkey, message_hash)
```

Output: DER-encoded signature (typically 70-73 bytes).

### Verification

```
1. Extract signing_id from message
2. Look up signing_pubkey in SigningKeyRegistry
3. Check key is not revoked (key_index >= miner's current_key_index)
4. Compute message_hash from message fields
5. ECDSA_verify_secp256k1(signing_pubkey, message_hash, signature)
```

### Signing Key Derivation (Detailed)

```
Input:
  master_privkey:  32 bytes (payout address private key)
  key_index:       uint32

Process:
  domain = b"p2pool-msg-v1" || pack('<I', key_index)   # 17 bytes
  signing_privkey = HMAC-SHA256(key=master_privkey, msg=domain)  # 32 bytes
  signing_pubkey  = secp256k1_point(signing_privkey)             # 33 bytes compressed
  signing_id      = RIPEMD160(SHA256(signing_pubkey))            # 20 bytes

Output:
  signing_privkey:  32 bytes (for signing)
  signing_pubkey:   33 bytes (announced in shares)
  signing_id:       20 bytes (message attribution)
```

## Payload Formats

### NODE_STATUS (0x01)

Compact JSON payload:

```json
{
  "v": "13.4-604-g02a27df",
  "up": 86400,
  "hr": 1500000,
  "sc": 8640,
  "p": 3,
  "mc": ["DOGE"],
  "cap": ["v36", "mm", "msg"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| v | string | Software version |
| up | int | Uptime in seconds |
| hr | int | Local hashrate (H/s) |
| sc | int | Share chain height |
| p | int | Connected peers |
| mc | string[] | Merged chains (optional) |
| cap | string[] | Capabilities (optional) |

### MINER_MESSAGE (0x02)

UTF-8 encoded text, max 220 bytes.

```
Hello from LVzy9mWFCQDBebZwvdSChevDJTJTxVbazc! First PoW-authenticated message.
```

### POOL_ANNOUNCE (0x03)

UTF-8 encoded text, max 220 bytes.

```
Maintenance window: 2026-02-15 00:00-04:00 UTC. Expect brief downtime.
```

### VERSION_SIGNAL (0x04)

Compact JSON payload:

```json
{
  "ver": 36,
  "feat": ["mm", "segwit", "mweb", "msg"],
  "proto": 3600
}
```

### MERGED_STATUS (0x05)

Compact JSON payload:

```json
{
  "chain": "Dogecoin",
  "sym": "DOGE",
  "h": 5234567,
  "bv": 10000.0,
  "bf": 3
}
```

| Field | Type | Description |
|-------|------|-------------|
| chain | string | Chain name |
| sym | string | Ticker symbol |
| h | int | Block height |
| bv | float | Block value |
| bf | int | Blocks found (local) |

### EMERGENCY (0x10)

UTF-8 encoded text, max 220 bytes.

```
CRITICAL: v36 activation bug found. Upgrade to commit abc1234 immediately.
```

## Deduplication

Messages are deduplicated by their `message_hash` (double-SHA256 of content).
If two shares carry the same message (same type, flags, timestamp, and payload),
only the first received copy is stored.

## Message Expiry

Messages older than 24 hours (86400 seconds) are pruned from the in-memory
store. The store also enforces a maximum of 1000 messages.

## Backward Compatibility

### Old Nodes (V35 and below)

Old nodes do not understand `message_data` in `ref_type`. The field uses
`PossiblyNoneType` with default `b''`, so:

- When parsing V36 shares from V36 nodes: old nodes ignore the field
- When V36 nodes parse shares without messages: `message_data = b''`
- ref_hash computation with `b''` matches the "no messages" case

### Version 1 Protocol

The envelope `version` byte enables future protocol upgrades:
- Version 1 nodes ignore envelopes with `version > 1`
- Future versions can add new fields after existing ones
- Message types 0x06-0x0F and 0x11-0xFF are reserved

## Example: Complete Share with Messages

```
Share V36:
  min_header: { version, previous_block, ... }
  share_info: { share_data, segwit_data, merged_addresses, ... }
  ref_merkle_link: { branch, index }
  last_txout_nonce: 42
  hash_link: { state, length, extra_data }
  merkle_link: { branch, index }

ref_type (hashed into ref_hash, NOT in coinbase):
  identifier: "\xfe\x4f\xe0\x14\x79\x25\x4f\x80"
  share_info: <same as above>
  message_data: <packed envelope>
    version: 1
    envelope_flags: 0x01 (has announcement)
    msg_count: 1
    announcement_len: 57
    announcement:
      signing_id: <20 bytes>
      key_index: 0
      signing_pubkey: <33 bytes compressed>
    message[0]:
      type: 0x02 (MINER_MESSAGE)
      flags: 0x07 (signed + broadcast + persistent)
      timestamp: 1707580800
      payload_len: 14
      payload: "Hello, p2pool!"
      signing_id: <20 bytes>
      sig_len: 71
      signature: <71 bytes DER ECDSA>

  Total message_data: 4 + 57 + 8 + 14 + 20 + 1 + 71 = 175 bytes

ref_hash = merkle(SHA256d(ref_type.pack(identifier, share_info, message_data)))
  └── Includes message → PoW protects "Hello, p2pool!" from tampering
```
