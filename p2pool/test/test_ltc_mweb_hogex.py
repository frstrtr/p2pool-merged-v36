# -*- coding: utf-8 -*-
'''
LTC MWEB / HogEx block-body KATs.

LTC mainnet blocks 3199722 (2026-10-22) and 521906df... (2026-08-27) were
lost with litecoind 'mweb-missing'. getblocktemplate returns the HogEx as the
LAST transaction, and litecoind reads the MWEB extension block only when the
block's last tx is a HogEx. The tx-selection loops in p2pool/data.py truncated
the GBT list (G2 fill-budget break; also the stripped-size break), dropping
the HogEx first, while both submit paths still sent the block without it.

These tests pin:
  * selection: on IMMUTABLE_BLOCKS networks the GBT list is taken verbatim,
    in order, HogEx last -- template path and monolith path, with the G2
    budget at the legacy 50 kB and with a witness-heavy template that trips
    the stripped-size break;
  * the RPC submit bytes (exactly what helper.submit_block_rpc sends) parse
    as a block with N+1 txs, a HogEx last, then '01' + the MWEB block;
  * the P2P block payload (broadcaster -> peer) equals the RPC bytes;
  * the extension submitted is the JOB's, not the current GBT's;
  * get_work() refuses a template whose HogEx/MWEB pairing is broken;
  * a non-immutable network keeps the old truncation behaviour.

Offline: no daemon, no reactor. The scrypt PoW is replaced by sha256d on a
network overlay (never on the real module) so no C extension is needed.

Run from the repo root:  python2 -m unittest p2pool.test.test_ltc_mweb_hogex
'''

from __future__ import division

import struct
import unittest

from twisted.internet import defer

import p2pool
from p2pool import data as p2pool_data
from p2pool import work as p2pool_work
from p2pool.bitcoin import broadcaster as bitcoin_broadcaster
from p2pool.bitcoin import data as bitcoin_data
from p2pool.bitcoin import helper
from p2pool.bitcoin import p2p as bitcoin_p2p
from p2pool.networks import litecoin as ltc_net
from p2pool.test import gen_gentx_golden as gen
from p2pool.util import forest, jsonrpc, pack, variable


SHARE_TYPE = p2pool_data.MergedMiningShare        # V36 LTC share class
LEGACY_BUDGET = 50000                             # fillbudget floor == v35 cap
SUBSIDY = 625000000
HEIGHT = 3199722
BITS = bitcoin_data.FloatingInteger(bits=0x1a01b8f4)
PUBKEY_HASH = 0x0123456789abcdef0123456789abcdef01234567

# A stand-in MWEB extension block (opaque to p2pool; only its bytes matter).
MWEB_JOB = ('4d5745422d4a4f42' * 64) + 'aa'       # "MWEB-JOB"...
MWEB_CUR = ('4d5745422d435552' * 64) + 'bb'       # "MWEB-CUR"...


# --------------------------------------------------------------------------
# synthetic GBT transactions (raw GBT dicts, exactly the rawtx shape)
# --------------------------------------------------------------------------
def _varint(n):
    return pack.VarIntType().pack(n)


def _varstr(s):
    return _varint(len(s)) + s


def _gbt_entry(stripped, full, fee):
    txid = bitcoin_data.hash256(stripped)
    wtxid = bitcoin_data.hash256(full)
    return dict(data=full.encode('hex'), txid='%064x' % txid,
                hash='%064x' % wtxid, fee=fee,
                weight=3 * len(stripped) + len(full))


def _txin(seed):
    prev = struct.pack('<Q', 0x5eed0000 + seed) * 4
    return prev + struct.pack('<I', seed % 3)


def plain_tx(seed, script_len=4000):
    '''Non-segwit tx, ~4.1 kB (big scriptSig).'''
    vin = _varint(1) + _txin(seed) + _varstr('\x51' * script_len) + struct.pack('<I', 0xffffffff)
    vout = _varint(1) + struct.pack('<q', 100000 + seed) + _varstr('\x00\x14' + struct.pack('<I', seed) * 5)
    raw = struct.pack('<i', 2) + vin + vout + struct.pack('<I', 0)
    return _gbt_entry(raw, raw, 1000 + seed)


