"""
Multi-Blockchain Explorer Proxy

Provides a unified REST API for querying block, address, and transaction data
across multiple chains. Proxies to upstream explorers (blockchair, chainz, etc.)
with response caching to reduce upstream load and improve latency.

Endpoints:
    GET /explorer/{chain}/block/{hash_or_height}
    GET /explorer/{chain}/address/{address}
    GET /explorer/{chain}/tx/{txid}
    GET /explorer/{chain}/info
    GET /explorer/chains
"""

import logging
import time
import re
from collections import OrderedDict

import aiohttp
from aiohttp import web

from .config import CHAIN_DEFAULTS

log = logging.getLogger('p2pool-service.explorer')

# Input validation patterns
_HEX64 = re.compile(r'^[0-9a-fA-F]{1,64}$')
_HEIGHT = re.compile(r'^[0-9]{1,10}$')
_ADDR = re.compile(r'^[a-zA-Z0-9]{20,130}$')


class LRUCache:
    """Simple TTL-aware LRU cache."""

    def __init__(self, max_items=10000):
        self.max_items = max_items
        self._store = OrderedDict()  # key -> (value, expiry_time)

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.time() > expiry:
            del self._store[key]
            return None
        # Move to end (most recently used)
        self._store.move_to_end(key)
        return value

    def put(self, key, value, ttl):
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.time() + ttl)
        # Evict oldest if over capacity
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)

    @property
    def size(self):
        return len(self._store)


class ExplorerProxy:
    """Proxies explorer requests to upstream APIs with caching."""

    def __init__(self, config):
        self.config = config.get('explorer', {})
        self.cache = LRUCache(self.config.get('cache_max_items', 10000))
        self.session = None
        self.timeout = aiohttp.ClientTimeout(
            total=self.config.get('upstream_timeout', 15)
        )
        self.blockchair_api_key = self.config.get('blockchair_api_key', '')

        # Rate limiting state
        self._request_timestamps = []
        self._rate_limit = self.config.get('rate_limit_per_min', 120)

        # Upstream adapters per chain
        self._adapters = {}
        for chain_id, defaults in CHAIN_DEFAULTS.items():
            self._adapters[chain_id] = ChainAdapter(
                chain_id, defaults, blockchair_key=self.blockchair_api_key
            )

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=self.timeout)

    async def stop(self):
        if self.session:
            await self.session.close()

    def _rate_check(self):
        """Simple sliding-window rate limiter for upstream requests."""
        now = time.time()
        cutoff = now - 60
        self._request_timestamps = [
            t for t in self._request_timestamps if t > cutoff
        ]
        if len(self._request_timestamps) >= self._rate_limit:
            return False
        self._request_timestamps.append(now)
        return True

    async def fetch_block(self, chain, block_id):
        """Fetch block info by hash or height."""
        adapter = self._adapters.get(chain)
        if not adapter:
            return None, 'unsupported chain: %s' % chain

        cache_key = 'block:%s:%s' % (chain, block_id)
        cached = self.cache.get(cache_key)
        if cached:
            cached['_cached'] = True
            return cached, None

        if not self._rate_check():
            return None, 'rate limit exceeded, try again later'

        url = adapter.block_url(block_id)
        data, err = await self._upstream_get(url, adapter)
        if err:
            return None, err

        result = adapter.parse_block(data, block_id)
        ttl = self.config.get('cache_ttl_block', 86400)
        self.cache.put(cache_key, result, ttl)
        return result, None

    async def fetch_address(self, chain, address):
        """Fetch address info."""
        adapter = self._adapters.get(chain)
        if not adapter:
            return None, 'unsupported chain: %s' % chain

        cache_key = 'addr:%s:%s' % (chain, address)
        cached = self.cache.get(cache_key)
        if cached:
            cached['_cached'] = True
            return cached, None

        if not self._rate_check():
            return None, 'rate limit exceeded, try again later'

        url = adapter.address_url(address)
        data, err = await self._upstream_get(url, adapter)
        if err:
            return None, err

        result = adapter.parse_address(data, address)
        ttl = self.config.get('cache_ttl_address', 60)
        self.cache.put(cache_key, result, ttl)
        return result, None

    async def fetch_tx(self, chain, txid):
        """Fetch transaction info."""
        adapter = self._adapters.get(chain)
        if not adapter:
            return None, 'unsupported chain: %s' % chain

        cache_key = 'tx:%s:%s' % (chain, txid)
        cached = self.cache.get(cache_key)
        if cached:
            cached['_cached'] = True
            return cached, None

        if not self._rate_check():
            return None, 'rate limit exceeded, try again later'

        url = adapter.tx_url(txid)
        data, err = await self._upstream_get(url, adapter)
        if err:
            return None, err

        result = adapter.parse_tx(data, txid)
        ttl = self.config.get('cache_ttl_tx', 86400)
        self.cache.put(cache_key, result, ttl)
        return result, None

    async def _upstream_get(self, url, adapter):
        """Make an upstream HTTP GET with error handling."""
        try:
            headers = adapter.request_headers()
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    return None, 'not found'
                if resp.status == 429:
                    return None, 'upstream rate limited'
                if resp.status != 200:
                    return None, 'upstream error: HTTP %d' % resp.status
                data = await resp.json(content_type=None)
                return data, None
        except aiohttp.ClientError as e:
            log.warning('Upstream request failed: %s — %s', url, e)
            return None, 'upstream connection error'
        except Exception as e:
            log.error('Unexpected error fetching %s: %s', url, e)
            return None, 'internal error'


