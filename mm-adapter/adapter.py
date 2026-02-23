#!/usr/bin/env python3
"""
Merged Mining RPC Adapter (v2 — optimized)

Acts as a bridge between P2Pool and standard cryptocurrency daemons for merged mining.
Translates P2Pool's merged mining RPC calls to standard daemon RPC methods.

Optimizations over v1:
  1. Persistent HTTP session — reuses TCP connection to dogecoind (no per-call handshake)
  2. Background poller — pre-fetches template every 1s so P2Pool gets instant responses
  3. Cached getbestblockhash — returns cached tip hash with zero upstream cost
  4. Parallel RPC — createauxblock + getblocktemplate fetched concurrently

P2Pool tries these methods in order:
1. getblocktemplate({"capabilities": ["auxpow"]}) - Our modified daemon (multiaddress)
2. createauxblock(address) - Standard daemon with address
3. getauxblock() - Standard daemon with wallet

This adapter speaks method 1 to P2Pool but uses methods 2/3 to talk to standard daemons.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import web
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('mm-adapter')


class JsonRpcError(Exception):
    """JSON-RPC error from upstream daemon."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"JSON-RPC Error {code}: {message}")


class UpstreamRPC:
    """JSON-RPC client for the upstream daemon with persistent session."""
    
    def __init__(self, url: str, user: str, password: str, timeout: int = 30):
        self.url = url
        self.auth = aiohttp.BasicAuth(user, password)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._id = 0
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create persistent HTTP session (connection pooling)."""
        if self._session is None or self._session.closed:
            # keepalive_timeout=60 keeps TCP connection alive between calls
            conn = aiohttp.TCPConnector(limit=4, keepalive_timeout=60)
            self._session = aiohttp.ClientSession(
                auth=self.auth, timeout=self.timeout, connector=conn)
        return self._session
    
    async def call(self, method: str, *params) -> Any:
        """Make a JSON-RPC call via persistent session."""
        self._id += 1
        payload = {
            "jsonrpc": "1.0",
            "id": self._id,
            "method": method,
            "params": list(params)
        }
        
        log.debug(f"RPC -> {method}({params})")
        
        session = await self._get_session()
        async with session.post(self.url, json=payload) as resp:
            if resp.status == 401:
                raise JsonRpcError(-1, "Authentication failed")
            
            result = await resp.json()
            
            if result.get('error'):
                err = result['error']
                raise JsonRpcError(err.get('code', -1), err.get('message', 'Unknown error'))
            
            res = result.get('result')
            log.debug(f"RPC <- {method}: {str(res)[:100]}...")
            return res
    
    async def close(self):
        """Close the persistent session."""
        if self._session and not self._session.closed:
            await self._session.close()


class MergedMiningAdapter:
    """
    Translates P2Pool's merged mining RPC to standard daemon RPC.
    
    P2Pool expects:
    - getblocktemplate({"capabilities": ["auxpow"]}) → response with auxpow object
    
    Standard Dogecoin provides:
    - createauxblock(address) → {"hash": "...", "chainid": ..., "target": "..."}
    - getauxblock(hash, auxpow) → submits block
    
    Optimization: background poller pre-fetches work from dogecoind every 1s.
    P2Pool calls return cached data instantly (sub-millisecond).
    """
    
    def __init__(self, config: Dict):
        self.config = config
        upstream = config['upstream']
        self.rpc = UpstreamRPC(
            f"http://{upstream['host']}:{upstream['port']}/",
            upstream['rpc_user'],
            upstream['rpc_password'],
            upstream.get('timeout', 30)
        )
        
        # Payout address for merged mining rewards
        self.payout_address = config.get('payout', {}).get('address')
        if not self.payout_address:
            log.warning("No payout address configured, will use wallet default")
        
        # --- Cached state (updated by background poller) ---
        self.cached_tip_hash: Optional[str] = None      # getbestblockhash result
        self.cached_response: Optional[Dict] = None      # Full GBT response for P2Pool
        self.cached_response_time: float = 0              # When cached_response was last updated
        self.cache_lock = asyncio.Lock()
        
        # Track pending aux blocks for submission (keyed by aux hash)
        self.pending_aux_blocks: Dict[str, Dict] = {}
        
        # Background poller settings
        poll_cfg = config.get('polling', {})
        self.poll_interval: float = poll_cfg.get('interval', 1.0)  # seconds
        self._poller_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Stats
        self._polls = 0
        self._cache_hits = 0
        self._cache_misses = 0
    
    async def start_poller(self):
        """Start the background template poller."""
        self._running = True
        self._poller_task = asyncio.ensure_future(self._poll_loop())
        log.info(f"Background poller started (interval={self.poll_interval}s)")
    
    async def stop_poller(self):
        """Stop the background poller."""
        self._running = False
        if self._poller_task:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
    
    async def _poll_loop(self):
        """Background loop: poll dogecoind for new work every poll_interval seconds."""
        while self._running:
            try:
                await self._refresh_template()
                self._polls += 1
            except Exception as e:
                log.error(f"Poller error: {e}")
            
            await asyncio.sleep(self.poll_interval)
    
    async def _refresh_template(self):
        """Fetch fresh work from dogecoind and update cache.
        
        Optimization: first check getbestblockhash.  If the tip hasn't
        changed, skip the expensive createauxblock + getblocktemplate calls
        and keep the cached response (transactions update slowly on DOGE).
        Full refresh is forced every 5s for mempool freshness.
        """
        FULL_REFRESH_INTERVAL = 5.0  # seconds
        
        # Lightweight tip check
        try:
            tip_hash = await self.rpc.call('getbestblockhash')
        except Exception:
            tip_hash = None
        
        now = time.time()
        tip_changed = (tip_hash != self.cached_tip_hash)
        needs_full = (tip_changed
                      or self.cached_response is None
                      or now - self.cached_response_time >= FULL_REFRESH_INTERVAL)
        
        if not needs_full:
            return  # Tip unchanged, cache fresh enough — skip heavy work
        
        # Full refresh: fetch createauxblock + getblocktemplate in parallel
        try:
            if self.payout_address:
                auxblock_coro = self.rpc.call('createauxblock', self.payout_address)
            else:
                auxblock_coro = self.rpc.call('getauxblock')
            
            try:
                template_coro = self.rpc.call('getblocktemplate', {"rules": ["segwit"]})
                auxblock, template = await asyncio.gather(auxblock_coro, template_coro)
            except Exception:
                # Retry template without segwit rules
                if self.payout_address:
                    auxblock = await self.rpc.call('createauxblock', self.payout_address)
                else:
                    auxblock = await self.rpc.call('getauxblock')
                template = await self.rpc.call('getblocktemplate')
            
            # Store for later submission
            self.pending_aux_blocks[auxblock['hash']] = {
                'auxblock': auxblock,
                'use_submitauxblock': self.payout_address is not None
            }
            
            # Prune old pending entries (keep last 10)
            if len(self.pending_aux_blocks) > 10:
                keys = sorted(self.pending_aux_blocks.keys())
                for k in keys[:-10]:
                    del self.pending_aux_blocks[k]
            
            # Build response in format P2Pool expects
            chain_id = self.config.get('chain', {}).get('chain_id', 98)
            response = {
                'version': template.get('version', 1),
                'previousblockhash': template.get('previousblockhash'),
                'transactions': template.get('transactions', []),
                'coinbasevalue': template.get('coinbasevalue', 0),
                'target': auxblock.get('target', template.get('target')),
                'mintime': template.get('mintime'),
                'curtime': template.get('curtime'),
                'mutable': template.get('mutable', ['time', 'transactions', 'prevblock']),
                'height': template.get('height'),
                'bits': template.get('bits'),
                
                # The key auxpow object P2Pool expects
                'auxpow': {
                    'chainid': auxblock.get('chainid', chain_id),
                    'target': auxblock.get('target'),
                    'hash': auxblock['hash'],
                    'coinbasevalue': template.get('coinbasevalue', 0),
                }
            }
            
            async with self.cache_lock:
                old_tip = self.cached_tip_hash
                self.cached_tip_hash = tip_hash
                self.cached_response = response
                self.cached_response_time = now
            
            if tip_changed and old_tip is not None:
                log.info(f"[POLLER] NEW BLOCK height={template.get('height')} "
                         f"prev={template.get('previousblockhash', '')[:16]} "
                         f"txs={len(template.get('transactions', []))} "
                         f"fees={sum(tx.get('fee', 0) for tx in template.get('transactions', []))}")
            else:
                log.debug(f"[POLLER] Template refresh height={template.get('height')} "
                          f"txs={len(template.get('transactions', []))}")
            
        except Exception as e:
            log.error(f"[POLLER] Failed to refresh template: {e}")
            raise
    
    async def handle_getblocktemplate(self, params: List) -> Dict:
        """Return cached template (populated by background poller)."""
        capabilities = []
        if params and isinstance(params[0], dict):
            capabilities = params[0].get('capabilities', [])
        
        if 'auxpow' not in capabilities:
            return await self.rpc.call('getblocktemplate', *params)
        
        # Return cached response if available
        async with self.cache_lock:
            if self.cached_response is not None:
                self._cache_hits += 1
                age_ms = (time.time() - self.cached_response_time) * 1000
                log.debug(f"[CACHE HIT] Serving cached template (age={age_ms:.0f}ms, "
                          f"hits={self._cache_hits} misses={self._cache_misses})")
                return self.cached_response
        
        # Cache miss (first call before poller has run) — fetch synchronously
        self._cache_misses += 1
        log.info("[CACHE MISS] No cached template, fetching synchronously")
        await self._refresh_template()
        async with self.cache_lock:
            return self.cached_response
    
    async def handle_getbestblockhash(self) -> str:
        """Return cached tip hash (zero upstream cost)."""
        async with self.cache_lock:
            if self.cached_tip_hash is not None:
                return self.cached_tip_hash
        
        # Cache miss — fetch from daemon
        tip = await self.rpc.call('getbestblockhash')
        async with self.cache_lock:
            self.cached_tip_hash = tip
        return tip
    
    async def handle_submitauxblock(self, params: List) -> bool:
        """
        Handle submitauxblock call from P2Pool.
        
        P2Pool calls: submitauxblock(hash, auxpow_hex)
        We translate to: submitauxblock(hash, auxpow) or getauxblock(hash, auxpow)
        """
        if len(params) < 2:
            raise JsonRpcError(-1, "submitauxblock requires hash and auxpow parameters")
        
        aux_hash = params[0]
        auxpow_hex = params[1]
        
        log.info(f"Submitting aux block: hash={aux_hash[:16]}...")
        
        # Look up how we got this aux work
        pending = self.pending_aux_blocks.get(aux_hash)
        
        try:
            if pending and pending.get('use_submitauxblock'):
                result = await self.rpc.call('submitauxblock', aux_hash, auxpow_hex)
            else:
                result = await self.rpc.call('getauxblock', aux_hash, auxpow_hex)
            
            log.info(f"Aux block submitted successfully: {aux_hash[:16]}...")
            
            # Clean up pending
            if aux_hash in self.pending_aux_blocks:
                del self.pending_aux_blocks[aux_hash]
            
            # Force immediate template refresh after block submission
            asyncio.ensure_future(self._refresh_template())
            
            return result if result is not None else True
            
        except JsonRpcError as e:
            log.error(f"Aux block submission failed: {e}")
            raise
    
    async def handle_rpc_request(self, method: str, params: List) -> Any:
        """Route RPC requests to appropriate handlers."""
        
        log.debug(f"Handling RPC: {method}({params})")
        
        if method == 'getblocktemplate':
            return await self.handle_getblocktemplate(params)
        
        elif method == 'getbestblockhash':
            return await self.handle_getbestblockhash()
        
        elif method == 'submitauxblock':
            return await self.handle_submitauxblock(params)
        
        elif method == 'getauxblock':
            if len(params) == 0:
                auxblock = await self.rpc.call('getauxblock')
                self.pending_aux_blocks[auxblock['hash']] = {
                    'auxblock': auxblock,
                    'use_submitauxblock': False
                }
                return auxblock
            elif len(params) == 1:
                auxblock = await self.rpc.call('createauxblock', params[0])
                self.pending_aux_blocks[auxblock['hash']] = {
                    'auxblock': auxblock,
                    'use_submitauxblock': True
                }
                return auxblock
            else:
                return await self.rpc.call('getauxblock', params[0], params[1])
        
        elif method == 'createauxblock':
            auxblock = await self.rpc.call('createauxblock', *params)
            self.pending_aux_blocks[auxblock['hash']] = {
                'auxblock': auxblock,
                'use_submitauxblock': True
            }
            return auxblock
        
        elif method == 'getnetworkinfo':
            # Pass through for daemon warning checks
            return await self.rpc.call('getnetworkinfo')
        
        else:
            # Pass through any other methods
            return await self.rpc.call(method, *params)


class RPCServer:
    """JSON-RPC server that accepts connections from P2Pool."""
    
    def __init__(self, adapter: MergedMiningAdapter, config: Dict):
        self.adapter = adapter
        self.config = config
        self.server_config = config['server']
    
    def check_auth(self, request: web.Request) -> bool:
        """Verify Basic auth credentials."""
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Basic '):
            return False
        
        try:
            encoded = auth_header[6:]
            decoded = base64.b64decode(encoded).decode('utf-8')
            user, password = decoded.split(':', 1)
            return (user == self.server_config['rpc_user'] and 
                    password == self.server_config['rpc_password'])
        except:
            return False
    
    async def handle_request(self, request: web.Request) -> web.Response:
        """Handle incoming JSON-RPC request."""
        
        if not self.check_auth(request):
            return web.Response(status=401, text='Unauthorized')
        
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({
                'jsonrpc': '1.0',
                'id': None,
                'result': None,
                'error': {'code': -32700, 'message': 'Parse error'}
            })
        
        request_id = body.get('id')
        method = body.get('method', '')
        params = body.get('params', [])
        
        log.debug(f"RPC request: {method}")
        
        try:
            result = await self.adapter.handle_rpc_request(method, params)
            return web.json_response({
                'jsonrpc': '1.0',
                'id': request_id,
                'result': result,
                'error': None
            })
        except JsonRpcError as e:
            return web.json_response({
                'jsonrpc': '1.0',
                'id': request_id,
                'result': None,
                'error': {'code': e.code, 'message': e.message}
            })
        except Exception as e:
            log.exception(f"Error handling {method}")
            return web.json_response({
                'jsonrpc': '1.0',
                'id': request_id,
                'result': None,
                'error': {'code': -1, 'message': str(e)}
            })
    
    async def run(self):
        """Start the RPC server and background poller."""
        app = web.Application()
        app.router.add_post('/', self.handle_request)
        
        host = self.server_config['host']
        port = self.server_config['port']
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, host, port)
        await site.start()
        
        log.info(f"MM Adapter listening on {host}:{port}")
        log.info(f"Upstream daemon: {self.config['upstream']['host']}:{self.config['upstream']['port']}")
        
        # Start background poller
        await self.adapter.start_poller()
        
        # Keep running
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await self.adapter.stop_poller()
            await self.adapter.rpc.close()


def load_config(path: str) -> Dict:
    """Load configuration from YAML file."""
    with open(path, 'r') as f:
        return yaml.safe_load(f)


async def main():
    parser = argparse.ArgumentParser(description='Merged Mining RPC Adapter')
    parser.add_argument('--config', '-c', default='config.yaml', help='Config file path')
    parser.add_argument('--debug', '-d', action='store_true', help='Enable debug logging')
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    config = load_config(args.config)
    
    # Override logging level from config
    log_level = config.get('logging', {}).get('level', 'INFO')
    logging.getLogger().setLevel(getattr(logging, log_level))
    
    adapter = MergedMiningAdapter(config)
    server = RPCServer(adapter, config)
    
    await server.run()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down...")
