"""
Cross-implementation test: V36 ref_hash and gentx_hash computation.

This test constructs a V36 share with known parameters, builds the
coinbase transaction including sorted PPLNS outputs, computes the
ref_hash (OP_RETURN commitment) and gentx_hash (coinbase txid).

The C++ counterpart (test_v36_cross_impl_refhash.cpp) must produce
identical ref_hash and gentx_hash values for the same inputs.

Tests cover:
1. Main case: 2 P2PKH miners, typical share
2. Edge case: 3 miners with mixed address types (P2PKH + P2WPKH + P2SH)
3. Edge case: genesis share (no previous share)
"""

import struct
import hashlib
import unittest
import sys
import os

# Add parent directory to path so we can import p2pool modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from p2pool.util import pack
from p2pool.bitcoin import data as bitcoin_data


def sha256d(data):
    """Double SHA-256 (Bitcoin hash256)."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def varint_encode(n):
    """Encode a Bitcoin-style VarInt (CompactSize)."""
    if n < 253:
        return struct.pack('<B', n)
    elif n < 0x10000:
        return struct.pack('<BH', 253, n)
    elif n < 0x100000000:
        return struct.pack('<BI', 254, n)
    else:
        return struct.pack('<BQ', 255, n)


def build_ref_stream(identifier, prev_hash, coinbase, nonce, pubkey_hash,
                     pubkey_type, subsidy, donation, stale_info,
                     desired_version, segwit_data, merged_addresses,
                     far_share_hash, max_bits, bits, timestamp, absheight,
                     abswork, merged_coinbase_info, merged_payout_hash,
                     message_data):
    """Build the V36 ref_stream bytes for ref_hash computation.

    Field order matches p2pool's ref_type = ComposedType([
        ('identifier', FixedStrType(8)),
        ('share_info', share_info_type),
        ('message_data', PossiblyNoneType(b'', VarStrType())),
    ])
    """
    buf = b''

    # 1. identifier (8 bytes)
    buf += identifier

    # --- share_info start ---
    # 2. share_data.previous_share_hash: PossiblyNoneType(0, IntType(256))
    if prev_hash is None:
        buf += struct.pack('<32s', b'\x00' * 32)  # none_value = 0
    else:
        buf += prev_hash  # 32 bytes LE

    # 3. coinbase: VarStrType
    buf += varint_encode(len(coinbase)) + coinbase

    # 4. nonce: IntType(32)
    buf += struct.pack('<I', nonce)

    # 5. pubkey_hash: IntType(160) — 20 bytes LE
    buf += pubkey_hash

    # 6. pubkey_type: IntType(8)
    buf += struct.pack('<B', pubkey_type)

    # 7. subsidy: VarIntType
    buf += varint_encode(subsidy)

    # 8. donation: IntType(16)
    buf += struct.pack('<H', donation)

    # 9. stale_info: EnumType(IntType(8), ...)
    buf += struct.pack('<B', stale_info)

    # 10. desired_version: VarIntType
    buf += varint_encode(desired_version)

    # 11. segwit_data: PossiblyNoneType(default, ComposedType([branch_list, wtxid_root]))
    if segwit_data is None:
        # Default: empty branch list + wtxid_merkle_root = 2^256-1
        buf += varint_encode(0)  # branch list length = 0
        # index is IntType(0) — not serialized (0-bit integer)
        buf += b'\xff' * 32  # wtxid_merkle_root = 2^256-1 (none sentinel)
    else:
        branch, wtxid_root = segwit_data
        buf += varint_encode(len(branch))
        for h in branch:
            buf += h  # 32 bytes each
        # index is IntType(0) — not serialized
        buf += wtxid_root  # 32 bytes

    # 12. merged_addresses: PossiblyNoneType([], ListType(...))
    if merged_addresses is None or len(merged_addresses) == 0:
        buf += varint_encode(0)  # empty list
    else:
        buf += varint_encode(len(merged_addresses))
        for entry in merged_addresses:
            buf += struct.pack('<I', entry['chain_id'])
            buf += varint_encode(len(entry['script'])) + entry['script']

    # 13. far_share_hash: PossiblyNoneType(0, IntType(256))
    if far_share_hash is None:
        buf += b'\x00' * 32
    else:
        buf += far_share_hash

    # 14. max_bits: FloatingIntegerType — 4 bytes LE
    buf += struct.pack('<I', max_bits)

    # 15. bits: FloatingIntegerType — 4 bytes LE
    buf += struct.pack('<I', bits)

    # 16. timestamp: IntType(32)
    buf += struct.pack('<I', timestamp)

    # 17. absheight: IntType(32)
    buf += struct.pack('<I', absheight)

    # 18. abswork: VarIntType (V36: was IntType(128))
    buf += varint_encode(abswork)

    # 19. merged_coinbase_info: PossiblyNoneType([], ListType(...))
    if merged_coinbase_info is None or len(merged_coinbase_info) == 0:
        buf += varint_encode(0)
    else:
        buf += varint_encode(len(merged_coinbase_info))
        for entry in merged_coinbase_info:
            buf += struct.pack('<I', entry['chain_id'])
            buf += varint_encode(entry['coinbase_value'])
            buf += varint_encode(entry['block_height'])
            buf += entry['block_header_bytes']  # 80 bytes
            buf += varint_encode(len(entry['coinbase_merkle_link']['branch']))
            for h in entry['coinbase_merkle_link']['branch']:
                buf += h

    # 20. merged_payout_hash: PossiblyNoneType(0, IntType(256))
    if merged_payout_hash is None:
        buf += b'\x00' * 32
    else:
        buf += merged_payout_hash

    # --- share_info end ---

    # 21. message_data: PossiblyNoneType(b'', VarStrType())
    if message_data is None or len(message_data) == 0:
        buf += varint_encode(0)  # empty VarStr = b'\x00'
    else:
        buf += varint_encode(len(message_data)) + message_data

    return buf


def build_v36_coinbase(coinbase_script, subsidy, sorted_outputs, donation_script,
                       donation_amount, ref_hash, last_txout_nonce,
                       segwit_commitment=None):
    """Build V36 coinbase transaction bytes (non-witness serialization for txid).

    Output order: [segwit_commitment] + sorted_payouts + donation + OP_RETURN
    """
    tx = b''

    # version
    tx += struct.pack('<I', 1)

    # vin count
    tx += varint_encode(1)

    # vin[0]: coinbase input
    tx += b'\x00' * 32  # prev_hash = 0
    tx += struct.pack('<I', 0xffffffff)  # prev_index = -1
    tx += varint_encode(len(coinbase_script)) + coinbase_script  # scriptSig
    tx += struct.pack('<I', 0xffffffff)  # sequence

    # count outputs
    n_outs = len(sorted_outputs) + 1 + 1  # payouts + donation + OP_RETURN
    if segwit_commitment is not None:
        n_outs += 1
    tx += varint_encode(n_outs)

    # segwit commitment (if present)
    if segwit_commitment is not None:
        tx += struct.pack('<Q', 0)  # value = 0
        tx += varint_encode(len(segwit_commitment)) + segwit_commitment

    # sorted PPLNS outputs
    for script, amount in sorted_outputs:
        tx += struct.pack('<Q', amount)
        tx += varint_encode(len(script)) + script

    # donation output (LAST value output)
    tx += struct.pack('<Q', donation_amount)
    tx += varint_encode(len(donation_script)) + donation_script

    # OP_RETURN commitment
    op_return_data = '\x6a\x28' + ref_hash + struct.pack('<Q', last_txout_nonce)
    tx += struct.pack('<Q', 0)  # value = 0
    tx += varint_encode(len(op_return_data)) + op_return_data

    # locktime
    tx += struct.pack('<I', 0)

    return tx


# ============================================================================
# Test vectors
# ============================================================================

# Known constants
TESTNET_IDENTIFIER = 'cca5e24ec6408b1e'.decode('hex')
COMBINED_DONATION_SCRIPT = '\xa9\x14\x8c\x62\x72\x62\x1d\x89\xe8\xfa\x52\x6d\xd8\x6a\xcf\xf6\x0c\x71\x36\xbe\x8e\x85\x87'


def make_p2pkh_script(hash160_hex):
    return '\x76\xa9\x14' + hash160_hex.decode('hex') + '\x88\xac'

def make_p2wpkh_script(hash160_hex):
    return '\x00\x14' + hash160_hex.decode('hex')

def make_p2sh_script(hash160_hex):
    return '\xa9\x14' + hash160_hex.decode('hex') + '\x87'


class TestV36CrossImplRefHash(unittest.TestCase):

    def _compute_test_vector(self, name, weights, subsidy, prev_hash,
                              coinbase_script, nonce, pubkey_hash_hex,
                              pubkey_type, donation, timestamp,
                              max_bits, bits, absheight, abswork,
                              far_share_hash, last_txout_nonce):
        """Compute ref_hash and gentx_hash for a test vector, print hex for C++ test."""

        # 1. Compute PPLNS amounts from weights (V36: no haircut)
        total_weight = sum(weights.values())
        amounts = {}
        for script, weight in weights.iteritems():
            amt = subsidy * weight // total_weight
            if amt > 0:
                amounts[script] = amt
        sum_amounts = sum(amounts.itervalues())
        donation_amount = subsidy - sum_amounts

        # V36: ensure donation >= 1
        # Deterministic tiebreak: (amount, script) — largest script wins when equal
        if donation_amount < 1 and subsidy > 0 and amounts:
            largest = max(amounts, key=lambda k: (amounts[k], k))
            amounts[largest] -= 1
            sum_amounts -= 1
            donation_amount = subsidy - sum_amounts

        # 2. Sort outputs V36-style: (amount, script) ascending
        excluded = {COMBINED_DONATION_SCRIPT}
        dests = sorted([s for s in amounts.iterkeys() if s not in excluded],
                       key=lambda s: (amounts[s], s))
        sorted_outputs = [(s, amounts[s]) for s in dests if amounts[s]]

        # 3. Build ref_stream and compute ref_hash
        pubkey_hash = pubkey_hash_hex.decode('hex')
        ref_bytes = build_ref_stream(
            identifier=TESTNET_IDENTIFIER,
            prev_hash=prev_hash,
            coinbase=coinbase_script,
            nonce=nonce,
            pubkey_hash=pubkey_hash,
            pubkey_type=pubkey_type,
            subsidy=subsidy,
            donation=donation,
            stale_info=0,
            desired_version=36,
            segwit_data=None,  # simplified: no segwit for test
            merged_addresses=None,
            far_share_hash=far_share_hash,
            max_bits=max_bits,
            bits=bits,
            timestamp=timestamp,
            absheight=absheight,
            abswork=abswork,
            merged_coinbase_info=None,
            merged_payout_hash=None,
            message_data=None,
        )
        ref_hash = sha256d(ref_bytes)

        # 4. Build coinbase and compute gentx_hash
        coinbase_tx = build_v36_coinbase(
            coinbase_script=coinbase_script,
            subsidy=subsidy,
            sorted_outputs=sorted_outputs,
            donation_script=COMBINED_DONATION_SCRIPT,
            donation_amount=donation_amount,
            ref_hash=ref_hash,
            last_txout_nonce=last_txout_nonce,
        )
        gentx_hash = sha256d(coinbase_tx)

        # Print test vector for C++ test
        print >> sys.stderr, ''
        print >> sys.stderr, '=== Test Vector: %s ===' % name
        print >> sys.stderr, 'ref_stream_len: %d' % len(ref_bytes)
        print >> sys.stderr, 'ref_stream_hex: %s' % ref_bytes.encode('hex')
        print >> sys.stderr, 'ref_hash:       %s' % ref_hash.encode('hex')
        print >> sys.stderr, 'coinbase_len:   %d' % len(coinbase_tx)
        print >> sys.stderr, 'coinbase_hex:   %s' % coinbase_tx.encode('hex')
        print >> sys.stderr, 'gentx_hash:     %s' % gentx_hash.encode('hex')
        print >> sys.stderr, 'sorted_outputs: %d' % len(sorted_outputs)
        for i, (s, a) in enumerate(sorted_outputs):
            print >> sys.stderr, '  out[%d]: amount=%d script=%s' % (i, a, s.encode('hex'))
        print >> sys.stderr, '  donation: amount=%d script=%s' % (donation_amount, COMBINED_DONATION_SCRIPT.encode('hex'))

        return ref_hash, gentx_hash, ref_bytes, sorted_outputs, donation_amount

    def test_main_case_two_p2pkh_miners(self):
        """Main case: 2 P2PKH miners with different weights."""
        weights = {
            make_p2pkh_script('aa' * 20): 7000000,
            make_p2pkh_script('bb' * 20): 3000000,
        }
        prev_hash = ('11' * 32).decode('hex')
        coinbase_script = '/c2pool/test/'.encode('hex').decode('hex')  # arbitrary
        coinbase_script = '/c2pool/test/'

        ref_hash, gentx_hash, ref_bytes, sorted_outputs, don_amt = self._compute_test_vector(
            name='2xP2PKH',
            weights=weights,
            subsidy=5000000000,
            prev_hash=prev_hash,
            coinbase_script=coinbase_script,
            nonce=0,
            pubkey_hash_hex='aa' * 20,
            pubkey_type=0,
            donation=50,
            timestamp=1700000000,
            max_bits=0x1e0fffff,
            bits=0x1e0fffff,
            absheight=100,
            abswork=12345,
            far_share_hash=None,
            last_txout_nonce=0x0102030405060708,
        )

        # Verify output order: amount ascending
        # bb has weight 3M → amount = 5B * 3M / 10M = 1500000000
        # aa has weight 7M → amount = 5B * 7M / 10M = 3499999999 (integer division)
        # donation = 5B - 1500000000 - 3499999999 = 1
        self.assertEqual(len(sorted_outputs), 2)
        self.assertEqual(sorted_outputs[0][1], 1500000000)  # bb first (lower amount)
        self.assertEqual(sorted_outputs[1][1], 3499999999)  # aa second (rounding)

        # Verify ref_hash is non-zero
        self.assertNotEqual(ref_hash, '\x00' * 32)
        # Verify gentx_hash is non-zero
        self.assertNotEqual(gentx_hash, '\x00' * 32)

    def test_edge_case_mixed_types(self):
        """Edge case: P2PKH + P2WPKH + P2SH with different weights.
        Uses different weights to avoid non-deterministic tiebreaking
        when 'deduct 1 from largest' hits equal amounts."""
        hash160 = 'dd' * 20
        weights = {
            make_p2pkh_script(hash160): 1500000,
            make_p2wpkh_script(hash160): 1000000,
            make_p2sh_script(hash160): 500000,
        }
        prev_hash = ('22' * 32).decode('hex')

        ref_hash, gentx_hash, ref_bytes, sorted_outputs, don_amt = self._compute_test_vector(
            name='mixed_equal',
            weights=weights,
            subsidy=3000000000,
            prev_hash=prev_hash,
            coinbase_script='test',
            nonce=42,
            pubkey_hash_hex='dd' * 20,
            pubkey_type=0,
            donation=50,
            timestamp=1700000001,
            max_bits=0x1e0fffff,
            bits=0x1d00ffff,
            absheight=200,
            abswork=99999,
            far_share_hash=('33' * 32).decode('hex'),
            last_txout_nonce=0xdeadbeefcafe1234,
        )

        # Different weights → different amounts → unambiguous sort order
        # P2SH:   3B * 0.5M / 3M = 500000000
        # P2WPKH: 3B * 1M / 3M   = 1000000000
        # P2PKH:  3B * 1.5M / 3M = 1499999999 (int division rounding)
        # donation = 3B - 500M - 1B - 1499999999 = 1
        # Sort ascending by amount: P2SH < P2WPKH < P2PKH
        self.assertEqual(len(sorted_outputs), 3)
        self.assertEqual(sorted_outputs[0][1], 500000000)    # P2SH
        self.assertEqual(sorted_outputs[1][1], 1000000000)   # P2WPKH
        self.assertEqual(sorted_outputs[2][1], 1499999999)   # P2PKH (rounding)

    def test_edge_case_genesis_share(self):
        """Edge case: genesis share (no previous share, no far_share_hash)."""
        weights = {
            make_p2pkh_script('ff' * 20): 5000000,
        }

        ref_hash, gentx_hash, ref_bytes, sorted_outputs, don_amt = self._compute_test_vector(
            name='genesis',
            weights=weights,
            subsidy=1000000000,
            prev_hash=None,  # genesis: no previous share
            coinbase_script='genesis',
            nonce=0,
            pubkey_hash_hex='ff' * 20,
            pubkey_type=0,
            donation=50,
            timestamp=1700000002,
            max_bits=0x1e0fffff,
            bits=0x1e0fffff,
            absheight=0,
            abswork=0,
            far_share_hash=None,
            last_txout_nonce=0,
        )

        self.assertEqual(len(sorted_outputs), 1)
        # Single miner gets (almost) all
        self.assertGreater(sorted_outputs[0][1], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