def witness_tx(seed, witness_len=3500):
    '''Segwit tx with a big witness (inscription-like): ~82 B base, ~3.6 kB.'''
    vin = _varint(1) + _txin(seed) + _varstr('') + struct.pack('<I', 0xfffffffd)
    vout = _varint(1) + struct.pack('<q', 50000 + seed) + _varstr('\x00\x14' + struct.pack('<I', seed) * 5)
    lock = struct.pack('<I', 0)
    stripped = struct.pack('<i', 2) + vin + vout + lock
    wit = _varint(1) + _varstr(chr(seed % 256) * witness_len)
    full = struct.pack('<i', 2) + '\x00\x01' + vin + vout + wit + lock
    return _gbt_entry(stripped, full, 2000 + seed)


def hogex_tx(tag='\x01'):
    '''Litecoin HogEx: marker 0x00, flag 0x08, null mweb_tx (0x00) before
    the locktime. Spends the previous HogEx, pays the HogAddr (OP_8 <32>).'''
    vin = _varint(1) + (tag * 32) + struct.pack('<I', 0) + _varstr('') + struct.pack('<I', 0xffffffff)
    vout = _varint(1) + struct.pack('<q', 123456789012) + _varstr('\x58\x20' + tag * 32)
    lock = struct.pack('<I', 0)
    stripped = struct.pack('<i', 2) + vin + vout + lock
    full = struct.pack('<i', 2) + '\x00\x08' + vin + vout + '\x00' + lock
    return _gbt_entry(stripped, full, 0)


def build_gbt(txs, mweb_hex):
    '''helper.getwork()-shaped work dict over raw GBT tx dicts.'''
    return dict(
        version=0x20000000,
        previous_block=0x1111111111111111111111111111111111111111111111111111111111111111,
        transactions=txs,
        transaction_hashes=[bitcoin_data.hex_to_hash(x['hash']) for x in txs],
        transaction_fees=[x['fee'] for x in txs],
        subsidy=SUBSIDY,
        time=1792700000,
        bits=BITS,
        coinbaseflags='',
        height=HEIGHT,
        rules=['csv', '!segwit', 'taproot', '!mweb'],
        last_update=0,
        use_getblocktemplate=True,
        latency=0,
        mweb=('01' + mweb_hex) if mweb_hex else '',
    )


def budget_gbt(mweb_hex=MWEB_JOB, hogex_tag='\x01'):
    '''>= 400 kB of txs, HogEx last.'''
    txs = [plain_tx(i) for i in xrange(100)] + [hogex_tx(hogex_tag)]
    assert sum(len(t['data']) // 2 for t in txs) >= 400000
    return build_gbt(txs, mweb_hex)


def witness_heavy_gbt(mweb_hex=MWEB_JOB):
    '''~1.08 MB / 1.15 MWU: a valid LTC block that still trips the
    stripped-size break (1,000,000 - 80 - gentx_size 50,000 - 500). For GBT
    dicts get_stripped_size() evaluates (4*size - weight)//3, i.e. the
    witness+marker bytes (3,506 per tx here), so ~271 such txs cross it.'''
    txs = [witness_tx(i) for i in xrange(300)] + [hogex_tx()]
    assert sum(t['weight'] for t in txs) < ltc_net.BLOCK_MAX_WEIGHT // 2
    return build_gbt(txs, mweb_hex)


# --------------------------------------------------------------------------
# network overlay (the litecoin module itself is never mutated)
# --------------------------------------------------------------------------
class NetOverlay(object):
    def __init__(self, base, **overrides):
        self.__dict__['_base'] = base
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._base, name)


def _sha256d_pow(data):
    return bitcoin_data.hash256(data)


def offline_ltc_net(**overrides):
    parent = NetOverlay(ltc_net.PARENT, POW_FUNC=_sha256d_pow, BLOCKHASH_FUNC=_sha256d_pow)
    return NetOverlay(ltc_net, PARENT=parent, **overrides)


