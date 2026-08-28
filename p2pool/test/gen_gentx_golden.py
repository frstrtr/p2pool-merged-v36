# -*- coding: utf-8 -*-
'''
OFFLINE golden-vector generator + byte-identical gentx differential for the
Litecoin (LTC) coinbase-generation path.

Unlike p2pool/test/test_gentx_live_differential.py (which needs a live safenet
WorkerBridge) and p2pool/test/test_getwork_differential.py (which also needs a
running node), this driver stands up the MINIMAL inputs in-process -- a net,
an empty tracker, synthetic share_data, and a small synthetic GBT tx-set --
and calls BOTH the OLD monolithic path (Share.generate_transaction) and the
NEW split path (generate_transaction_template + finalize_generate_transaction)
under frozen time/randomness, asserting the two produce BYTE-IDENTICAL
consensus artifacts (gentx, share_info, other_transaction_hashes).

It requires NO scrypt daemon and NO WorkerBridge, so it can run the G1 merge
gate in CI.  It also emits frozen golden-vector JSON fixtures to
p2pool/test/golden/ltc_gentx_*.json -- these are the reference target the
c2pool `ltc_gentx_dump` producer must reproduce (same key shape as the live
harness's cross-impl contract: gentx_hex, gentx_txid_hex, share_info,
other_tx_hashes, new_tx_bytes, template_weight).

The tracker is deliberately EMPTY and previous_share_hash is None: the share
being generated is the first on its chain, so no share history is required to
exercise the full gentx assembly (payouts collapse to the donation output,
which is exactly the boundary case that must still be byte-identical across
implementations).

Run:
    ~/.pyenv/versions/2.7.18/bin/python -m p2pool.test.gen_gentx_golden
'''

from __future__ import division

import json
import os

import random
import time

from p2pool import data as p2pool_data
from p2pool.bitcoin import data as bitcoin_data
from p2pool.bitcoin import script as bitcoin_script
from p2pool.util import forest
from p2pool.util import math as p2pool_math
from p2pool.networks import litecoin as ltc_net


NEW_TX_BUDGET_BYTES = 50 * 1000   # mirrors the live harness's crossing valve

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden')

# A fixed wall-clock epoch so the emitted fixtures are reproducible byte-for-
# byte on any host (the live harness freezes to "now"; a frozen reference file
# must instead pin an absolute instant).
FROZEN_EPOCH = 1700000000.0


class _FrozenDeterminism(object):
    '''Freeze time.time and random.randrange around one OLD/NEW call pair.
    Both calls are fully synchronous, so nothing re-enters while frozen.
    Same nonce/timestamp policy as test_getwork_differential._FrozenDeterminism,
    but pinned to an absolute epoch for reproducible fixtures.'''
    def __enter__(self):
        self._real_time = time.time
        self._real_randrange = random.randrange
        self._real_random = random.random
        time.time = lambda: FROZEN_EPOCH
        random.randrange = lambda *a, **k: 0x5AFE5AFE % (a[0] if a else 2**32)
        # perfect_round (donation %) is stochastic: int(x + random.random()).
        # Pin random.random to 0.0 so the donation field is a deterministic
        # floor and the fixture is reproducible byte-for-byte.
        random.random = lambda *a, **k: 0.0
        return self

    def __exit__(self, *exc):
        time.time = self._real_time
        random.randrange = self._real_randrange
        random.random = self._real_random
        return False


# --------------------------------------------------------------------------
# minimal synthetic inputs
# --------------------------------------------------------------------------
def make_synthetic_tx(seed):
    '''A small, valid, NON-segwit transaction (so is_segwit_tx() is False and
    the segwit tx-set path treats wtxid == txid).'''
    return dict(
        version=1,
        tx_ins=[dict(
            previous_output=dict(hash=(0x1111111111111111111111111111111111111111111111111111111111111111 + seed) % 2**256, index=seed % 4),
            script=('\x51' * (8 + seed)),   # OP_1 padding, distinct length per tx
            sequence=None,
        )],
        tx_outs=[dict(
            value=100000 + 1000 * seed,
            script='\x76\xa9\x14' + (chr(0x20 + seed) * 20) + '\x88\xac',  # P2PKH-shaped
        )],
        lock_time=0,
    )


