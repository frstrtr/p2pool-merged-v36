"""
RPC Proxy — Authenticated JSON-RPC relay to blockchain daemons.

Allows p2pool nodes to connect to hosted daemons instead of running their own.
Only whitelisted RPC methods are forwarded. Write operations (submitblock)
require elevated API key permissions.

Endpoints:
    POST /rpc/{chain}          — JSON-RPC relay
    GET  /rpc/{chain}/status   — Daemon connectivity check
    GET  /rpc/chains           — List configured daemon chains
"""

import base64
import json
import logging
import time
from collections import defaultdict

import aiohttp
from aiohttp import web

log = logging.getLogger('p2pool-service.rpc_proxy')


# Methods safe for read-only access (no wallet, no mutations)
READ_METHODS = frozenset([
    # Block & chain queries
    'getbestblockhash',
    'getblock',
    'getblockchaininfo',
    'getblockcount',
    'getblockhash',
    'getblockheader',
    'getblocktemplate',
    'getchaintips',
    'getdifficulty',

    # Network
    'getnetworkinfo',
    'getpeerinfo',
    'getconnectioncount',
    'getnettotals',

    # Mining
    'getmininginfo',
    'getnetworkhashps',

    # Mempool
    'getmempoolinfo',
    'getrawmempool',
    'getmempoolentry',

    # Transaction queries
    'getrawtransaction',
    'decoderawtransaction',
    'decodescript',
    'gettxout',
    'gettxoutproof',

    # Address validation
    'validateaddress',

    # Misc
    'getinfo',
    'help',

    # Merged mining
    'getauxblock',
    'createauxblock',
    'getblocktemplate',

    # Memory pool (legacy)
    'getmemorypool',
])

# Methods requiring elevated (write) access
WRITE_METHODS = frozenset([
    'submitblock',
    'submitauxblock',
    'sendrawtransaction',
])

# Methods that are NEVER proxied (security-critical)
BLOCKED_METHODS = frozenset([
    'dumpprivkey',
    'dumpwallet',
    'importprivkey',
    'importwallet',
    'signmessage',
    'signrawtransaction',
    'signrawtransactionwithkey',
    'signrawtransactionwithwallet',
    'walletpassphrase',
    'walletpassphrasechange',
    'encryptwallet',
    'backupwallet',
    'keypoolrefill',
    'listunspent',
    'sendtoaddress',
    'sendmany',
    'settxfee',
    'stop',
    'addnode',
    'disconnectnode',
    'setban',
    'clearbanned',
])


class RPCProxy:
    """Proxies JSON-RPC calls to configured blockchain daemon backends."""

    def __init__(self, config):
        self.config = config.get('rpc_proxy', {})
        self.daemons = {}  # chain -> DaemonConnection
        self.session = None

        # Per-IP rate limiting
        self._rate_limit = self.config.get('rate_limit_per_min', 60)
        self._ip_timestamps = defaultdict(list)

        # Load daemon configs
        for chain, daemon_cfg in self.config.get('daemons', {}).items():
            self.daemons[chain] = DaemonConnection(chain, daemon_cfg)
            log.info('RPC daemon configured: %s → %s:%s',
                     chain, daemon_cfg.get('host', '127.0.0.1'),
                     daemon_cfg.get('port'))

    async def start(self):
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def stop(self):
        if self.session:
            await self.session.close()

    def _rate_check(self, client_ip):
        """Per-IP sliding window rate limiter."""
        now = time.time()
        cutoff = now - 60
        ts = self._ip_timestamps[client_ip]
        self._ip_timestamps[client_ip] = [t for t in ts if t > cutoff]
        if len(self._ip_timestamps[client_ip]) >= self._rate_limit:
            return False
        self._ip_timestamps[client_ip].append(now)
        return True

    async def relay(self, chain, rpc_request, client_ip, write_access=False):
        """
        Relay a JSON-RPC request to the appropriate daemon.

        Returns (response_dict, error_string).
        """
        daemon = self.daemons.get(chain)
        if not daemon:
            return None, 'no daemon configured for chain: %s' % chain

        method = rpc_request.get('method', '').lower()
        params = rpc_request.get('params', [])
        rpc_id = rpc_request.get('id', 1)

        # Method validation
        if method in BLOCKED_METHODS:
            log.warning('Blocked RPC method %s from %s', method, client_ip)
            return None, 'method not allowed: %s' % method

        if method in WRITE_METHODS and not write_access:
            return None, 'write access required for method: %s' % method

        if method not in READ_METHODS and method not in WRITE_METHODS:
            return None, 'unknown method: %s' % method

        # Rate limiting
        if not self._rate_check(client_ip):
            return None, 'rate limit exceeded'

        # Forward to daemon
        try:
            result = await daemon.call(self.session, method, params, rpc_id)
            return result, None
        except Exception as e:
            log.error('RPC relay error [%s] %s: %s', chain, method, e)
            return None, 'daemon error: %s' % str(e)