# --------------------------------------------------------------------------
# the two selection paths
# --------------------------------------------------------------------------
def select_template(net, work, new_tx_budget):
    tx_map = dict(zip(work['transaction_hashes'], work['transactions']))
    template = SHARE_TYPE.generate_transaction_template(
        tracker=forest.Tracker(), previous_share_hash=None,
        block_target=work['bits'].target,
        desired_other_transaction_hashes_and_fees=zip(work['transaction_hashes'], work['transaction_fees']),
        net=net, known_txs=tx_map, subsidy=work['subsidy'],
        base_subsidy=work['subsidy'], v36_active=True,
        new_tx_budget=new_tx_budget)
    template['tx_map'] = tx_map
    template['other_transactions'] = [tx_map[h] for h in template['other_transaction_hashes']]
    return template


def select_monolith(net, work, new_tx_budget):
    tx_map = dict(zip(work['transaction_hashes'], work['transactions']))
    with gen._FrozenDeterminism():
        share_data = gen.build_share_data(net, SHARE_TYPE, PUBKEY_HASH, 0, work['subsidy'], HEIGHT)
        share_info, gentx, other_transaction_hashes, get_share = SHARE_TYPE.generate_transaction(
            tracker=forest.Tracker(), share_data=share_data,
            block_target=work['bits'].target, desired_timestamp=work['time'],
            desired_target=2**256 - 1, ref_merkle_link=dict(branch=[], index=0),
            desired_other_transaction_hashes_and_fees=zip(work['transaction_hashes'], work['transaction_fees']),
            net=net, known_txs=tx_map, base_subsidy=work['subsidy'],
            v36_active=True, new_tx_budget=new_tx_budget)
    return other_transaction_hashes


def finalize_gentx_hex(net, template):
    with gen._FrozenDeterminism():
        share_data = gen.build_share_data(net, SHARE_TYPE, PUBKEY_HASH, 0, SUBSIDY, HEIGHT)
        share_info, gentx, other_transaction_hashes, get_share = SHARE_TYPE.finalize_generate_transaction(
            template=template, share_data=share_data, desired_timestamp=1792700000,
            desired_target=2**256 - 1, ref_merkle_link=dict(branch=[], index=0),
            net=net, merged_addresses=None, message_data=None, merged_coinbase_info=None)
    return bitcoin_data.tx_type.pack(gentx).encode('hex')


def make_header(work):
    return dict(version=work['version'], previous_block=work['previous_block'],
                merkle_root=0x22, timestamp=work['time'], bits=work['bits'], nonce=7)


def build_block(work, template, net):
    '''The block dict exactly as work.py got_response() builds it.'''
    return dict(header=make_header(work),
                txs=[finalize_gentx_hex(net, template)] + [tx['data'] for tx in template['other_transactions']],
                mweb=template.get('mweb', work.get('mweb', '')))


# --------------------------------------------------------------------------
# minimal Litecoin block parser (independent of p2pool's own types)
# --------------------------------------------------------------------------
def _read_varint(b, i):
    n = ord(b[i])
    if n < 0xfd:
        return n, i + 1
    if n == 0xfd:
        return struct.unpack('<H', b[i + 1:i + 3])[0], i + 3
    if n == 0xfe:
        return struct.unpack('<I', b[i + 1:i + 5])[0], i + 5
    return struct.unpack('<Q', b[i + 1:i + 9])[0], i + 9


def _parse_tx(b, i):
    i += 4                                        # version
    flag = 0
    if b[i] == '\x00':                            # extended serialization
        flag = ord(b[i + 1])
        i += 2
    nin, i = _read_varint(b, i)
    for _ in xrange(nin):
        i += 36
        n, i = _read_varint(b, i)
        i += n + 4
    nout, i = _read_varint(b, i)
    for _ in xrange(nout):
        i += 8
        n, i = _read_varint(b, i)
        i += n
    if flag & 0x01:
        for _ in xrange(nin):
            items, i = _read_varint(b, i)
            for _ in xrange(items):
                n, i = _read_varint(b, i)
                i += n
    if flag & 0x08:
        # OptionalPtr<mw::Transaction>: 0x00 = null, i.e. a HogEx
        if b[i] != '\x00':
            raise ValueError('non-null mweb_tx in canonical vtx')
        i += 1
    i += 4                                        # locktime
    return i, flag


