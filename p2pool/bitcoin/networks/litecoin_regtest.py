import os
import platform

from twisted.internet import defer

from .. import data, helper
from p2pool.util import pack


# Litecoin Core -regtest parent. Test-rig only (T4 DOA-under-load A/B): the
# daemon must be started with -vbparams=mweb:9223372036854775806:9223372036854775807
# or MWEB activates at h432 and CreateNewBlock stops producing valid templates.
P2P_PREFIX = 'fabfb5da'.decode('hex')
P2P_PORT = 19444
ADDRESS_VERSION = 111
ADDRESS_P2SH_VERSION = 58
HUMAN_READABLE_PART = 'rltc'
RPC_PORT = 19443
RPC_CHECK = defer.inlineCallbacks(lambda bitcoind: defer.returnValue(
            'getreceivedbyaddress' in (yield bitcoind.rpc_help()) and
            (yield bitcoind.rpc_getblockchaininfo())['chain'] == 'regtest'
        ))
SUBSIDY_FUNC = lambda height: 50*100000000 >> (height + 1)//150
POW_FUNC = lambda data: pack.IntType(256).unpack(__import__('ltc_scrypt').getPoWHash(data))
BLOCKHASH_FUNC = POW_FUNC  # same convention as litecoin / litecoin_testnet
BLOCK_PERIOD = 150 # s
SYMBOL = 'rLTC'
CONF_FILE_FUNC = lambda: os.path.join(os.path.join(os.environ['APPDATA'], 'Litecoin') if platform.system() == 'Windows' else os.path.expanduser('~/Library/Application Support/Litecoin/') if platform.system() == 'Darwin' else os.path.expanduser('~/.litecoin'), 'litecoin.conf')
BLOCK_EXPLORER_URL_PREFIX = '#'
ADDRESS_EXPLORER_URL_PREFIX = '#'
TX_EXPLORER_URL_PREFIX = '#'
SANE_TARGET_RANGE = (2**256//2**32//1000 - 1, 2**256//2 - 1)
DUMB_SCRYPT_DIFF = 2**16
DUST_THRESHOLD = 1e8
