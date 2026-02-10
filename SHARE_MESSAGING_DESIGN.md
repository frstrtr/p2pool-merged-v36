# P2Pool Share-Based Messaging System — Design Document

## Overview

A decentralized messaging system embedded in P2Pool share data, using share
Proof-of-Work as anti-spam and derived signing keys for authentication.

Messages are part of the share hash (PoW-protected) but NOT part of the
coinbase transaction — they never pollute the parent blockchain.

## Design Goals

1. **PoW-gated**: Only miners who produce valid shares can send messages
2. **Blockchain-clean**: Messages never appear in Litecoin/Dogecoin blocks
3. **Authenticated**: Messages signed with derived keys, verifiable by all nodes
4. **Key-safe**: Master private key (payout address) is never exposed
5. **Rotatable**: Signing keys can be rotated, revoking all previous signatures
6. **Backward-compatible**: Embeds in V36 without breaking existing share format
7. **Zero infrastructure**: No external servers, databases, or PKI — trust is anchored to share PoW

## Why NOT Coinbase OP_RETURN

| Approach | PoW-protected? | Blockchain-clean? | Propagated? |
|----------|:-:|:-:|:-:|
| Coinbase OP_RETURN | ✅ | ❌ pollutes blockchain if block found | ✅ |
| `share_data['coinbase']` suffix | ✅ | ❌ same problem — in coinbase | ✅ |
| Separate P2P message type | ❌ needs own anti-spam | ✅ | ✅ needs new protocol |
| **ref_type extension (chosen)** | **✅** | **✅** | **✅** via existing P2P |

The chosen approach adds a `message_data` field to V36's `ref_type`. This field:
- Is included in `ref_hash` → part of the share merkle tree → protected by PoW
- Is NOT included in the coinbase/gentx → never written to any blockchain
- Propagates with shares via existing p2pool P2P protocol — no new message types

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     P2Pool Share (V36)                        │
├──────────────────────────────────────────────────────────────┤
│ share_info (existing — in coinbase/gentx/blockchain)         │
│   ├── share_data (previous_hash, coinbase, nonce, address…)  │
│   ├── segwit_data                                            │
│   ├── merged_addresses (NEW in V36)                          │
│   └── far_share_hash, bits, timestamp, absheight, abswork    │
├──────────────────────────────────────────────────────────────┤
│ ref_type (PoW-protected via ref_hash, NOT in blockchain)     │
│   ├── identifier (8 bytes)                                   │
│   ├── share_info (same as above)                             │
│   └── message_data (NEW — VarStrType)  ◄── MESSAGES HERE     │
│         ├── signing_key_announcement (57 bytes, optional)     │
│         └── messages[] (up to 3 per share)                   │
├──────────────────────────────────────────────────────────────┤
│ ref_hash = merkle(hash256(ref_type.pack(...)))               │
│   └── Includes message_data → PoW protects messages          │
├──────────────────────────────────────────────────────────────┤
│ gentx/coinbase (existing — written to blockchain)            │
│   └── Does NOT include message_data → blockchain stays clean │
└──────────────────────────────────────────────────────────────┘
```

## Derived Signing Key Scheme

### The Problem

Miners need to sign messages, but exposing the payout address private key
would be catastrophic — an attacker could steal all mining rewards.

### The Solution

Derive a **one-way** signing key from the master private key using HMAC.
The signing key can sign messages but cannot be reversed to obtain the
master key. If the signing key is compromised, the miner rotates to a
new key by incrementing `key_index`.

### Derivation

```
Master Private Key (payout address — NEVER EXPOSED)
    │
    └─── HMAC-SHA256(master_pk, "p2pool-msg-v1" || key_index_le32)
              │
              ├── signing_privkey (32 bytes)
              │     └── Used to ECDSA-sign messages
              │
              ├── signing_pubkey = secp256k1(signing_privkey) (33 bytes compressed)
              │     └── Announced in shares for verification
              │
              └── signing_id = HASH160(signing_pubkey) (20 bytes)
                    └── Short identifier for message attribution
```

### Key Rotation

```
key_index=0 ──► signing_key_A ──► signing_id_A  (announced in share)
                  │
                  ├── Signs messages M1, M2, M3
                  │
                  └── COMPROMISED! Attacker has signing_key_A
                        │