def parse_block(raw):
    count, i = _read_varint(raw, 80)
    flags = []
    for _ in xrange(count):
        i, flag = _parse_tx(raw, i)
        flags.append(flag)
    return dict(tx_count=count, flags=flags, rest=raw[i:])


# --------------------------------------------------------------------------
# submit capture: helper.submit_block_rpc against a fake coind
# --------------------------------------------------------------------------
class FakeCoind(object):
    def __init__(self):
        self.submitted = []

    def rpc_submitblock(self, block_hex):
        self.submitted.append(block_hex)
        return defer.succeed(None)


def rpc_submit_bytes(block, current_work, net):
    coind = FakeCoind()
    d = helper.submit_block_rpc(block, True, coind, variable.Variable(current_work), net)
    errors = []
    d.addErrback(errors.append)
    if errors:
        errors[0].raiseException()
    assert len(coind.submitted) == 1
    return coind.submitted[0].decode('hex')


class FakeTransport(object):
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)


class FakeFactory(object):
    def __init__(self, conn):
        self.conn = variable.Variable(conn)


def p2p_frame_via_broadcaster(block):
    '''What NetworkBroadcaster sends one peer for `block`, as framed bytes.'''
    proto = bitcoin_p2p.Protocol(ltc_net.PARENT)
    proto.transport = FakeTransport()
    bc = bitcoin_broadcaster.NetworkBroadcaster.__new__(bitcoin_broadcaster.NetworkBroadcaster)
    bc.chain_name = 'litecoin'
    d = bc._send_block_to_peer(('127.0.0.1', 9333), dict(factory=FakeFactory(proto)), block)
    sent = []
    d.addCallback(sent.append)
    assert sent == [True], sent
    return ''.join(proto.transport.writes)


def unframe(frame):
    prefix, command, length, checksum = frame[:4], frame[4:16].rstrip('\0'), struct.unpack('<I', frame[16:20])[0], frame[20:24]
    payload = frame[24:]
    assert prefix == ltc_net.PARENT.P2P_PREFIX
    assert length == len(payload)
    assert checksum == bitcoin_data.hash256d(payload)[:4]
    return command, payload


# --------------------------------------------------------------------------
# tests: selection
# --------------------------------------------------------------------------
class ImmutableSelectionKAT(unittest.TestCase):

    def assert_verbatim(self, selected, work):
        hogex_hash = bitcoin_data.hex_to_hash(work['transactions'][-1]['hash'])
        self.assertEqual(len(selected), len(work['transaction_hashes']),
                         'selected %d of %d GBT txs' % (len(selected), len(work['transaction_hashes'])))
        self.assertEqual(list(selected), list(work['transaction_hashes']))
        self.assertEqual(selected[-1], hogex_hash)

    # G2 budget at the legacy floor, >= 400 kB GBT: template (cached) path.
    def test_budget_template_path_takes_gbt_verbatim(self):
        work = budget_gbt()
        t = select_template(ltc_net, work, LEGACY_BUDGET)
        self.assert_verbatim(t['other_transaction_hashes'], work)
        last = t['other_transactions'][-1]['data']
        self.assertEqual(last[8:10], '00')                 # marker
        self.assertTrue(int(last[10:12], 16) & 0x08)       # MWEB flag: HogEx

    # Same, monolith generate_transaction() path (legacy get_work path).
    def test_budget_monolith_path_takes_gbt_verbatim(self):
        work = budget_gbt()
        self.assert_verbatim(select_monolith(ltc_net, work, LEGACY_BUDGET), work)

    # G2 disabled (the one-line mitigation): a witness-heavy template still
    # tripped the ungated stripped-size break and dropped the HogEx.
    def test_stripped_size_break_does_not_drop_hogex(self):
        work = witness_heavy_gbt()
        self.assert_verbatim(select_template(ltc_net, work, None)['other_transaction_hashes'], work)
        self.assert_verbatim(select_monolith(ltc_net, work, None), work)


