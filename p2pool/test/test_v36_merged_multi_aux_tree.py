# -*- coding: utf-8 -*-
# Python 2.7 KAT: the V36 merged-commitment check with more than one merged
# chain (data.verify_merged_coinbase_commitment).
#
# A share's parent coinbase commits (mm_data) to the root of the AuxPoW chain
# merkle tree over the merged chains it carries in merged_coinbase_info, each at
# its CAuxPow::getExpectedIndex slot (nonce 0), in the smallest power-of-two
# tree that separates them: what work.py builds (make_auxpow_tree over the
# chains with work) and what c2pool builds (AuxPowTree::build over the chains
# committed in that round). The check computed the expected size from the one
# chain being verified, so every share carrying two or more chains (aux_size 2
# and up) failed with DeterministicShareCheckFailure: python peers rejected
# the shares of any node merge-mining DOGE with a second chain.
#
#   * the expected size is make_auxpow_tree over ALL of the share's chain ids;
#   * the sizes are pinned to c2pool's AuxPowTree::build KATs (the same chain
#     sets give the same sizes), and the slots to getExpectedIndex in 32 bits;
#   * one chain: unchanged (size 1, the root is the block hash).
#
# RED on the pre-fix tree (DOGE+PEP at size 2 raises), GREEN with the fix.
# Self-skips if the p2pool package / twisted cannot be imported.

import unittest

try:
    from p2pool import data as p2pool_data
    from p2pool.bitcoin import data as bitcoin_data
    HAVE_DEPS = True
    _IMPORT_ERR = None
except Exception as _e:
    p2pool_data = bitcoin_data = None
    HAVE_DEPS = False
    _IMPORT_ERR = repr(_e)

DOGE, PEP, BELLS, LKY, JKC, SHIC, NMC = 98, 63, 16, 8211, 8224, 74, 1


def get_expected_index(nonce, chain_id, h):
    # CAuxPow::getExpectedIndex (namecoin/dogecoin auxpow.cpp), uint32 arithmetic.
    rand = nonce
    rand = (rand * 1103515245 + 12345) & 0xffffffff
    rand = (rand + chain_id) & 0xffffffff
    rand = (rand * 1103515245 + 12345) & 0xffffffff
    return rand % (1 << h)


class _Bits(object):
    target = 2 ** 256 - 1


class _Share(object):
    def __init__(self, coinbase_script, infos):
        sd = {'coinbase': coinbase_script, 'previous_share_hash': 0x1234,
              'pubkey_hash': 0x55, 'pubkey_type': 0}
        self.share_info = {'merged_coinbase_info': infos, 'share_data': sd}
        self.share_data = sd
        self.header = {'bits': _Bits()}


class _Tracker(object):
    def get_height(self, h):
        return 10 ** 6


class _Net(object):
    REAL_CHAIN_LENGTH = 10


def _canonical_tx():
    return dict(version=1, tx_ins=[dict(previous_output=None, sequence=None, script='\x51')],
                tx_outs=[dict(value=1, script='\x51')], lock_time=0)


@unittest.skipUnless(HAVE_DEPS, 'p2pool package not importable: %s' % (_IMPORT_ERR,))
class MultiAuxTreeSize(unittest.TestCase):

    def size(self, ids):
        f = getattr(p2pool_data, 'expected_merged_aux_tree_size', None)
        if f is None:
            self.fail('data.expected_merged_aux_tree_size missing (pre-fix tree)')
        return f([dict(chain_id=c) for c in ids])

    def test_one_chain_is_size_one(self):
        for cid in (DOGE, PEP, NMC, SHIC):
            self.assertEqual(self.size([cid]), 1)

    def test_sizes_match_c2pool_auxpow_tree(self):
        # c2pool src/impl/ltc/test/auxpow_tree_slot_test.cpp and
        # merged_aux_hardening_test.cpp (AuxPowTree::build).
        self.assertEqual(self.size([DOGE, NMC]), 2)
        self.assertEqual(self.size([DOGE, PEP]), 2)
        self.assertEqual(self.size([DOGE, NMC, 20, 22]), 8)
        self.assertEqual(self.size([DOGE, PEP, BELLS, LKY, JKC, SHIC, NMC]), 32)
        # order does not matter, a repeated id is one chain
        self.assertEqual(self.size([PEP, DOGE, PEP]), 2)

    def test_slots_are_get_expected_index(self):
        for ids in ([DOGE, PEP], [DOGE, PEP, NMC], [DOGE, NMC, 20, 22],
                    [DOGE, PEP, BELLS, LKY, JKC, SHIC, NMC], [DOGE, SHIC]):
            tree, size = bitcoin_data.make_auxpow_tree(dict((c, None) for c in ids))
            h = size.bit_length() - 1
            self.assertEqual(size, 1 << h)
            for pos, cid in tree.items():
                self.assertEqual(pos, get_expected_index(0, cid, h), (ids, cid))
            # the smallest: half the size cannot separate them
            if size > 1 and size // 2 >= len(ids):
                slots = [get_expected_index(0, c, h - 1) for c in ids]
                self.assertNotEqual(len(set(slots)), len(slots))