def build_gbt_txset(n):
    '''Return (transaction_hashes, transactions, transaction_fees) for a
    synthetic GBT view with n transactions, preserving order (GBT order is
    consensus-bearing -- no re-sort).'''
    txs = [make_synthetic_tx(i) for i in xrange(n)]
    hashes = [bitcoin_data.get_txid(tx) for tx in txs]
    fees = [1000 * (i + 1) for i in xrange(n)]
    return hashes, txs, fees


def build_share_data(net, share_type, pubkey_hash, pubkey_type, subsidy,
                     height, donation_percentage=1.0):
    '''Mirror WorkerBridge.get_work()'s share_data_base construction for a
    V36 (>=36) share with an empty tip (previous_share_hash=None).'''
    coinbase = (
        bitcoin_script.create_push_script([height]) +
        getattr(net, 'COINBASEEXT', b'')
    )[:100]
    share_data = dict(
        previous_share_hash=None,
        coinbase=coinbase,
        nonce=random.randrange(2**32),   # frozen -> deterministic
        subsidy=subsidy,
        donation=p2pool_math.perfect_round(65535 * donation_percentage / 100),
        stale_info=None,
        desired_version=share_type.VERSION,
    )
    # V36 stores pubkey_hash (IntType 160) + pubkey_type (1 byte)
    share_data['pubkey_hash'] = pubkey_hash
    share_data['pubkey_type'] = pubkey_type
    return share_data


# --------------------------------------------------------------------------
# the two paths
# --------------------------------------------------------------------------
def run_old_path(share_type, tracker, net, share_data, block_target,
                 desired_target, tx_hashes, txs, fees, subsidy, v36_active):
    tx_map = dict(zip(tx_hashes, txs))
    share_info, gentx, other_transaction_hashes, get_share = share_type.generate_transaction(
        tracker=tracker,
        share_data=dict(share_data),   # defensive copy; the path mutates locally
        block_target=block_target,
        desired_timestamp=int(time.time() + 0.5),
        desired_target=desired_target,
        ref_merkle_link=dict(branch=[], index=0),
        desired_other_transaction_hashes_and_fees=zip(tx_hashes, fees),
        net=net,
        known_txs=tx_map,
        base_subsidy=subsidy,
        v36_active=v36_active,
        merged_addresses=None,
        message_data=None,
        merged_coinbase_info=None,
    )
    merkle_link = (bitcoin_data.calculate_merkle_link([None] + other_transaction_hashes, 0)
                   if share_info.get('segwit_data', None) is None
                   else share_info['segwit_data']['txid_merkle_link'])
    return dict(share_info=share_info, gentx=gentx,
                other_transaction_hashes=other_transaction_hashes,
                get_share=get_share, merkle_link=merkle_link, tx_map=tx_map)


def run_new_path(share_type, tracker, net, share_data, block_target,
                 desired_target, tx_hashes, txs, fees, subsidy, v36_active):
    tx_map = dict(zip(tx_hashes, txs))
    template = share_type.generate_transaction_template(
        tracker=tracker,
        previous_share_hash=share_data['previous_share_hash'],
        block_target=block_target,
        desired_other_transaction_hashes_and_fees=zip(tx_hashes, fees),
        net=net,
        known_txs=tx_map,
        subsidy=subsidy,
        base_subsidy=subsidy,
        v36_active=v36_active,
    )
    template['tx_map'] = tx_map
    template['other_transactions'] = [tx_map[h] for h in template['other_transaction_hashes']]
    share_info, gentx, other_transaction_hashes, get_share = share_type.finalize_generate_transaction(
        template=template,
        share_data=dict(share_data),
        desired_timestamp=int(time.time() + 0.5),
        desired_target=desired_target,
        ref_merkle_link=dict(branch=[], index=0),
        net=net,
        merged_addresses=None,
        message_data=None,
        merged_coinbase_info=None,
    )
    merkle_link = (template['merkle_link_nonsegwit']
                   if share_info.get('segwit_data', None) is None
                   else share_info['segwit_data']['txid_merkle_link'])
    return dict(share_info=share_info, gentx=gentx,
                other_transaction_hashes=other_transaction_hashes,
                get_share=get_share, merkle_link=merkle_link, tx_map=tx_map)


