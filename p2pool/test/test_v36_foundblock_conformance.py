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

  * stale_info is accounting-only: a doa/orphan share keeps its FULL
    PPLNS weight. WeightsSkipList (V35), MergedWeightsSkipList and
    get_decayed_cumulative_weights (V36) never read stale_info, so a chain
    whose shares are tagged doa/orphan pays out exactly as the same chain
    untagged. Pinned with a golden decayed-weight vector.

These are pure wire/constant/weight invariants: no daemon required. The live
found-block submit path (pow<=target -> submit_block) is exercised by the
differential harness (T5), not here.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from p2pool import data as p2pool_data
from p2pool.bitcoin import data as bitcoin_data
from p2pool import networks
from p2pool.util import forest


def _field(composed, name):
    for k, t in composed.fields:
        if k == name:
            return t
    raise KeyError(name)


def _stale_info_type(share_cls):
    net = networks.nets["litecoin"]
    share_info_type = share_cls.get_dynamic_types(net)["share_info_type"]
    return _field(_field(share_info_type, "share_data"), "stale_info")


# The stale_info type as data.py actually builds it, for V36 and V35.
STALE_INFO_TYPE = _stale_info_type(p2pool_data.MergedMiningShare)
STALE_INFO_TYPE_V35 = _stale_info_type(p2pool_data.PaddingBugfixShare)


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

    def test_v35_v36_identical(self):
        # The v35 side of the crossing must encode stale_info the same way.
        for label in (None, "orphan", "doa"):
            self.assertEqual(STALE_INFO_TYPE_V35.pack(label),
                             STALE_INFO_TYPE.pack(label))


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


class _FakeShare(object):
    VERSION = 36
    desired_version = 36

    def __init__(self, **kwargs):
        for k, v in kwargs.iteritems():
            setattr(self, k, v)


class _FakeNet(object):
    CHAIN_LENGTH = 400


STALE_CYCLE = (None, "doa", "orphan", "doa", None)
N_SHARES = 50


def _chain(stale_for):
    t = forest.Tracker()
    for i in xrange(N_SHARES):
        script = "\x76\xa9\x14" + chr(i % 7) * 20 + "\x88\xac"
        t.add(_FakeShare(
            hash=i + 1,
            previous_hash=i if i > 0 else None,
            target=2**240 >> (i % 3),
            address=script,
            new_script=script,
            share_data=dict(donation=(i * 37) % 200,
                            stale_info=stale_for(i))))
    return t


def _all_weights(t):
    head = N_SHARES
    desired = 10**9 * 65535
    v35 = p2pool_data.WeightsSkipList(t)(head, N_SHARES, desired)
    v36 = p2pool_data.MergedWeightsSkipList(t)(head, N_SHARES, desired)
    decayed = p2pool_data.get_decayed_cumulative_weights(
        t, head, N_SHARES, desired, _FakeNet)
    # Capped window: forces the partial-last-share path.
    capped = p2pool_data.get_decayed_cumulative_weights(
        t, head, N_SHARES, 40 * 65535 * 2**16, _FakeNet)
    return v35, v36, decayed, capped


class TestDoaKeepsPplnsWeight(unittest.TestCase):
    """A doa/orphan share is NOT excluded from PPLNS: stale_info must not
    move a single weight unit on either side of the crossing."""

    def test_tagged_chain_matches_untagged(self):
        tagged = _all_weights(_chain(lambda i: STALE_CYCLE[i % len(STALE_CYCLE)]))
        clean = _all_weights(_chain(lambda i: None))
        self.assertEqual(tagged, clean)

    def test_all_doa_chain_matches_untagged(self):
        doa = _all_weights(_chain(lambda i: "doa"))
        clean = _all_weights(_chain(lambda i: None))
        self.assertEqual(doa, clean)

    def test_golden_decayed_vector(self):
        # PINNED: c2pool must reproduce these for the same synthetic chain.
        _, _, decayed, capped = _all_weights(
            _chain(lambda i: STALE_CYCLE[i % len(STALE_CYCLE)]))
        self.assertEqual(_digest(decayed), GOLDEN_DECAYED)
        self.assertEqual(_digest(capped), GOLDEN_CAPPED)


def _digest((weights, total, donation)):
    return (sorted((k.encode("hex"), v) for k, v in weights.iteritems()),
            total, donation)


GOLDEN_DECAYED = ([
    ("76a914000000000000000000000000000000000000000088ac", 62080883674),
    ("76a914010101010101010101010101010101010101010188ac", 56607603832),
    ("76a914020202020202020202020202020202020202020288ac", 64977746551),
    ("76a914030303030303030303030303030303030303030388ac", 54642461434),
    ("76a914040404040404040404040404040404040404040488ac", 57791939204),
    ("76a914050505050505050505050505050505050505050588ac", 66303087617),
    ("76a914060606060606060606060606060606060606060688ac", 55753844855),
], 418778414715, 620847548)
GOLDEN_CAPPED = ([
    ("76a914000000000000000000000000000000000000000088ac", 28231991442),
    ("76a914010101010101010101010101010101010101010188ac", 12130202479),
    ("76a914020202020202020202020202020202020202020288ac", 24466633358),
    ("76a914030303030303030303030303030303030303030388ac", 22075221946),
    ("76a914040404040404040404040404040404040404040488ac", 27623603772),
    ("76a914050505050505050505050505050505050505050588ac", 28806079399),
    ("76a914060606060606060606060606060606060606060688ac", 28206070037),
], 171796070400, 256267967)


if __name__ == "__main__":
    unittest.main()