key_index=1 ──► signing_key_B ──► signing_id_B  (announced in NEW share)
                  │
                  ├── SigningKeyRegistry sees key_index=1 > 0
                  ├── REVOKES signing_id_A ──► M1, M2, M3 now UNVERIFIABLE
                  └── Signs new messages with signing_key_B
```

**Properties:**
- Master key stays secret — signing key is one-way derived
- Compromise of signing key only affects message auth, not mining rewards
- Key rotation is immediate — just mine one share with new key_index
- Old messages become unverifiable (revoked), preventing impersonation
- No PKI, no key exchange — trust anchored entirely to share PoW

### Security Analysis

| Attack | Mitigation |
|--------|-----------|
| Steal master private key | Signing key is derived via HMAC — can't reverse |
| Steal signing key | Rotate key_index — old key revoked, new key in next share |
| Forge messages from another miner | Need their signing_privkey — can't derive without master |
| Spam messages | Must mine valid shares (PoW cost) |
| Announce fake signing key | Must mine valid share — PoW prevents sybil |
| Replay old messages | Deduplicated by message_hash in ShareMessageStore |
| Impersonate after key rotation | Old signing_id is revoked — verification fails |

## Message Types

| Type | ID | Signed? | Persistent? | Purpose |
|------|----|---------|-------------|---------|
| NODE_STATUS | 0x01 | Optional | No | Node health: version, uptime, hashrate, peers |
| MINER_MESSAGE | 0x02 | **Required** | Yes | Miner-to-miner text chat |
| POOL_ANNOUNCE | 0x03 | **Required** | Yes | Node operator announcements |
| VERSION_SIGNAL | 0x04 | Optional | No | Extended version signaling with metadata |
| MERGED_STATUS | 0x05 | Optional | No | Merged mining chain status reports |
| EMERGENCY | 0x10 | **Required** | Yes | Security alerts, critical upgrade notices |

## Message Flags

```
Bit 0 (0x01): FLAG_HAS_SIGNATURE  — Message carries an ECDSA signature
Bit 1 (0x02): FLAG_BROADCAST      — Relay to peers (vs. local-only)
Bit 2 (0x04): FLAG_PERSISTENT     — Store in message history (vs. ephemeral)
Bits 3-7:     Reserved for future use
```

## Limits

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max payload per message | 220 bytes | Fits a tweet-length message + JSON metadata |
| Max messages per share | 3 | Keeps share overhead small |
| Max total message bytes per share | 512 bytes | ~0.5 KB overhead on ~2 KB share |
| Signing key announcement | 57 bytes | signing_id(20) + key_index(4) + pubkey(33) |
| Message expiry | 24 hours | Prevents unbounded memory growth |
| Message store capacity | 1000 messages | ~24 hours at typical share rates |

## Use Cases

### 1. Miner Chat
Miners communicate in real-time via signed text messages. Only verified shares
can carry messages — this is the most expensive chat system in the world
(you literally need to perform PoW to send a message).

### 2. Pool Status Dashboard
Node operators broadcast health status: hashrate, peer count, merged chain
availability. Other nodes display this on their dashboards.

### 3. Emergency Alerts
Critical security notices propagate through the share chain. Since they're
signed and PoW-protected, they can't be spoofed.

### 4. Upgrade Coordination
Extended version signaling carries feature flags and capability lists,
enabling richer negotiation than the existing `desired_version` integer.

### 5. Merged Mining Discovery
Nodes announce which merged chains they support and their block heights,
enabling automatic peer discovery for new merged mining chains.

## Implementation Files

| File | Purpose |
|------|---------|
| `p2pool/share_messages.py` | Core module: messages, signing, registry, store |
| `SHARE_MESSAGING_DESIGN.md` | This document — architecture and rationale |
| `SHARE_MESSAGING_PROTOCOL.md` | Wire format specification |
| `SHARE_MESSAGING_INTEGRATION.md` | Integration plan for data.py, work.py, web.py |

## Integration Points (Summary)

1. **data.py**: Add `message_data` to V36 `ref_type`, process in share verification
2. **work.py**: Embed queued messages when generating new shares
3. **web.py**: API endpoints for viewing messages and queuing outbound messages
4. **p2p.py**: No changes needed — messages travel with shares automatically

See `SHARE_MESSAGING_INTEGRATION.md` for detailed integration plan.