class ChainAdapter:
    """
    Per-chain adapter that knows how to build URLs and parse responses
    from specific upstream explorer APIs.

    Supports: blockchair.com, chainz.cryptoid.info, blockchain.info,
              digiexplorer.info, explorer.bitcoin.com
    """

    def __init__(self, chain_id, defaults, blockchair_key=''):
        self.chain_id = chain_id
        self.defaults = defaults
        self.base = defaults.get('explorer_base', '')
        self.blockchair_key = blockchair_key

    def request_headers(self):
        """Headers for upstream requests."""
        return {'Accept': 'application/json', 'User-Agent': 'p2pool-service/1.0'}

    def _append_blockchair_key(self, url):
        """Append blockchair API key as query parameter if configured."""
        if self.blockchair_key:
            sep = '&' if '?' in url else '?'
            return '%s%skey=%s' % (url, sep, self.blockchair_key)
        return url

    # ── URL builders ──────────────────────────────────────────────────────

    def block_url(self, block_id):
        base = self.base
        if 'blockchair.com' in base:
            url = '%s/dashboards/block/%s' % (base, block_id)
            return self._append_blockchair_key(url)
        elif 'chainz.cryptoid.info' in base:
            # chainz API
            return '%s/api.dws?q=getblockinfo&height=%s' % (base, block_id)
        elif 'blockchain.info' in base:
            return 'https://blockchain.info/rawblock/%s' % block_id
        elif 'digiexplorer.info' in base:
            return 'https://digiexplorer.info/api/block/%s' % block_id
        elif 'explorer.bitcoin.com' in base:
            return '%s/api/block/%s' % (base, block_id)
        return '%s/api/block/%s' % (base, block_id)

    def address_url(self, address):
        base = self.base
        if 'blockchair.com' in base:
            url = '%s/dashboards/address/%s' % (base, address)
            return self._append_blockchair_key(url)
        elif 'chainz.cryptoid.info' in base:
            return '%s/api.dws?q=getbalance&a=%s' % (base, address)
        elif 'blockchain.info' in base:
            return 'https://blockchain.info/rawaddr/%s?limit=10' % address
        elif 'digiexplorer.info' in base:
            return 'https://digiexplorer.info/api/addr/%s' % address
        elif 'explorer.bitcoin.com' in base:
            return '%s/api/addr/%s' % (base, address)
        return '%s/api/address/%s' % (base, address)

    def tx_url(self, txid):
        base = self.base
        if 'blockchair.com' in base:
            url = '%s/dashboards/transaction/%s' % (base, txid)
            return self._append_blockchair_key(url)
        elif 'chainz.cryptoid.info' in base:
            return '%s/api.dws?q=txinfo&t=%s' % (base, txid)
        elif 'blockchain.info' in base:
            return 'https://blockchain.info/rawtx/%s' % txid
        elif 'digiexplorer.info' in base:
            return 'https://digiexplorer.info/api/tx/%s' % txid
        elif 'explorer.bitcoin.com' in base:
            return '%s/api/tx/%s' % (base, txid)
        return '%s/api/tx/%s' % (base, txid)

    # ── Response parsers ──────────────────────────────────────────────────

    def parse_block(self, data, block_id):
        """Normalize upstream block response to common format."""
        if 'blockchair.com' in self.base:
            return self._parse_blockchair_block(data, block_id)
        elif 'blockchain.info' in self.base:
            return self._parse_blockchain_info_block(data, block_id)
        # Generic fallback: return as-is with metadata
        return {
            'chain': self.chain_id,
            'block_id': block_id,
            'source': self.base,
            'raw': data,
        }

    def parse_address(self, data, address):
        if 'blockchair.com' in self.base:
            return self._parse_blockchair_address(data, address)
        return {
            'chain': self.chain_id,
            'address': address,
            'source': self.base,
            'raw': data,
        }

    def parse_tx(self, data, txid):
        if 'blockchair.com' in self.base:
            return self._parse_blockchair_tx(data, txid)
        return {
            'chain': self.chain_id,
            'txid': txid,
            'source': self.base,
            'raw': data,
        }

    # ── Blockchair parsers ────────────────────────────────────────────────

    def _parse_blockchair_block(self, data, block_id):
        try:
            block_data = data.get('data', {}).get(str(block_id), {})
            block = block_data.get('block', {})
            return {
                'chain': self.chain_id,
                'hash': block.get('hash', ''),
                'height': block.get('id'),
                'time': block.get('time', ''),
                'size': block.get('size'),
                'tx_count': block.get('transaction_count'),
                'difficulty': block.get('difficulty'),
                'reward': block.get('reward'),
                'miner': block.get('guessed_miner', ''),
                'source': 'blockchair.com',
            }
        except Exception:
            return {'chain': self.chain_id, 'block_id': block_id, 'raw': data}

    def _parse_blockchair_address(self, data, address):
        try:
            addr_data = data.get('data', {}).get(address, {})
            info = addr_data.get('address', {})
            return {
                'chain': self.chain_id,
                'address': address,
                'balance': info.get('balance'),
                'received': info.get('received'),
                'spent': info.get('spent'),
                'tx_count': info.get('transaction_count'),
                'first_seen': info.get('first_seen_receiving'),
                'last_seen': info.get('last_seen_receiving'),
                'source': 'blockchair.com',
            }
        except Exception:
            return {'chain': self.chain_id, 'address': address, 'raw': data}

    def _parse_blockchair_tx(self, data, txid):
        try:
            tx_data = data.get('data', {}).get(txid, {})
            tx = tx_data.get('transaction', {})
            return {
                'chain': self.chain_id,
                'txid': txid,
                'block_id': tx.get('block_id'),
                'time': tx.get('time', ''),
                'size': tx.get('size'),
                'fee': tx.get('fee'),
                'input_total': tx.get('input_total'),
                'output_total': tx.get('output_total'),
                'input_count': tx.get('input_count'),
                'output_count': tx.get('output_count'),
                'source': 'blockchair.com',
            }
        except Exception:
            return {'chain': self.chain_id, 'txid': txid, 'raw': data}

    # ── blockchain.info parsers ───────────────────────────────────────────

    def _parse_blockchain_info_block(self, data, block_id):
        try:
            return {
                'chain': self.chain_id,
                'hash': data.get('hash', ''),
                'height': data.get('height'),
                'time': data.get('time'),
                'size': data.get('size'),
                'tx_count': len(data.get('tx', [])),
                'difficulty': data.get('bits'),
                'source': 'blockchain.info',
            }
        except Exception:
            return {'chain': self.chain_id, 'block_id': block_id, 'raw': data}


