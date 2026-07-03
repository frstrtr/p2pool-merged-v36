'''
Differential gate for the gentx template cache (DOA-under-load fix, G1).

Asserts BYTE-IDENTICAL get_work() output between the OLD per-connection path
(WorkerBridge.use_gentx_template_cache = False -> monolithic
Share.generate_transaction) and the NEW cached-template + per-user finalize
path, across a matrix of users x tx-sets x merged-address variants.

This is consensus-critical: any byte difference in the gentx / share_info /
merkle link would fork the sharechain against v35/v36 peers.  THE STEWARD
RUNS THIS ON THE SAFENET BEFORE THE PR IS MERGED, in both ratchet states
(AutoRatchet at v35/PaddingBugfixShare and at v36) so the v35<->v36 crossing
is covered.

Usage, from a running safenet node (manhole / --debug console / a small
driver after WorkerBridge construction):

    from p2pool.test import test_getwork_differential as diff
    d = diff.run_across_work_events(wb, users=diff.default_users(8),
                                    merged_variants=[None, {}],
                                    events=10)   # 10 distinct GBT tx-sets
    # d is a Deferred; it errbacks with an AssertionError listing every
    # mismatch, or callbacks with a summary string on success.

Determinism: random.randrange (coinbase nonce) and time.time (timestamps,
whale/vardiff inputs) are frozen around each old/new call pair.  Both
get_work() calls run synchronously in one reactor turn, so no share or GBT
update can land between them.
'''

from __future__ import division

import random
import time
import unittest

from twisted.internet import defer

from p2pool.bitcoin import data as bitcoin_data


class _FrozenDeterminism(object):
    '''Freeze time.time and random.randrange for the duration of one
    old-path/new-path call pair.  Safe because the wrapped calls are fully
    synchronous (the reactor is not re-entered while frozen).'''
    def __enter__(self):
        self._real_time = time.time
        self._real_randrange = random.randrange
        now = self._real_time()
        time.time = lambda: now
        random.randrange = lambda *a, **k: 0x5AFE5AFE % (a[0] if a else 2**32)
        return self
    def __exit__(self, *exc):
        time.time = self._real_time
        random.randrange = self._real_randrange
        return False


def default_users(n=8):
    '''(user, pubkey_hash, pubkey_type) matrix. pubkey_type cycles P2PKH /
    P2WPKH / P2SH.  TODO(maintainer): restrict types to what the safenet
    PARENT actually supports if address round-tripping rejects any.'''
    base = 0x0123456789abcdef0123456789abcdef01234567
    return [('difftest_%d' % i, (base * (i + 1)) % 2**160, i % 3) for i in xrange(n)]


def _canon_link(link):
    return (tuple(link['branch']), link['index'])


def _fake_header(ba, cap):
    # A header consistent with this path's own gentx + merkle link; PoW is
    # irrelevant for byte comparison.
    merkle_root = bitcoin_data.check_merkle_link(bitcoin_data.get_txid(cap['gentx']), cap['merkle_link'])
    return dict(version=ba['version'], previous_block=ba['previous_block'],
                merkle_root=merkle_root, timestamp=ba['timestamp'],
                bits=ba['bits'], nonce=0)