class DaemonConnection:
    """Manages connection details for a single blockchain daemon."""

    def __init__(self, chain, cfg):
        self.chain = chain
        self.host = cfg.get('host', '127.0.0.1')
        self.port = int(cfg.get('port', 8332))
        self.user = cfg.get('user', '')
        self.password = cfg.get('password', '')
        self.url = 'http://%s:%d' % (self.host, self.port)

        # Pre-compute auth header
        if self.user:
            creds = '%s:%s' % (self.user, self.password)
            b64 = base64.b64encode(creds.encode()).decode()
            self._auth_header = 'Basic %s' % b64
        else:
            self._auth_header = None

    async def call(self, session, method, params, rpc_id=1):
        """Make a JSON-RPC call to the daemon."""
        payload = {
            'jsonrpc': '1.0',
            'id': rpc_id,
            'method': method,
            'params': params,
        }
        headers = {'Content-Type': 'application/json'}
        if self._auth_header:
            headers['Authorization'] = self._auth_header

        async with session.post(self.url, json=payload, headers=headers) as resp:
            if resp.status == 401:
                raise RuntimeError('daemon authentication failed')
            if resp.status == 403:
                raise RuntimeError('daemon access forbidden')
            result = await resp.json(content_type=None)
            return result

    async def check_health(self, session):
        """Quick health check — call getblockchaininfo."""
        try:
            result = await self.call(session, 'getblockchaininfo', [])
            error = result.get('error')
            if error:
                return {'chain': self.chain, 'status': 'error',
                        'error': str(error)}
            info = result.get('result', {})
            return {
                'chain': self.chain,
                'status': 'ok',
                'blocks': info.get('blocks'),
                'headers': info.get('headers'),
                'chain_name': info.get('chain', ''),
                'verification_progress': info.get('verificationprogress'),
            }
        except Exception as e:
            return {'chain': self.chain, 'status': 'unreachable', 'error': str(e)}


def setup_routes(app, rpc_proxy, auth_check):
    """Register RPC proxy HTTP routes."""

    async def handle_relay(request):
        """POST /rpc/{chain} — JSON-RPC relay."""
        chain = request.match_info['chain'].lower()

        # Auth check
        if rpc_proxy.config.get('require_api_key') and not auth_check(request):
            return web.json_response(
                {'jsonrpc': '2.0', 'error': {'code': -1, 'message': 'unauthorized'}, 'id': None},
                status=401
            )

        try:
            rpc_request = await request.json()
        except Exception:
            return web.json_response(
                {'jsonrpc': '2.0', 'error': {'code': -32700, 'message': 'parse error'}, 'id': None},
                status=400
            )

        # Determine write access from API key tier
        write_access = _has_write_access(request)
        client_ip = request.remote or '0.0.0.0'

        result, err = await rpc_proxy.relay(chain, rpc_request, client_ip, write_access)
        if err:
            status = 403 if 'not allowed' in err or 'write access' in err else \
                     429 if 'rate limit' in err else \
                     502 if 'daemon' in err else 400
            return web.json_response(
                {'jsonrpc': '2.0', 'error': {'code': -1, 'message': err},
                 'id': rpc_request.get('id')},
                status=status
            )

        return web.json_response(result)

    async def handle_status(request):
        """GET /rpc/{chain}/status — Daemon health check."""
        chain = request.match_info['chain'].lower()
        daemon = rpc_proxy.daemons.get(chain)
        if not daemon:
            return web.json_response(
                {'error': 'no daemon for chain: %s' % chain}, status=404
            )
        health = await daemon.check_health(rpc_proxy.session)
        return web.json_response(health, headers={'Access-Control-Allow-Origin': '*'})

    async def handle_chains(request):
        """GET /rpc/chains — List configured daemon chains."""
        chains = []
        for chain, daemon in rpc_proxy.daemons.items():
            chains.append({
                'chain': chain,
                'host': daemon.host,
                'port': daemon.port,
            })
        return web.json_response({'chains': chains},
                                 headers={'Access-Control-Allow-Origin': '*'})

    app.router.add_post('/rpc/{chain}', handle_relay)
    app.router.add_get('/rpc/{chain}/status', handle_status)
    app.router.add_get('/rpc/chains', handle_chains)


def _has_write_access(request):
    """Check if request has write-tier API key (key ending with ':w')."""
    api_key = request.headers.get('X-API-Key', '')
    if not api_key:
        api_key = request.query.get('api_key', '')
    return api_key.endswith(':w')
