import os
import platform

from twisted.internet import defer

from .. import data, helper
from p2pool.util import pack


# DigiByte mainnet (Scrypt algo) network parameters
# Reference: https://github.com/DigiByte-Core/digibyte/blob/develop/src/kernel/chainparams.cpp
# Reference: https://github.com/farsider350/p2pool-dgb-scrypt-350

P2P_PREFIX = 'fac3b6da'.decode('hex')  # DigiByte mainnet magic bytes
P2P_PORT = 12024
P2P_VERSION = 70019  # DGB Core v8.26.2 protocol version
ADDRESS_VERSION = 30   # 0x1e - mainnet addresses start with 'D'
ADDRESS_P2SH_VERSION = 63  # 0x3f - mainnet P2SH addresses start with 'S'
HUMAN_READABLE_PART = 'dgb'  # bech32 HRP for native segwit (dgb1...)
RPC_PORT = 14024
RPC_CHECK = defer.inlineCallbacks(lambda bitcoind: defer.returnValue(
            (yield helper.check_block_header(bitcoind, '7497ea1b465eb39f1c8f507bc877078fe016d6fcb6dfad3a64c98dcc6e1e8496')) and
            (yield bitcoind.rpc_getblockchaininfo())['chain'] == 'main'
        ))

# DigiByte subsidy schedule (pure Python port)
# Source: DigiByte Core validation.cpp + farsider350/p2pool-dgb-scrypt-350/digibyte_subsidy
#
# Constants from DigiByte consensus:
_COIN = 100000000
_DIFF_CHANGE_TARGET = 67200        # DigiShield Hard Fork block
_PATCH_REWARD_DURATION = 10080     # 0.5% decrease every 10080 blocks
_PATCH_REWARD_DURATION2 = 80160    # 1% decrease every 80160 blocks
_PATCH_REWARD_DURATION3 = 400000   # Block after which phase 2 begins

def _dgb_subsidy(nHeight):
    """Get DGB block reward at height (returns satoshis).
    
    Phase 1 (height < 67200): Fixed early rewards
    Phase 2 (67200 <= height < 400000): 8000 DGB base, -0.5% every 10080 blocks
    Phase 3 (height >= 400000): 2459 DGB base, -1% every 80160 blocks
    Minimum: 1 DGB (100000000 satoshis)
    """
    if nHeight < _DIFF_CHANGE_TARGET:
        # Pre-DigiShield fixed rewards
        if nHeight < 1440:
            return 72000 * _COIN
        elif nHeight < 5760:
            return 16000 * _COIN
        else:
            return 8000 * _COIN
    elif nHeight < _PATCH_REWARD_DURATION3:
        # Phase 2: 0.5% decrease every 10080 blocks from 8000 DGB base
        subsidy = 8000 * _COIN
        blocks = nHeight - _DIFF_CHANGE_TARGET
        weeks = (blocks // _PATCH_REWARD_DURATION) + 1
        for _ in xrange(weeks):
            subsidy -= subsidy // 200
        return max(subsidy, _COIN)
    else:
        # Phase 3: 1% decrease every 80160 blocks from 2459 DGB base
        subsidy = 2459 * _COIN
        blocks = nHeight - _PATCH_REWARD_DURATION3
        weeks = (blocks // _PATCH_REWARD_DURATION2) + 1
        for _ in xrange(weeks):
            subsidy -= subsidy // 100
        return max(subsidy, _COIN)

SUBSIDY_FUNC = _dgb_subsidy

# DigiByte Scrypt PoW (identical to Litecoin)
POW_FUNC = lambda data: pack.IntType(256).unpack(__import__('ltc_scrypt').getPoWHash(data))
BLOCKHASH_FUNC = POW_FUNC  # For scrypt coins, block hash and PoW hash are the same

BLOCK_PERIOD = 15  # 15 seconds (75s per algo / 5 algos)
SYMBOL = 'DGB'
CONF_FILE_FUNC = lambda: os.path.join(
    os.path.join(os.environ['APPDATA'], 'DigiByte') if platform.system() == 'Windows'
    else os.path.expanduser('~/Library/Application Support/DigiByte/') if platform.system() == 'Darwin'
    else os.path.expanduser('~/.digibyte'),
    'digibyte.conf'
)
BLOCK_EXPLORER_URL_PREFIX = 'https://digiexplorer.info/block/'
ADDRESS_EXPLORER_URL_PREFIX = 'https://digiexplorer.info/address/'
TX_EXPLORER_URL_PREFIX = 'https://digiexplorer.info/tx/'

# SANE_TARGET_RANGE: (hardest/min_target, easiest/max_target)
# DGB Scrypt current difficulty ~337K (target ~8e61)
# Min allows ~700x headroom for future difficulty growth
SANE_TARGET_RANGE = (2**256//10**17 - 1, 2**256//2**20 - 1)
DUMB_SCRYPT_DIFF = 2**16
DUST_THRESHOLD = 0.03e8

# DigiByte has segwit and taproot active on mainnet
SOFTFORKS_REQUIRED = set(['csv', 'segwit'])
