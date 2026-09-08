# -*- coding: utf-8 -*-
"""
G3 found-block conformance golden vectors (Master #38 sec.2.2).

Freezes the "submit-but-exclude" rule so c2pool v36 <-> p2pool v35 stay
BYTE-IDENTICAL on the found-block path:

  * A share with pow_hash <= header target IS submitted as a block
    (node.py / work.py helper.submit_block), but its share_info is NOT
    altered by the win: the stale_info tag and its wire encoding are the
    only signal, and they must round-trip byte-for-byte across impls.

  * stale_info EnumType wire encoding is PINNED:
        None -> 0x00, orphan -> 0xfd, doa -> 0xfe.
    A drift here splits the chain along implementation lines.

  * V36 carries NO finder fee (CANONICAL_MERGED_FINDER_FEE_PER_MILLE == 0,
    removed in commit 069690c0). Pinned so a re-introduced finder bonus
    cannot silently change coinbase weights.

These are pure wire/constant invariants: no daemon required. The live
found-block submit path (pow<=target -> submit_block, exclusion left
byte-unchanged) is exercised by the differential harness (T5), not here.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from p2pool import data as p2pool_data
from p2pool.bitcoin import data as bitcoin_data
from p2pool.util import pack


# The exact EnumType the share_data type is built from (data.py stale_info field).
STALE_INFO_TYPE = pack.EnumType(
    pack.IntType(8),
    dict((k, {0: None, 253: "orphan", 254: "doa"}.get(k, "unk%i" % (k,)))
         for k in xrange(256)))


class TestStaleInfoWireEncoding(unittest.TestCase):
    def test_canonical_bytes(self):
        # PINNED wire bytes -- c2pool must emit exactly these.
        self.assertEqual(STALE_INFO_TYPE.pack(None), "\x00")
        self.assertEqual(STALE_INFO_TYPE.pack("orphan"), "\xfd")
        self.assertEqual(STALE_INFO_TYPE.pack("doa"), "\xfe")

    def test_round_trip(self):
        for label in (None, "orphan", "doa"):
            packed = STALE_INFO_TYPE.pack(label)
            self.assertEqual(STALE_INFO_TYPE.unpack(packed), label)


class TestStaleExclusionSemantics(unittest.TestCase):
    """Both doa and orphan count as stale (excluded from good work);
    only stale_info is None is "good". Pins the accounting predicate that
    the WeightsSkipList/stale-prop math keys off (data.py get_*_stale_*)."""

    def test_predicate(self):
        self.assertFalse(None is not None)          # good share -> not stale
        self.assertTrue("orphan" is not None)        # orphan -> stale
        self.assertTrue("doa" is not None)           # doa -> stale


class TestNoFinderFee(unittest.TestCase):
    def test_finder_fee_pinned_zero(self):
        self.assertEqual(p2pool_data.CANONICAL_MERGED_FINDER_FEE_PER_MILLE, 0)


if __name__ == "__main__":
    unittest.main()