# --------------------------------------------------------------------------
# comparison + serialization
# --------------------------------------------------------------------------
def _canon_link(link):
    return (tuple(link['branch']), link['index'])


def compare_paths(old, new):
    '''Return a list of mismatch descriptions (empty == byte-identical).'''
    m = []
    if bitcoin_data.tx_type.pack(old['gentx']) != bitcoin_data.tx_type.pack(new['gentx']):
        m.append('packed gentx (tx_type) differs')
    if bitcoin_data.tx_id_type.pack(old['gentx']) != bitcoin_data.tx_id_type.pack(new['gentx']):
        m.append('packed stripped gentx (tx_id_type) differs')
    if old['other_transaction_hashes'] != new['other_transaction_hashes']:
        m.append('other_transaction_hashes differ or were re-sorted')
    if old['share_info'] != new['share_info']:
        m.append('share_info differs')
    if _canon_link(old['merkle_link']) != _canon_link(new['merkle_link']):
        m.append('merkle_link differs')
    return m


def jsonify(obj):
    '''Recursively convert a share_info / value into a canonical JSON-safe
    form: bytes -> hex string, arrays/tuples -> lists, FloatingInteger -> its
    compact int, ints preserved (json handles arbitrary precision).'''
    import array
    if isinstance(obj, dict):
        return dict((k, jsonify(v)) for k, v in obj.iteritems())
    if isinstance(obj, (list, tuple)):
        return [jsonify(v) for v in obj]
    if isinstance(obj, array.array):
        return [jsonify(v) for v in obj]
    if isinstance(obj, bitcoin_data.FloatingInteger):
        return {'__floatinginteger_bits__': obj.bits}
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, long)):
        return obj
    if obj is None:
        return None
    if isinstance(obj, str):
        # raw bytes (py2 str) -> hex
        return {'__hex__': obj.encode('hex')}
    if isinstance(obj, unicode):
        return obj
    raise TypeError('cannot jsonify %r (%s)' % (obj, type(obj)))


def build_artifact(share_type, net, res):
    '''Same key shape as the live harness's c2pool contract, plus the packed
    share_info bytes (the unambiguous consensus anchor for c2pool).'''
    share_info_type = share_type.get_dynamic_types(net)['share_info_type']
    other = res['other_transaction_hashes']
    tx_map = res['tx_map']
    new_tx_bytes = sum(len(bitcoin_data.tx_type.pack(tx_map[h])) for h in other if h in tx_map)
    template_weight = sum(len(bitcoin_data.tx_type.pack(t)) for t in tx_map.itervalues())
    return dict(
        gentx_hex=bitcoin_data.tx_type.pack(res['gentx']).encode('hex'),
        gentx_txid_hex=bitcoin_data.tx_id_type.pack(res['gentx']).encode('hex'),
        share_info=jsonify(res['share_info']),
        share_info_packed_hex=share_info_type.pack(res['share_info']).encode('hex'),
        other_tx_hashes=['%064x' % h for h in other],
        new_tx_bytes=new_tx_bytes,
        template_weight=template_weight,
    )


# --------------------------------------------------------------------------
# scenarios
# --------------------------------------------------------------------------
def default_users():
    base = 0x0123456789abcdef0123456789abcdef01234567
    return [
        ('difftest_p2pkh', base % 2**160, 0),           # P2PKH
        ('difftest_p2wpkh', (base * 2) % 2**160, 1),    # P2WPKH/bech32
    ]


def default_scenarios():
    # (name, n_txs)
    return [('empty', 0), ('twotx', 2), ('threetx', 3)]


