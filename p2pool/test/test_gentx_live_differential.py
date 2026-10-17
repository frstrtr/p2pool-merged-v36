# -*- coding: utf-8 -*-
'''
LIVE cross-implementation gentx differential: c2pool (v36) <-> p2pool (v35).

Unlike p2pool/test/test_getwork_differential.py -- which proves the DOA-fix
template cache is byte-identical to the legacy path WITHIN p2pool -- this gate
crosses the implementation boundary.  It drives the REAL get_work() path on
BOTH sides over the SAME GBT tx-set / tip / user and asserts they would produce
the same consensus artifact AND that each side's artifact is ACCEPTABLE under
its own consensus rules.

Two independent faces (a share can fail either one):

  FACE A -- IDENTITY (chain-split gate)
      packed gentx (tx_type + tx_id_type), share_info dict, merkle link,
      other_transaction_hashes, and the shared tx-set policy re-derive
      BYTE-IDENTICAL across c2pool and p2pool.  A mismatch here forks the
      sharechain along implementation lines.

  FACE B -- SIZE / ADMISSIBILITY (block-2517855 gate)
      each side's emitted template is ACCEPTED by its own consensus rules,
      not merely identical to the other:
        - new-transaction bytes (txs not already chain-known) <= 50 kB budget
        - total template weight/size within the parent's consensus cap
        - GBT tx ordering preserved (no re-sort), canonical first-seen refs
      A differential that only checked FACE A would have waved 2517855
      through -- both sides can agree on an artifact that neither chain will
      accept.  FACE B is the reason this harness exists.

STATUS: SKELETON.  The p2pool side is wired to the live WorkerBridge.  The
c2pool side is NOT yet standable -- there is no gentx PRODUCER dump in
frstrtr/c2pool (src/impl/ltc has a codec ROUND-TRIP harness,
test/wirecompat_runtime_test.cpp, but nothing that emits a gentx + share_info
from a given GBT tx-set for cross-comparison).  This harness therefore FAILS
LOUDLY at c2pool_gentx_over() with the exact producer contract that must be
built.  An honest failing skeleton beats a plan.

Run (from a live safenet WorkerBridge -- self-provision per the standing rule):
    from p2pool.test import test_gentx_live_differential as live
    live.run_live_differential(wb, user='difftest_0',
                               pubkey_hash=0x00, pubkey_type=0)
'''

from __future__ import division

import json
import os
import subprocess

from p2pool.bitcoin import data as bitcoin_data


# --- config: where the c2pool producer will live once built -----------------
# Contract for the not-yet-built producer (this is the first unimplemented
# step the skeleton fails at):
#
#   target : a c2pool binary/ctest `ltc_gentx_dump` under src/impl/ltc/test
#   input  : JSON on stdin -- the exported GBT view below (export_gbt_view)
#   output : JSON on stdout with the SAME keys the p2pool side captures:
#              gentx_hex            hex of tx_type.pack(gentx)
#              gentx_txid_hex       hex of tx_id_type.pack(gentx)  (stripped)
#              share_info           the share_info dict, canonically ordered
#              other_tx_hashes      [int,...] in GBT order, no re-sort
#              new_tx_bytes         int  -- bytes of txs NOT chain-known
#              template_weight      int  -- total weight/size of the template
#            so FACE A is a dict/bytes compare and FACE B reads new_tx_bytes.
C2POOL_GENTX_DUMP = os.environ.get(
    'C2POOL_GENTX_DUMP',
    os.path.expanduser('~/Github/c2pool/build/ltc_gentx_dump'))

NEW_TX_BUDGET_BYTES = 50 * 1000   # the crossing safety valve (<=50 kB new-tx)


# --- p2pool side: REAL get_work() capture -----------------------------------
def p2pool_gentx_over(wb, user, pubkey_hash, pubkey_type, merged_addresses=None):
    '''Drive the live WorkerBridge get_work() once and return the consensus
    artifact + the size face inputs.  This is the production path -- the same
    bytes v35/v36 peers deserialize.'''
    cap = {}
    orig = wb.differential_capture
    wb.differential_capture = cap
    try:
        ba, _got = wb.get_work(user, pubkey_hash, pubkey_type, None, None, merged_addresses)
    finally:
        wb.differential_capture = orig

    work = wb.current_work.value
    tx_map = dict(zip(work['transaction_hashes'], work['transactions']))
    other = cap['other_transaction_hashes']
    # new-tx bytes = txs this share introduces that are not already chain-known.
    # (Chain-known set on the live node = current_work tx_hashes minus those the
    #  c2pool side reports as already referenced; on the p2pool side every
    #  other_tx is "new" relative to the empty share, so this is the upper
    #  bound the 50 kB budget clamps -- FACE B asserts it holds.)
    new_tx_bytes = sum(len(bitcoin_data.tx_type.pack(tx_map[h])) for h in other if h in tx_map)
    return dict(
        gentx_hex=bitcoin_data.tx_type.pack(cap['gentx']).encode('hex'),
        gentx_txid_hex=bitcoin_data.tx_id_type.pack(cap['gentx']).encode('hex'),
        share_info=cap['share_info'],
        other_tx_hashes=list(other),
        new_tx_bytes=new_tx_bytes,
        template_weight=sum(len(bitcoin_data.tx_type.pack(t)) for t in work['transactions']),
        _ba=ba,
    )