class NonImmutableNetKeepsTruncation(unittest.TestCase):
    '''Negative: networks without IMMUTABLE_BLOCKS keep the old breaks.'''

    def test_budget_still_truncates(self):
        net = NetOverlay(ltc_net, IMMUTABLE_BLOCKS=False)
        work = budget_gbt()
        # 12 x 4,084 B = 49,008 <= 50,000 < 13 x 4,084: the legacy cap.
        expected = work['transaction_hashes'][:12]
        t = select_template(net, work, LEGACY_BUDGET)
        self.assertEqual(t['other_transaction_hashes'], expected)
        self.assertEqual(select_monolith(net, work, LEGACY_BUDGET), expected)

    def test_stripped_size_break_still_applies(self):
        net = NetOverlay(ltc_net, IMMUTABLE_BLOCKS=False)
        work = witness_heavy_gbt()
        selected = select_template(net, work, None)['other_transaction_hashes']
        self.assertTrue(len(selected) < len(work['transaction_hashes']) - 1)
        self.assertEqual(selected, work['transaction_hashes'][:len(selected)])

    def test_guard_ignores_non_immutable_non_mweb_nets(self):
        net = NetOverlay(ltc_net, IMMUTABLE_BLOCKS=False, SOFTFORKS_REQUIRED=set(['segwit']))
        work = budget_gbt(mweb_hex=None)
        work['rules'] = ['csv', '!segwit']
        work['transactions'] = work['transactions'][:-1]
        work['transaction_hashes'] = work['transaction_hashes'][:-1]
        txs = work['transactions'][:12]
        self.assertEqual(p2pool_work.immutable_template_violation(
            net, work, work['transaction_hashes'][:12], txs), None)


# --------------------------------------------------------------------------
# tests: submit bytes (RPC + P2P)
# --------------------------------------------------------------------------
class SubmitBytesKAT(unittest.TestCase):

    def setUp(self):
        self.net = offline_ltc_net()
        self.work = budget_gbt()
        self.template = select_template(self.net, self.work, LEGACY_BUDGET)
        self.template['mweb'] = self.work['mweb']   # as WorkerBridge._get_gentx_template binds it
        self.block = build_block(self.work, self.template, self.net)

    def test_rpc_bytes_parse_with_hogex_last_and_mweb(self):
        raw = rpc_submit_bytes(self.block, self.work, self.net)
        parsed = parse_block(raw)
        self.assertEqual(parsed['tx_count'], len(self.work['transactions']) + 1)
        self.assertTrue(parsed['flags'][-1] & 0x08, 'last tx is not a HogEx')
        self.assertEqual(parsed['rest'], ('01' + MWEB_JOB).decode('hex'))

    def test_p2p_payload_equals_rpc_bytes(self):
        raw = rpc_submit_bytes(self.block, self.work, self.net)
        command, payload = unframe(p2p_frame_via_broadcaster(self.block))
        self.assertEqual(command, 'block')
        self.assertEqual(payload, raw)
        self.assertEqual(payload, bitcoin_data.block_type.pack(self.block) + ('01' + MWEB_JOB).decode('hex'))

    def test_p2p_without_mweb_is_byte_identical_to_send_block(self):
        block = dict(self.block)
        del block['mweb']
        frame = p2p_frame_via_broadcaster(block)
        proto = bitcoin_p2p.Protocol(ltc_net.PARENT)
        proto.transport = FakeTransport()
        proto.send_block(block=block)               # the pre-fix path
        self.assertEqual(frame, ''.join(proto.transport.writes))
        self.assertEqual(unframe(frame)[1], bitcoin_data.block_type.pack(block))


