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
#   * the root must be the tree over exactly the carried blocks
#     (merged_aux_commitment_problem); the CROSS vectors and the generated
#     PARITY vectors (5,000, pinned verdict digest) are pinned in c2pool's
#     merged_aux_hardening_test.cpp too (the same rule there:
#     src/c2pool/merged/aux_commitment_rule.hpp): both give the same verdicts;
#   * this release the rule only logs (MERGED_RULE_STATS) except where python
#     master already refused the share (aux size other than 1 on the DOGE
#     check): no share master accepted is refused, and the only shares master
#     refused that are accepted now are the well-formed multi-chain ones;
#   * work.py commits exactly the carried chains once any is carried
#     (select_committed_merged_work): its own DOGE + getauxblock-chain
#     shares were refused by its peers;
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

# Cross-implementation vectors (c2pool merged_aux_hardening_test.cpp
# MergedAuxCommitment.CrossImplementationVectors): chain ids, tree size, and
# the root over the headers kat_header(id) at their slots ('%064x', the
# uint256 GetHex form).
CROSS = [
    ([DOGE, PEP], 2, 'ff2edbdd2355c879e4cee6ef4014c9c060d04ca42ca3970c989826a7ef7f7990'),
    ([DOGE, PEP, NMC], 4, '1e38a42508658108f37c3eb2b7dfcddae24d9ebbbcf724da7f200088fd36e401'),
    ([DOGE, SHIC], 16, '528fe2cd8a23303a26e5b6b1c46d2dd3543d89a74edba2a8192c6c4013ff2db7'),
    ([DOGE, PEP, BELLS, LKY, JKC, SHIC, NMC], 32, 'bb58ff2387da0d494ff1bb509a3158341f161896c43c3e3ccda775ea7341c462'),
]


def kat_header(cid):
    return ''.join(chr((cid * 7 + i) & 0xff) for i in xrange(80))


def kat_infos(ids):
    return [dict(chain_id=c, block_header_bytes=kat_header(c)) for c in ids]


# ---- the generated parity vectors (the same generator in c2pool's test) ----
M64 = (1 << 64) - 1


class SplitMix64(object):
    def __init__(self, seed):
        self.s = seed & M64

    def next(self):
        self.s = (self.s + 0x9E3779B97F4A7C15) & M64
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
        return z ^ (z >> 31)

    def below(self, n):
        return self.next() % n


def _sha256d(b):
    import hashlib
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


PARITY_REAL = [98, 63, 16, 8211, 8224, 8225, 74, 50, 1, 2, 7]
PARITY_SPECIAL = [5, 5 + (1 << 30), 5 + (1 << 29), 5 + (1 << 12), 5 + (1 << 11), 0xffffffff, 0]
PARITY_KS = [1, 1, 2, 2, 2, 3, 3, 4, 5, 6, 8]
PARITY_SEED, PARITY_N = 2026, 5000
PARITY_DIGEST = 'a37fc58712ab0392114cd05d1e6cf13432ab577a3aaab06200b3d76f7677a83c'
PARITY_COUNTS = {'A': 1896, 'R:big': 131, 'R:dup': 1121, 'R:hdr': 296, 'R:root': 671, 'R:size': 885}


def _parity_id(r):
    c = r.below(100)
    if c < 60: return PARITY_REAL[r.below(len(PARITY_REAL))]
    if c < 75: return r.below(1 << 16)
    if c < 85: return r.next() & 0xffffffff
    if c < 93: return PARITY_SPECIAL[r.below(len(PARITY_SPECIAL))]
    return 98 + (1 << (1 + r.below(30)))


def _parity_tree(ids):
    for i in xrange(31):
        size = 1 << i
        if size < len(ids): continue
        slots = {}
        ok = True
        for cid in ids:
            pos = (1103515245 * cid + 1103515245 * 12345 + 12345) % size
            if pos in slots.values():
                ok = False
                break
            slots[cid] = pos
        if ok: return size, slots
    return 0, {}