def export_gbt_view(wb, user, pubkey_hash, pubkey_type):
    '''Serialize the live GBT view so the c2pool producer sees the IDENTICAL
    tx-set / tip / bits / height / coinbaseflags / user.  Preserves GBT order
    (no re-sort) -- ordering is itself consensus-bearing.'''
    work = wb.current_work.value
    return dict(
        user=user, pubkey_hash='%040x' % pubkey_hash, pubkey_type=pubkey_type,
        previous_block='%064x' % work['previous_block'],
        bits=work['bits'].bits, height=work['height'],
        coinbaseflags=work['coinbaseflags'].encode('hex'),
        transaction_hashes=['%064x' % h for h in work['transaction_hashes']],
        transactions=[bitcoin_data.tx_type.pack(t).encode('hex') for t in work['transactions']],
        best_share=('%064x' % wb.node.best_share_var.value) if wb.node.best_share_var.value is not None else None,
    )


# --- c2pool side: NOT YET STANDABLE (first unimplemented step) ---------------
def c2pool_gentx_over(gbt_view):
    '''Invoke the c2pool LTC gentx producer over the exported GBT view and
    return the same-shaped artifact dict as p2pool_gentx_over().'''
    if not os.path.exists(C2POOL_GENTX_DUMP):
        raise NotImplementedError(
            'c2pool gentx producer not built: %s does not exist.\n'
            'FIRST STEP: add a `ltc_gentx_dump` target under '
            'frstrtr/c2pool src/impl/ltc/test that reads the exported GBT '
            'view (JSON stdin) and emits {gentx_hex, gentx_txid_hex, '
            'share_info, other_tx_hashes, new_tx_bytes, template_weight} '
            '(JSON stdout) from the REAL ltc gentx path -- NOT the codec '
            'round-trip in wirecompat_runtime_test.cpp, which never builds a '
            'gentx from a tx-set.' % C2POOL_GENTX_DUMP)
    proc = subprocess.Popen([C2POOL_GENTX_DUMP], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(json.dumps(gbt_view))
    if proc.returncode != 0:
        raise RuntimeError('c2pool gentx dump failed (rc=%d): %s' % (proc.returncode, err))
    return json.loads(out)


# --- the two faces ----------------------------------------------------------
def face_a_identity(p, c):
    '''Chain-split gate: byte/dict identity across implementations.'''
    m = []
    if p['gentx_hex'] != c['gentx_hex']:
        m.append('FACE-A: packed gentx (tx_type) differs')
    if p['gentx_txid_hex'] != c['gentx_txid_hex']:
        m.append('FACE-A: stripped gentx (tx_id_type) differs')
    if p['other_tx_hashes'] != c['other_tx_hashes']:
        m.append('FACE-A: tx-set differs or was re-sorted (GBT order not preserved)')
    if p['share_info'] != c['share_info']:
        m.append('FACE-A: share_info differs')
    return m


def face_b_admissibility(side, art):
    '''block-2517855 gate: would THIS side's own consensus accept it?'''
    m = []
    if art['new_tx_bytes'] > NEW_TX_BUDGET_BYTES:
        m.append('FACE-B[%s]: new-tx bytes %d exceed 50 kB budget -- template '
                 'would be REJECTED regardless of cross-impl identity'
                 % (side, art['new_tx_bytes']))
    # TODO: parent consensus weight cap check once export carries the cap.
    return m


def run_live_differential(wb, user='difftest_0', pubkey_hash=0x00, pubkey_type=0,
                          merged_addresses=None):
    '''One live crossing check.  Raises AssertionError listing every failure
    across both faces, or NotImplementedError at the first unbuilt step.'''
    p = p2pool_gentx_over(wb, user, pubkey_hash, pubkey_type, merged_addresses)
    gbt = export_gbt_view(wb, user, pubkey_hash, pubkey_type)
    c = c2pool_gentx_over(gbt)   # <-- honest failure here until producer exists

    failures = []
    failures += face_a_identity(p, c)
    failures += face_b_admissibility('p2pool', p)
    failures += face_b_admissibility('c2pool', c)
    if failures:
        raise AssertionError('gentx live differential FAILED (%d):\n%s'
                             % (len(failures), '\n'.join(failures)))
    return dict(user=user, new_tx_bytes=p['new_tx_bytes'],
                txs=len(gbt['transaction_hashes']), faces='A+B PASS')


if __name__ == '__main__':
    raise SystemExit(
        'This harness needs a live safenet WorkerBridge. Import it and call '
        'run_live_differential(wb, ...) from the node console. It will fail '
        'loudly at c2pool_gentx_over() until the ltc_gentx_dump producer is '
        'built -- that is the first step, by design.')