class JobBoundMweb(unittest.TestCase):

    # Template refreshed after the job was handed out: the current GBT's
    # extension belongs to a different HogEx. The job's must be submitted.
    def test_rpc_uses_job_mweb_not_current(self):
        net = offline_ltc_net()
        job = budget_gbt(mweb_hex=MWEB_JOB)
        current = budget_gbt(mweb_hex=MWEB_CUR, hogex_tag='\x02')
        t = select_template(net, job, LEGACY_BUDGET)
        t['mweb'] = job['mweb']
        block = build_block(job, t, net)
        raw = rpc_submit_bytes(block, current, net)
        self.assertEqual(parse_block(raw)['rest'], ('01' + MWEB_JOB).decode('hex'))

    # Blocks built without a job-bound extension (peer path, VERSION < 34)
    # keep falling back to the current GBT's.
    def test_rpc_falls_back_to_current_without_job_mweb(self):
        net = offline_ltc_net()
        work = budget_gbt(mweb_hex=MWEB_CUR)
        block = build_block(work, select_template(net, work, LEGACY_BUDGET), net)
        del block['mweb']
        raw = rpc_submit_bytes(block, work, net)
        self.assertEqual(parse_block(raw)['rest'], ('01' + MWEB_CUR).decode('hex'))


# --------------------------------------------------------------------------
# tests: the real WorkerBridge.get_work() / got_response()
# --------------------------------------------------------------------------
class _Stop(Exception):
    pass


def _stop(*args, **kwargs):
    raise _Stop()


class _Var(object):
    def __init__(self, value):
        self.value = value


class _Args(object):
    coinb_texts = []


class _Ratchet(object):
    def get_share_version(self, tracker, best, net):
        return SHARE_TYPE, SHARE_TYPE.VERSION


class _Node(object):
    def __init__(self, net, work):
        self.net = net
        self.tracker = forest.Tracker()
        self.best_share_var = _Var(None)
        self.bitcoind_work = _Var(work)
        self.broadcaster = None


def make_bridge(net, work, use_template_cache):
    from p2pool import fillbudget
    wb = p2pool_work.WorkerBridge.__new__(p2pool_work.WorkerBridge)
    wb.node = _Node(net, work)
    wb.current_work = _Var(work)
    wb.args = _Args()
    wb.auto_ratchet = _Ratchet()
    wb.donation_percentage = 1.0
    wb.transition_message_data = None
    wb.differential_capture = None
    wb.use_gentx_template_cache = use_template_cache
    wb._gentx_template_cache = None
    wb.fill_budgets = fillbudget.FillBudgetBook()
    wb._g2_parent_tag = 'ltc'
    wb.fill_budgets.register('ltc', fillbudget.FillBudget('ltc', rate=6666, burst=LEGACY_BUDGET,
                                                          floor=LEGACY_BUDGET, clock=lambda: 0.0))
    wb._g2_frozen_grant = None
    wb._g2_last_parent_key = None
    wb.new_work_event = variable.Event()
    wb.parent_block_event = variable.Event()
    wb.last_work_shares = _Var({})
    # stubs for the parts of get_work() unrelated to the block body
    wb._persist_serve_gate_active = lambda: False
    wb._build_user_specific_merged_work = lambda *a, **k: {}
    wb.is_v36_active = lambda: (True, True)
    wb.get_local_addr_rates = lambda: {}
    wb._detect_whale_departure = lambda: False
    wb._clamp_stale_tip_serve_target = lambda target, prev: target
    wb.get_stale_counts = lambda: ((0, 0), 0, (0, 0))
    wb.get_user_details = _stop    # got_response() stops right after the submit
    return wb


