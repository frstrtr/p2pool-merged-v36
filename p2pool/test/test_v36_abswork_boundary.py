"""
Regression test: V36 abswork must stay packable when cumulative sharechain
work crosses 2**64.

V36 (MergedMiningShare) re-typed share_info['abswork'] from IntType(128)
to VarIntType on the wire.  VarIntType.write raises
ValueError('int too large for varint') for any value >= 2**64, but the
abswork accumulator in generate_transaction kept its historical % 2**128
mask.  The moment the sharechain tip's cumulative work plus one share's
target_to_average_attempts crosses 2**64, every V36 node crash-loops on
share CREATION (deterministic, reboot-proof: the tip is reloaded from the
share store / peers on every restart).

These tests drive the real MergedMiningShare.generate_transaction with a
fake one-share tracker whose tip abswork sits at the boundary.
"""

import unittest

from p2pool import data
from p2pool import networks
from p2pool.bitcoin import data as bitcoin_data
from p2pool.util import pack


NET = networks.nets['litecoin_testnet']

# Simple P2PKH script (20-byte hash160 of zeros)
P2PKH_SCRIPT = '\x76\xa9\x14' + '\x00'*20 + '\x88\xac'


class _FakeShare(object):
    """Minimal stand-in for a sharechain tip share, with only the
    attributes generate_transaction and the PPLNS weight walkers read."""
    VERSION = 36
    desired_version = 36
    merged_addresses = None

    def __init__(self, abswork):
        self.hash = 12345
        self.previous_hash = None
        self.timestamp = 1500000000
        self.target = NET.MAX_TARGET
        self.max_target = NET.MAX_TARGET
        self.absheight = 7
        self.abswork = abswork
        self.share_data = dict(donation=200)
        self.new_script = P2PKH_SCRIPT


class _FakeTracker(object):
    """One-share chain: items + the traversal methods used on this path."""
    def __init__(self, tip):
        self.items = {tip.hash: tip}

    def get_height_and_last(self, start_hash):
        return 1, None

    def get_height(self, start_hash):
        return 1

    def get_chain(self, start_hash, length):
        n = 0
        h = start_hash
        while h is not None and n < length:
            share = self.items[h]
            yield share
            h = share.previous_hash
            n += 1


def _generate_share_info(prev_abswork):
    """Run the real V36 creation path on a tip with the given abswork."""
    tip = _FakeShare(prev_abswork)
    tracker = _FakeTracker(tip)
    share_data = dict(
        previous_share_hash=tip.hash,
        coinbase='\x03\x01\x02\x03',
        nonce=0,
        pubkey_hash=0x1122334455667788990011223344556677889900,
        pubkey_type=0,
        subsidy=625000000,
        donation=200,
        stale_info=None,
        desired_version=36,
    )
    share_info, gentx, other_tx_hashes, get_share = \
        data.MergedMiningShare.generate_transaction(
            tracker=tracker,
            share_data=share_data,
            block_target=NET.MAX_TARGET,
            desired_timestamp=tip.timestamp + 5,
            desired_target=NET.MAX_TARGET,
            ref_merkle_link=dict(branch=[], index=0),
            desired_other_transaction_hashes_and_fees=[],
            net=NET,
            known_txs=None,
            last_txout_nonce=0,
            segwit_data=dict(
                txid_merkle_link=dict(branch=[], index=0),
                wtxid_merkle_root=1,
            ),
            v36_active=True,
        )
    return share_info


class TestV36AbsworkBoundary(unittest.TestCase):

    def test_varint_wire_ceiling_is_2_64(self):
        # Documents the wire-format value domain the accumulator must obey.
        # 2**64-1 is the largest encodable varint; 2**64 must raise.
        self.assertEqual(
            pack.VarIntType().pack(2**64 - 1),
            '\xff' + '\xff'*8,
        )
        self.assertRaises(ValueError, pack.VarIntType().pack, 2**64)

    def test_v36_share_creation_across_2_64_boundary(self):
        # THE INCIDENT: tip abswork at the varint ceiling; adding one
        # share's attempts crosses 2**64.  Unfixed, generate_transaction
        # raises ValueError('int too large for varint') from
        # get_ref_hash -> ref_type.pack -> VarIntType.write and the node
        # crash-loops forever.  Fixed, the accumulator wraps mod 2**64
        # (same idiom as absheight mod 2**32) and the share packs.
        prev_abswork = 2**64 - 1
        share_info = _generate_share_info(prev_abswork)
        attempts = bitcoin_data.target_to_average_attempts(
            share_info['bits'].target)
        self.assertTrue(share_info['abswork'] < 2**64)
        self.assertEqual(share_info['abswork'],
                         (prev_abswork + attempts) % 2**64)
        # And the full ref_type (what get_ref_hash packs) must round-trip.
        ref_type = data.MergedMiningShare.get_dynamic_types(NET)['ref_type']
        ref_dict = dict(identifier=NET.IDENTIFIER, share_info=share_info,
                        message_data=None)
        packed = ref_type.pack(ref_dict)
        self.assertEqual(ref_type.unpack(packed), ref_dict)

    def test_v36_pre_boundary_values_unchanged(self):
        # Consensus safety: below the boundary the wrap is a no-op, so
        # patched and unpatched nodes produce byte-identical shares for
        # every abswork value the old code could actually serve.
        prev_abswork = 2**40
        share_info = _generate_share_info(prev_abswork)
        attempts = bitcoin_data.target_to_average_attempts(
            share_info['bits'].target)
        self.assertEqual(share_info['abswork'], prev_abswork + attempts)

    def test_pre_v36_wire_type_still_128_bit(self):
        # Pre-V36 shares keep IntType(128): a >2**64 abswork must still
        # pack (proves the fix did not touch the pre-V36 wire format).
        v35_type = data.PaddingBugfixShare.get_dynamic_types(NET)[
            'share_info_type']
        share_info = dict(
            share_data=dict(
                previous_share_hash=None,
                coinbase='\x03\x01\x02\x03',
                nonce=0,
                address='mkzvGDLdvbBiRvBftgcz9rreHfSMBR3wYU',
                subsidy=625000000,
                donation=200,
                stale_info=None,
                desired_version=35,
            ),
            segwit_data=None,
            far_share_hash=None,
            max_bits=bitcoin_data.FloatingInteger.from_target_upper_bound(
                NET.MAX_TARGET),
            bits=bitcoin_data.FloatingInteger.from_target_upper_bound(
                NET.MAX_TARGET),
            timestamp=1500000000,
            absheight=7,
            abswork=2**100,  # legal pre-V36, > varint ceiling
        )
        packed = v35_type.pack(share_info)
        self.assertEqual(v35_type.unpack(packed)['abswork'], 2**100)


if __name__ == '__main__':
    unittest.main()