@unittest.skipUnless(HAVE_DEPS, 'p2pool package not importable: %s' % (_IMPORT_ERR,))
class VerifyMultiAuxCommitment(unittest.TestCase):
    """verify_merged_coinbase_commitment end to end, with the PPLNS
    re-derivation stubbed (it is not what is under test): every chain's header
    commits to the canonical coinbase, the parent coinbase to the tree."""

    PATCHED = ('get_v36_merged_weights', '_get_merged_chain_net',
               'get_canonical_merged_finder_script', 'build_canonical_merged_coinbase')

    def setUp(self):
        self.saved = dict((n, getattr(p2pool_data, n)) for n in self.PATCHED)
        p2pool_data.get_v36_merged_weights = lambda *a, **k: ({1: 1}, 1, 0)
        # Like the real one: DOGE is known, the other chains are skipped.
        p2pool_data._get_merged_chain_net = lambda cid, net: object() if cid == DOGE else None
        p2pool_data.get_canonical_merged_finder_script = lambda *a, **k: None
        p2pool_data.build_canonical_merged_coinbase = lambda *a, **k: _canonical_tx()

    def tearDown(self):
        for n, f in self.saved.items():
            setattr(p2pool_data, n, f)

    def share(self, ids, size=None):
        txid = bitcoin_data.hash256(bitcoin_data.tx_id_type.pack(_canonical_tx()))
        infos, hashes = [], {}
        for cid in ids:
            header = dict(version=(cid << 16) | 0x104, previous_block=cid, merkle_root=txid,
                          timestamp=1793210000, bits=bitcoin_data.FloatingInteger(0x207fffff), nonce=0)
            hb = bitcoin_data.block_header_type.pack(header)
            hashes[cid] = bitcoin_data.hash256(hb)
            infos.append(dict(chain_id=cid, coinbase_value=1, block_height=1, block_header_bytes=hb,
                              coinbase_merkle_link=dict(branch=[], index=0), coinbase_script=''))
        tree, tsize = bitcoin_data.make_auxpow_tree(dict((c, None) for c in ids))
        mm_hashes = [hashes[tree[i]] if i in tree else 0 for i in xrange(tsize)]
        mm = '\xfa\xbemm' + bitcoin_data.aux_pow_coinbase_type.pack(dict(
            merkle_root=bitcoin_data.merkle_hash(mm_hashes),
            size=tsize if size is None else size, nonce=0))
        return _Share('\x03\x01\x02\x03' + mm + '\x00' * 8, infos)

    def verify(self, share):
        return p2pool_data.verify_merged_coinbase_commitment(share, _Tracker(), _Net(), None)

    def test_single_chain_unchanged(self):
        self.verify(self.share([DOGE]))
        self.assertRaises(ValueError, self.verify, self.share([DOGE], size=2))

    def test_two_chains_accepted(self):
        # c2pool / work.py commit DOGE+PEP in a tree of 2: the pre-fix check
        # expected 1 (DOGE alone) and rejected the share.
        self.verify(self.share([DOGE, PEP]))
        self.verify(self.share([PEP, DOGE]))

    def test_three_chains_accepted(self):
        self.verify(self.share([DOGE, PEP, NMC]))
        self.verify(self.share([DOGE, PEP, BELLS, LKY, JKC, SHIC, NMC]))

    def test_wrong_size_rejected(self):
        for ids, bad in (([DOGE, PEP], 1), ([DOGE, PEP], 4), ([DOGE, PEP, NMC], 2), ([DOGE, NMC], 8)):
            self.assertRaises(ValueError, self.verify, self.share(ids, size=bad))


if __name__ == '__main__':
    unittest.main()
