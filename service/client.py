"""
P2Pool Service Client

Lightweight client for p2pool nodes to interact with the hosted p2pool-service:
  - Periodic node announcement to the registry
  - Bootstrap peer discovery from the registry
  - Optional RPC relay (when no local daemon available)

Usage from p2pool main.py:
    from service.client import ServiceClient
    client = ServiceClient(service_url='https://p2pool-service.example.com')
    client.start(reactor, node_info)  # Start periodic announcements

This module uses Twisted (matching p2pool's event loop), not asyncio.
"""

import json
import logging
import time

try:
    from twisted.internet import reactor, task
    from twisted.web.client import Agent, readBody
    from twisted.web.http_headers import Headers
    from twisted.internet.defer import inlineCallbacks, returnValue
    from twisted.web.client import FileBodyProducer
    from io import BytesIO
    HAS_TWISTED = True
except ImportError:
    HAS_TWISTED = False

log = logging.getLogger('p2pool.service_client')


class ServiceClient(object):
    """Client for p2pool-service registry + RPC proxy."""

    def __init__(self, service_url, api_key=None, chain='ltc'):
        self.service_url = service_url.rstrip('/')
        self.api_key = api_key or ''
        self.chain = chain
        self._agent = None
        self._announce_loop = None
        self._node_info = {}

    def start(self, reactor_ref, node_info, announce_interval=300):
        """
        Begin periodic announcements and peer discovery.

        Args:
            reactor_ref: Twisted reactor
            node_info: Dict with keys: host, p2pool_port, web_port, version,
                       protocol_version, hashrate, miners, uptime, merged_chains
            announce_interval: Seconds between announcements
        """
        if not HAS_TWISTED:
            log.warning('Twisted not available, service client disabled')
            return

        self._agent = Agent(reactor_ref)
        self._node_info = node_info

        # Start announcement loop
        self._announce_loop = task.LoopingCall(self._do_announce)
        self._announce_loop.start(announce_interval, now=True)
        log.info('Service client started — registry: %s, chain: %s',
                 self.service_url, self.chain)

    def stop(self):
        """Stop announcement loop."""
        if self._announce_loop and self._announce_loop.running:
            self._announce_loop.stop()
        log.info('Service client stopped')

    @inlineCallbacks
    def _do_announce(self):
        """Announce this node to the registry."""
        try:
            url = '%s/registry/announce' % self.service_url
            payload = dict(self._node_info)
            payload['chain'] = self.chain

            body = json.dumps(payload).encode('utf-8')
            headers = Headers({
                b'Content-Type': [b'application/json'],
            })
            if self.api_key:
                headers.addRawHeader(b'X-API-Key', self.api_key.encode())

            response = yield self._agent.request(
                b'POST', url.encode('utf-8'), headers,
                FileBodyProducer(BytesIO(body))
            )
            resp_body = yield readBody(response)
            data = json.loads(resp_body)

            if response.code == 200 and data.get('ok'):
                log.debug('Registry announce OK — node_id=%s, ttl=%s',
                          data.get('node_id'), data.get('ttl'))
            else:
                log.warning('Registry announce failed: %s', data)

        except Exception as e:
            log.debug('Registry announce error: %s', e)

    @inlineCallbacks
    def get_peers(self, chain=None):
        """
        Fetch peer list from registry.

        Returns list of (host, port) tuples for p2pool peer connections.
        """
        if not self._agent:
            returnValue([])

        chain = chain or self.chain
        url = '%s/registry/nodes/%s' % (self.service_url, chain)

        try:
            response = yield self._agent.request(
                b'GET', url.encode('utf-8'), Headers({}), None
            )
            body = yield readBody(response)
            data = json.loads(body)

            peers = []
            for node in data.get('nodes', []):
                if not node.get('stale'):
                    peers.append((
                        str(node['host']),
                        int(node['p2pool_port'])
                    ))
            returnValue(peers)

        except Exception as e:
            log.debug('Registry peer fetch error: %s', e)
            returnValue([])

    @inlineCallbacks
    def rpc_call(self, chain, method, params=None):
        """
        Make an RPC call through the proxy service.

        Returns the JSON-RPC result dict or raises on error.
        """
        if not self._agent:
            raise RuntimeError('Service client not started')

        url = '%s/rpc/%s' % (self.service_url, chain)
        payload = {
            'jsonrpc': '1.0',
            'id': 1,
            'method': method,
            'params': params or [],
        }

        body = json.dumps(payload).encode('utf-8')
        headers = Headers({
            b'Content-Type': [b'application/json'],
        })
        if self.api_key:
            headers.addRawHeader(b'X-API-Key', self.api_key.encode())

        response = yield self._agent.request(
            b'POST', url.encode('utf-8'), headers,
            FileBodyProducer(BytesIO(body))
        )
        resp_body = yield readBody(response)
        data = json.loads(resp_body)

        if data.get('error'):
            raise RuntimeError('RPC error: %s' % data['error'])

        returnValue(data)