def run(emit=True, verbose=True):
    net = ltc_net
    share_type = p2pool_data.MergedMiningShare   # V36 LTC share class
    v36_active = share_type.VERSION >= 36
    block_target = net.MAX_TARGET                # easiest allowed
    desired_target = 2**256 - 1                  # get_work()'s None default
    subsidy = 1250000000                         # ~12.5 LTC in litoshi
    height = 2000000

    if emit and not os.path.isdir(GOLDEN_DIR):
        os.makedirs(GOLDEN_DIR)

    all_mismatches = []
    emitted = []
    results = []   # (basename, fixture) for every scenario/user
    for scen_name, n in default_scenarios():
        tx_hashes, txs, fees = build_gbt_txset(n)
        for user, pubkey_hash, pubkey_type in default_users():
            tracker = forest.Tracker()  # deliberately EMPTY (previous_share_hash=None)
            with _FrozenDeterminism():
                # build share_data INSIDE the frozen block so the coinbase
                # nonce (random.randrange) is deterministic across runs -- the
                # fixture must be reproducible byte-for-byte on any host.
                share_data = build_share_data(net, share_type, pubkey_hash,
                                              pubkey_type, subsidy, height)
                old = run_old_path(share_type, tracker, net, share_data,
                                   block_target, desired_target,
                                   tx_hashes, txs, fees, subsidy, v36_active)
                new = run_new_path(share_type, tracker, net, share_data,
                                   block_target, desired_target,
                                   tx_hashes, txs, fees, subsidy, v36_active)

            mismatches = compare_paths(old, new)
            tag = '%s/%s' % (scen_name, user)
            if mismatches:
                for msg in mismatches:
                    all_mismatches.append('%s: %s' % (tag, msg))

            art = build_artifact(share_type, net, old)
            # cross-check: the NEW path serializes to the identical artifact
            art_new = build_artifact(share_type, net, new)
            if art != art_new:
                all_mismatches.append('%s: serialized artifact OLD != NEW' % tag)

            if verbose:
                print '[GOLDEN] %-22s txs=%d gentx=%s... txid_len=%d new_tx_bytes=%d OLD==NEW:%s' % (
                    tag, n, art['gentx_hex'][:32], len(art['gentx_txid_hex']) // 2,
                    art['new_tx_bytes'], 'yes' if not mismatches else 'NO')

            if art['new_tx_bytes'] > NEW_TX_BUDGET_BYTES:
                all_mismatches.append('%s: new_tx_bytes %d exceed 50kB budget'
                                      % (tag, art['new_tx_bytes']))

            fixture = dict(
                _comment=('OFFLINE golden vector for the LTC gentx path. '
                          'Frozen reference target for c2pool ltc_gentx_dump. '
                          'Regenerate with p2pool.test.gen_gentx_golden.'),
                scenario=scen_name,
                user=user,
                net='litecoin',
                share_version=share_type.VERSION,
                v36_active=v36_active,
                pubkey_hash='%040x' % pubkey_hash,
                pubkey_type=pubkey_type,
                subsidy=subsidy,
                height=height,
                frozen_epoch=FROZEN_EPOCH,
                block_target_hex='%064x' % block_target,
                desired_target_hex='%064x' % desired_target,
                gbt_transactions=[bitcoin_data.tx_type.pack(t).encode('hex') for t in txs],
                gbt_transaction_hashes=['%064x' % h for h in tx_hashes],
                gbt_transaction_fees=fees,
                artifact=art,
            )
            basename = 'ltc_gentx_%s_%s.json' % (scen_name, user)
            results.append((basename, fixture))
            if emit:
                path = os.path.join(GOLDEN_DIR, basename)
                with open(path, 'wb') as f:
                    json.dump(fixture, f, indent=2, sort_keys=True)
                    f.write('\n')
                emitted.append(path)

    if verbose and emitted:
        print '[GOLDEN] emitted %d fixtures to %s' % (len(emitted), GOLDEN_DIR)

    if all_mismatches:
        raise AssertionError('gentx OFFLINE differential FAILED (%d):\n%s'
                             % (len(all_mismatches), '\n'.join(all_mismatches)))
    return results


if __name__ == '__main__':
    results = run(emit=True, verbose=True)
    print 'OK: OLD == NEW byte-identical across all scenarios/users; %d fixtures written.' % len(results)
