from p2pool.bitcoin import networks

PARENT = networks.nets['bitcoin_testnet']
SHARE_PERIOD = 20 # seconds
CHAIN_LENGTH = 24*60*60//20 # shares
REAL_CHAIN_LENGTH = 24*60*60//20 # shares
TARGET_LOOKBEHIND = 100 # shares
SPREAD = 10 # blocks
IDENTIFIER = 'a1b2c3d4e5f60718'.decode('hex')
PREFIX = '7e8f9a0b1c2d3e4f'.decode('hex')
COINBASEEXT = '0E2F5032506F6F6C2D7442544321'.decode('hex')  # /P2Pool-tBTC!
P2P_PORT = 19333
MIN_TARGET = 0
MAX_TARGET = 2**256//2**20 - 1
PERSIST = False
WORKER_PORT = 19332
BOOTSTRAP_ADDRS = ''.split(' ')  # Add testnet bootstrap nodes if available
ANNOUNCE_CHANNEL = ''
VERSION_CHECK = lambda v: True  # Accept all Bitcoin versions for now
MINIMUM_PROTOCOL_VERSION = 1700

# ==== Stratum Vardiff Configuration ====
STRATUM_SHARE_RATE = 10  # Target seconds per pseudoshare

# Vardiff trigger thresholds
VARDIFF_SHARES_TRIGGER = 5
VARDIFF_TIMEOUT_MULT = 3
VARDIFF_QUICKUP_SHARES = 2
VARDIFF_QUICKUP_DIVISOR = 4

# Vardiff adjustment limits
VARDIFF_MIN_ADJUST = 0.25
VARDIFF_MAX_ADJUST = 4.0

# ==== Connection Threat Detection ====
CONNECTION_WORKER_ELEVATED = 4.0
CONNECTION_WORKER_WARNING = 6.0