def _parity_merkle(size, leaves):
    layer = [leaves.get(i, '\0' * 32) for i in xrange(size)]
    while len(layer) > 1:
        layer = [_sha256d(layer[2 * k] + layer[2 * k + 1]) for k in xrange(len(layer) // 2)]
    return layer[0]


def _parity_bytes(r, n):
    return ''.join(chr(r.next() & 0xff) for _ in xrange(n))


def parity_vector(r):
    """(root bytes as the uint256's, size, [(chain id, header bytes)])."""
    ids = [_parity_id(r) for _ in xrange(PARITY_KS[r.below(len(PARITY_KS))])]
    if r.below(100) < 8: ids.append(ids[r.below(len(ids))])
    carried = []
    for cid in ids:
        hl = 80
        if r.below(100) < 4: hl = [0, 79, 81, 160][r.below(4)]
        carried.append((cid, _parity_bytes(r, hl)))
    uniq = []
    for c in ids:
        if c not in uniq: uniq.append(c)
    size, slots = _parity_tree(uniq)
    good = _parity_merkle(size, dict((slots[c], _sha256d(h)) for c, h in carried)) if 0 < size <= 4096 else '\0' * 32
    mode = r.below(100)
    if mode < 45: return good, size, carried
    if mode < 55:
        b = bytearray(good)
        i = r.below(32)
        b[i] ^= 1 << r.below(8)
        return str(b), size, carried
    if mode < 62:
        opts = [0, 1, 2, 4, size * 2, max(size // 2, 1), 1 << 31, 4096, 8192, r.next() & 0xffffffff]
        return good, opts[r.below(len(opts))] & 0xffffffff, carried
    if mode < 75:
        extra = [_parity_id(r) for _ in xrange(r.below(2) + 1)]
        allids = list(uniq)
        for c in extra:
            if c not in allids: allids.append(c)
        s2, sl2 = _parity_tree(allids)
        if 0 < s2 <= 4096:
            leaves = dict((sl2[c], _sha256d(h)) for c, h in carried)
            for c in extra:
                if c not in uniq and r.below(2) == 0: leaves[sl2[c]] = _parity_bytes(r, 32)
            return _parity_merkle(s2, leaves), s2, carried
        return good, size, carried
    if mode < 82 and len(carried) == 1:
        h = carried[0][1]
        rt = _sha256d(h[:80]) if r.below(10) < 7 else _parity_bytes(r, 32)
        return rt, [1, 1, 1, 2, 0][r.below(5)], carried
    if mode < 90:
        if 0 < size <= 4096 and len(uniq) >= 2:
            leaves = dict((i % size, _sha256d(h)) for i, (c, h) in enumerate(carried))
            return _parity_merkle(size, leaves), size, carried
        return good, size, carried
    rt = _parity_bytes(r, 32)
    sz = (r.next() & 0xffffffff) if r.below(10) < 3 else [1, 2, 4, 8][r.below(4)]
    return rt, sz, carried


def parity_class(why):
    if not why: return 'A'
    for key, cls in (('twice', 'R:dup'), ('larger than', 'R:big'), ('size mismatch', 'R:size'),
                     ('not 80 bytes', 'R:hdr'), ('aux root', 'R:root')):
        if key in why: return cls
    return 'R:other'


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
        return bitcoin_data.make_auxpow_tree(dict((c, None) for c in ids))[1]

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
class CommitmentRule(unittest.TestCase):
    """check_merged_aux_commitment on the cross-implementation vectors."""

    def check(self, infos, root, size):
        f = getattr(p2pool_data, 'check_merged_aux_commitment', None)
        if f is None:
            self.fail('data.check_merged_aux_commitment missing (pre-fix tree)')
        return f(infos, root, size)

    def test_vectors_accepted(self):
        for ids, size, root in CROSS:
            self.check(kat_infos(ids), int(root, 16), size)
            self.check(kat_infos(list(reversed(ids))), int(root, 16), size)   # order does not matter

    def test_vectors_refused(self):
        for ids, size, root in CROSS:
            r = int(root, 16)
            self.assertRaises(ValueError, self.check, kat_infos(ids), r, size * 2)        # size
            self.assertRaises(ValueError, self.check, kat_infos(ids), r ^ 1, size)        # root
            self.assertRaises(ValueError, self.check, kat_infos(ids[:-1]), r, size)       # a chain missing
            dup = kat_infos(ids) + kat_infos(ids[:1])
            self.assertRaises(ValueError, self.check, dup, r, size)                       # a chain twice
            other = kat_infos(ids)
            other[0] = dict(chain_id=other[0]['chain_id'], block_header_bytes=kat_header(5))
            self.assertRaises(ValueError, self.check, other, r, size)                     # another block

    def test_single_chain_left_to_the_per_chain_check(self):
        self.check(kat_infos([DOGE]), 12345, 1)   # not judged here
        self.assertRaises(ValueError, self.check, kat_infos([DOGE]), 12345, 2)

    def test_tree_too_large_refused(self):
        # equal mod 2^12: distinct only in a tree of 8192 slots
        self.assertRaises(ValueError, self.check, kat_infos([5, 5 + 2 ** 12]), 0, 8192)
        # equal mod 2^30: no tree at all
        self.assertRaises(ValueError, self.check, kat_infos([5, 5 + 2 ** 30]), 0, 2)


@unittest.skipUnless(HAVE_DEPS, 'p2pool package not importable: %s' % (_IMPORT_ERR,))
class ParityVectors(unittest.TestCase):
    """5,000 generated vectors: this implementation's verdicts, digested,
    equal c2pool's (MergedAuxCommitment.ParityVectors pins the same digest)."""

    def test_digest(self):
        import hashlib
        from p2pool.util import pack
        r = SplitMix64(PARITY_SEED)
        dig = hashlib.sha256()
        counts = {}
        for _ in xrange(PARITY_N):
            root_bytes, size, carried = parity_vector(r)
            infos = [dict(chain_id=c, block_header_bytes=h) for c, h in carried]
            v = parity_class(p2pool_data.merged_aux_commitment_problem(
                infos, pack.IntType(256).unpack(root_bytes), size))
            dig.update(v + '\n')
            counts[v] = counts.get(v, 0) + 1
        self.assertEqual(counts, PARITY_COUNTS)
        self.assertEqual(dig.hexdigest(), PARITY_DIGEST)


@unittest.skipUnless(HAVE_DEPS, 'p2pool package not importable: %s' % (_IMPORT_ERR,))
class WorkPyCommitsTheCarriedChains(unittest.TestCase):
    """work.select_committed_merged_work: on a V36 share that carries some
    chains, the tree holds exactly them."""

    def setUp(self):
        try:
            from p2pool import work as p2pool_work
        except Exception as e:
            self.skipTest('p2pool.work not importable: %r' % (e,))
        self.select = getattr(p2pool_work, 'select_committed_merged_work', None)
        if self.select is None:
            self.fail('work.select_committed_merged_work missing (pre-fix tree)')

    def test_carried_only(self):
        doge = dict(hash=1, merged_coinbase_info_entry=dict(chain_id=DOGE))
        pep_getauxblock = dict(hash=2)   # no prebuilt block: not carried
        work = {DOGE: doge, PEP: pep_getauxblock}
        self.assertEqual(self.select(work, True), {DOGE: doge})
        self.assertEqual(self.select(work, False), work)              # pre-V36: unchanged
        self.assertEqual(self.select({PEP: pep_getauxblock}, True), {PEP: pep_getauxblock})   # none carried
        self.assertEqual(self.select({}, True), {})


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

    def share(self, ids, size=None, root=None):
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
            merkle_root=bitcoin_data.merkle_hash(mm_hashes) if root is None else root,
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

    def test_root_not_over_the_carried_blocks_rejected(self):
        # size 2 on the DOGE check: python master refused it; still refused
        self.assertRaises(ValueError, self.verify, self.share([DOGE, PEP], root=12345))

    def test_tree_over_more_chains_than_carried_rejected(self):
        # what work.py built before select_committed_merged_work (and c2pool up
        # to this release with an unproductive second chain): the tree over
        # DOGE + PEP, merged_coinbase_info for DOGE only. Master refused it too.
        s = self.share([DOGE, PEP])
        s.share_info['merged_coinbase_info'] = [i for i in s.share_info['merged_coinbase_info']
                                                if i['chain_id'] == DOGE]
        self.assertRaises(ValueError, self.verify, s)

    def stats(self):
        return dict(p2pool_data.MERGED_RULE_STATS)

    def test_log_only_where_master_accepted(self):
        # Shapes python master accepted: still accepted, the rule only counts
        # them. PEP alone (an unknown net: no check), a commitment of size 1
        # whose root is the DOGE block although PEP is carried too, and DOGE
        # without V36 weights in the window.
        before = self.stats()['would_reject']
        self.verify(self.share([PEP], size=4, root=7))
        s = self.share([DOGE, PEP])
        doge_hash = bitcoin_data.hash256(s.share_info['merged_coinbase_info'][0]['block_header_bytes']) \
            if s.share_info['merged_coinbase_info'][0]['chain_id'] == DOGE else \
            bitcoin_data.hash256(s.share_info['merged_coinbase_info'][1]['block_header_bytes'])
        self.verify(self.share([DOGE, PEP], size=1, root=doge_hash))
        p2pool_data.get_v36_merged_weights = lambda *a, **k: ({}, 0, 0)
        self.verify(self.share([DOGE, PEP], size=4))
        self.assertEqual(self.stats()['would_reject'], before + 3)
        self.assertEqual(self.stats()['mode'], 'log-only')


if __name__ == '__main__':
    unittest.main()