def setup_routes(app, explorer, auth_check):
    """Register explorer HTTP routes."""

    async def handle_chains(request):
        """GET /explorer/chains — List supported chains."""
        chains = []
        for chain_id, defaults in CHAIN_DEFAULTS.items():
            chains.append({
                'id': chain_id,
                'name': defaults['name'],
                'symbol': defaults['symbol'],
                'explorer_block': defaults['explorer_block'],
                'explorer_address': defaults['explorer_address'],
                'explorer_tx': defaults['explorer_tx'],
            })
        return web.json_response({'chains': chains},
                                 headers={'Access-Control-Allow-Origin': '*'})

    async def handle_block(request):
        """GET /explorer/{chain}/block/{block_id}"""
        chain = request.match_info['chain'].lower()
        block_id = request.match_info['block_id']

        # Validate input
        if not (_HEX64.match(block_id) or _HEIGHT.match(block_id)):
            return web.json_response({'error': 'invalid block_id'}, status=400)

        data, err = await explorer.fetch_block(chain, block_id)
        if err:
            status = 404 if 'not found' in err else 429 if 'rate' in err else 502
            return web.json_response({'error': err}, status=status)

        return web.json_response(data, headers={'Access-Control-Allow-Origin': '*'})

    async def handle_address(request):
        """GET /explorer/{chain}/address/{address}"""
        chain = request.match_info['chain'].lower()
        address = request.match_info['address']

        if not _ADDR.match(address):
            return web.json_response({'error': 'invalid address'}, status=400)

        data, err = await explorer.fetch_address(chain, address)
        if err:
            status = 404 if 'not found' in err else 429 if 'rate' in err else 502
            return web.json_response({'error': err}, status=status)

        return web.json_response(data, headers={'Access-Control-Allow-Origin': '*'})

    async def handle_tx(request):
        """GET /explorer/{chain}/tx/{txid}"""
        chain = request.match_info['chain'].lower()
        txid = request.match_info['txid']

        if not _HEX64.match(txid):
            return web.json_response({'error': 'invalid txid'}, status=400)

        data, err = await explorer.fetch_tx(chain, txid)
        if err:
            status = 404 if 'not found' in err else 429 if 'rate' in err else 502
            return web.json_response({'error': err}, status=status)

        return web.json_response(data, headers={'Access-Control-Allow-Origin': '*'})

    async def handle_cache_stats(request):
        """GET /explorer/cache — Cache statistics."""
        return web.json_response({
            'cache_size': explorer.cache.size,
            'cache_max': explorer.config.get('cache_max_items', 10000),
            'rate_budget_used': len(explorer._request_timestamps),
            'rate_budget_max': explorer._rate_limit,
        }, headers={'Access-Control-Allow-Origin': '*'})

    app.router.add_get('/explorer/chains', handle_chains)
    app.router.add_get('/explorer/cache', handle_cache_stats)
    app.router.add_get('/explorer/{chain}/block/{block_id}', handle_block)
    app.router.add_get('/explorer/{chain}/address/{address}', handle_address)
    app.router.add_get('/explorer/{chain}/tx/{txid}', handle_tx)
