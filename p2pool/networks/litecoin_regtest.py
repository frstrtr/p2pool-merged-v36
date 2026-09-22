from p2pool.bitcoin import networks

# Isolated LTC regtest sharechain for the T4 stratum-swarm DOA-under-load A/B.
# Own IDENTIFIER/PREFIX so it can never peer with testnet or mainnet nodes.
PARENT = networks.nets['litecoin_regtest']
SHARE_PERIOD = 4 # seconds
CHAIN_LENGTH = 20*60//3 # shares
REAL_CHAIN_LENGTH = 20*60//3 # shares
TARGET_LOOKBEHIND = 200 # shares
SPREAD = 3 # blocks
IDENTIFIER = '7c2e4b19d0a3f85e'.decode('hex')
PREFIX = '3b91e6d27fa04c18'.decode('hex')
P2P_PORT = 19478
MIN_TARGET = 0
MAX_TARGET = 2**256//20 - 1
PERSIST = False
WORKER_PORT = 19477
BOOTSTRAP_ADDRS = []
ANNOUNCE_CHANNEL = '#p2pool-alt'
VERSION_CHECK = lambda v: True
# 'mweb' stays listed even though the rig keeps the deployment inactive (see
# parent): Litecoin Core 0.21 rejects getblocktemplate without the mweb rule.
SOFTFORKS_REQUIRED = set(['bip65', 'csv', 'segwit', 'taproot', 'mweb'])
MINIMUM_PROTOCOL_VERSION = 3301
SEGWIT_ACTIVATION_VERSION = 17
BLOCK_MAX_SIZE = 1000000
BLOCK_MAX_WEIGHT = 4000000
IMMUTABLE_BLOCKS = True