class GetWorkBlockBody(unittest.TestCase):

    def setUp(self):
        self._real_submit = helper.submit_block
        self._real_debug = p2pool.DEBUG
        self.captured = []

        def capture(block, ignore_failure, node, broadcaster=None):
            self.captured.append(block)
            return defer.succeed(None)
        p2pool_work.helper.submit_block = capture

    def tearDown(self):
        p2pool_work.helper.submit_block = self._real_submit
        p2pool.DEBUG = self._real_debug

    def _find(self, wb, ba, got):
        header = dict(version=ba['version'], previous_block=ba['previous_block'], merkle_root=0,
                      timestamp=ba['timestamp'], bits=ba['bits'], nonce=0)
        p2pool.DEBUG = True        # submit regardless of PoW (got_response's own debug switch)
        try:
            got(header, 'user', '\x00' * wb.COINBASE_NONCE_LENGTH)
        except _Stop:
            pass
        finally:
            p2pool.DEBUG = self._real_debug
        self.assertEqual(len(self.captured), 1)
        return self.captured[0]

    def _job_vs_current(self, use_template_cache):
        net = offline_ltc_net()
        job = budget_gbt(mweb_hex=MWEB_JOB)
        current = budget_gbt(mweb_hex=MWEB_CUR, hogex_tag='\x02')
        wb = make_bridge(net, job, use_template_cache)
        ba, got = wb.get_work('user', PUBKEY_HASH, 0, 2**256 - 1, 2**256 - 1, None)
        # GBT refreshes (new HogEx + extension) before the miner finds the block
        wb.current_work.value = current
        wb.node.bitcoind_work.value = current
        block = self._find(wb, ba, got)
        self.assertEqual(len(block['txs']), len(job['transactions']) + 1)
        self.assertEqual(block['txs'][-1], job['transactions'][-1]['data'])
        raw = rpc_submit_bytes(block, current, net)
        parsed = parse_block(raw)
        self.assertEqual(parsed['tx_count'], len(job['transactions']) + 1)
        self.assertTrue(parsed['flags'][-1] & 0x08)
        self.assertEqual(parsed['rest'], ('01' + MWEB_JOB).decode('hex'))

    def test_job_mweb_template_path(self):
        self._job_vs_current(True)

    def test_job_mweb_legacy_path(self):
        self._job_vs_current(False)

    def _refuses(self, work, use_template_cache):
        wb = make_bridge(offline_ltc_net(), work, use_template_cache)
        self.assertRaises(jsonrpc.Error_for_code(-12345), wb.get_work,
                          'user', PUBKEY_HASH, 0, 2**256 - 1, 2**256 - 1, None)

    def test_refuses_mweb_without_hogex_last(self):
        work = budget_gbt()
        work['transactions'] = work['transactions'][:-1]          # HogEx gone, extension kept
        work['transaction_hashes'] = work['transaction_hashes'][:-1]
        work['transaction_fees'] = work['transaction_fees'][:-1]
        self._refuses(work, True)
        self._refuses(work, False)

    def test_refuses_hogex_without_mweb(self):
        work = budget_gbt()
        work['mweb'] = ''
        self._refuses(work, True)
        self._refuses(work, False)


class TemplateGuard(unittest.TestCase):
    '''work.immutable_template_violation(): the pure guard.'''

    def setUp(self):
        self.work = budget_gbt()
        self.hashes = list(self.work['transaction_hashes'])
        self.txs = list(self.work['transactions'])

    def check(self, hashes, txs, work=None):
        return p2pool_work.immutable_template_violation(ltc_net, work or self.work, hashes, txs)

    def test_verbatim_ok(self):
        self.assertEqual(self.check(self.hashes, self.txs), None)

    def test_truncated(self):
        self.assertTrue('12 of 101' in self.check(self.hashes[:12], self.txs[:12]))

    def test_reordered(self):
        h, t = self.hashes[:], self.txs[:]
        h[0], h[1] = h[1], h[0]
        t[0], t[1] = t[1], t[0]
        self.assertTrue('reordered' in self.check(h, t))

    def test_hogex_detection(self):
        self.assertTrue(p2pool_work.is_hogex_tx_hex(hogex_tx()['data']))
        self.assertFalse(p2pool_work.is_hogex_tx_hex(plain_tx(1)['data']))
        self.assertFalse(p2pool_work.is_hogex_tx_hex(witness_tx(1)['data']))

    def test_mweb_rule_from_gbt_rules_alone(self):
        net = NetOverlay(ltc_net, IMMUTABLE_BLOCKS=False, SOFTFORKS_REQUIRED=set(['segwit']))
        bad = self.txs[:-1]
        self.assertTrue(p2pool_work.immutable_template_violation(net, self.work, self.hashes[:-1], bad))
        no_rule = dict(self.work, rules=['csv', '!segwit'])
        self.assertEqual(p2pool_work.immutable_template_violation(net, no_rule, self.hashes[:-1], bad), None)


if __name__ == '__main__':
    unittest.main()