def compare_one(wb, user, pubkey_hash, pubkey_type, merged_addresses):
    '''Run get_work() through the OLD path then the NEW path (cold cache,
    then warm cache) with frozen determinism, and return a list of mismatch
    descriptions (empty == byte-identical).'''
    from p2pool import data as p2pool_data
    mismatches = []
    orig_flag = wb.use_gentx_template_cache
    orig_capture = wb.differential_capture
    frozen = _FrozenDeterminism()
    frozen.__enter__()
    try:
        # OLD (reference) path
        wb.differential_capture = cap_old = {}
        wb.use_gentx_template_cache = False
        ba_old, got_old = wb.get_work(user, pubkey_hash, pubkey_type, None, None, merged_addresses)
        # NEW path, cold template build
        wb.differential_capture = cap_new = {}
        wb.use_gentx_template_cache = True
        wb._gentx_template_cache = None
        ba_new, got_new = wb.get_work(user, pubkey_hash, pubkey_type, None, None, merged_addresses)
        # NEW path again, warm cache hit (verifies the memoization key)
        wb.differential_capture = cap_warm = {}
        ba_warm, got_warm = wb.get_work(user, pubkey_hash, pubkey_type, None, None, merged_addresses)
    finally:
        frozen.__exit__(None, None, None)
        wb.use_gentx_template_cache = orig_flag
        wb.differential_capture = orig_capture

    def diff_pair(tag, ba_a, cap_a, ba_b, cap_b):
        # 1) stratum job bytes
        for k in ('version', 'previous_block', 'timestamp', 'min_share_target', 'share_target', 'coinb1', 'coinb2'):
            if ba_a[k] != ba_b[k]:
                mismatches.append('%s: ba[%r] differs' % (tag, k))
        if ba_a['bits'].bits != ba_b['bits'].bits:
            mismatches.append('%s: ba[bits] differs' % tag)
        if _canon_link(ba_a['merkle_link']) != _canon_link(ba_b['merkle_link']):
            mismatches.append('%s: ba[merkle_link] differs' % tag)
        # 2) full gentx bytes (with and without witness)
        if bitcoin_data.tx_type.pack(cap_a['gentx']) != bitcoin_data.tx_type.pack(cap_b['gentx']):
            mismatches.append('%s: packed gentx (tx_type) differs' % tag)
        if bitcoin_data.tx_id_type.pack(cap_a['gentx']) != bitcoin_data.tx_id_type.pack(cap_b['gentx']):
            mismatches.append('%s: packed stripped gentx (tx_id_type) differs' % tag)
        # 3) tx list + share_info (dict equality is exact: ints/strs/arrays)
        if cap_a['other_transaction_hashes'] != cap_b['other_transaction_hashes']:
            mismatches.append('%s: other_transaction_hashes differ' % tag)
        if cap_a['share_info'] != cap_b['share_info']:
            mismatches.append('%s: share_info differs' % tag)
        if _canon_link(cap_a['merkle_link']) != _canon_link(cap_b['merkle_link']):
            mismatches.append('%s: internal merkle_link differs' % tag)
        # 4) wire-level share bytes via get_share (strongest gate: this is
        #    exactly what v35/v36 peers deserialize and re-derive)
        try:
            s_a = cap_a['get_share'](_fake_header(ba_a, cap_a))
            s_b = cap_b['get_share'](_fake_header(ba_b, cap_b))
            w_a = p2pool_data.share_type.pack(s_a.as_share())
            w_b = p2pool_data.share_type.pack(s_b.as_share())
            if w_a != w_b:
                mismatches.append('%s: wire share bytes differ' % tag)
        except Exception, e:
            # If Share.__init__ enforces PoW on this fork, fall back to the
            # gentx/share_info comparisons above (which already cover all
            # consensus bytes) — but surface it so the steward knows.
            mismatches.append('%s: wire-share comparison did not run: %r' % (tag, e))

    diff_pair('old-vs-new(cold)', ba_old, cap_old, ba_new, cap_new)
    diff_pair('new(cold)-vs-new(warm)', ba_new, cap_new, ba_warm, cap_warm)
    return mismatches


def run_differential_once(wb, users=None, merged_variants=None):
    '''One full users x merged-variants sweep against the CURRENT tx-set.
    Returns a report dict; raises AssertionError on any byte mismatch.'''
    users = users if users is not None else default_users()
    merged_variants = merged_variants if merged_variants is not None else [None, {}]
    failures = []
    checked = 0
    for (user, pubkey_hash, pubkey_type) in users:
        for mv in merged_variants:
            checked += 1
            for m in compare_one(wb, user, pubkey_hash, pubkey_type, mv):
                failures.append('user=%r pkh=%040x type=%d merged=%r: %s' % (
                    user, pubkey_hash, pubkey_type, mv, m))
    if failures:
        raise AssertionError('get_work differential FAILED (%d mismatches):\n%s' % (
            len(failures), '\n'.join(failures)))
    return dict(checked=checked, txs=len(wb.current_work.value['transactions']),
                best_share=wb.node.best_share_var.value)


@defer.inlineCallbacks
def run_across_work_events(wb, users=None, merged_variants=None, events=5):
    '''Covers the tx-set axis: sweep once per work event (GBT refresh or new
    share on the safenet changes the template/tip between iterations).'''
    reports = []
    for i in xrange(events):
        report = run_differential_once(wb, users, merged_variants)
        print '[GETWORK-DIFF] event %d/%d OK: %d combos byte-identical (%d txs, tip=%s)' % (
            i + 1, events, report['checked'], report['txs'],
            '%064x' % report['best_share'] if report['best_share'] is not None else 'None')
        reports.append(report)
        if i + 1 < events:
            yield wb.new_work_event.get_deferred()
    defer.returnValue('PASS: %d work events, %d combos each, all byte-identical' % (
        len(reports), reports[0]['checked'] if reports else 0))


class GetWorkDifferentialTest(unittest.TestCase):
    def test_requires_live_node(self):
        raise unittest.SkipTest(
            'This gate needs a live safenet WorkerBridge. Run '
            'run_across_work_events(wb, ...) from the node console, once with '
            'AutoRatchet at v35 and once at v36.')