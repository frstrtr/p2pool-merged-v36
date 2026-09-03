import random
import sys
import time

from twisted.internet import defer, error, reactor
from twisted.python import log

from p2pool import data as p2pool_data, p2p
from p2pool.bitcoin import data as bitcoin_data, helper, height_tracker
from p2pool.util import deferral, variable


class P2PNode(p2p.Node):
    # v36-0.24 convergence: how long a per-peer failure to serve a given share
    # hash is remembered. Within this window that peer is skipped for THAT hash
    # (we fail over to other peers), and a head is "abandoned" (reapable) only
    # once EVERY connected peer has failed its missing parent inside the window.
    # Long enough to route around a black-hole/slow peer across several download
    # attempts; short enough that a transiently-busy honest peer is retried and
    # a parent that becomes available again is re-fetched. ~6*SHARE_PERIOD(15s).
    _fetch_failure_ttl = 90.0

    def __init__(self, node, **kwargs):
        self.node = node
        # share_hash -> {peer_key: last_failure_timestamp}
        self._share_fetch_failures = {}
        p2p.Node.__init__(self,
            best_share_hash_func=lambda: node.best_share_var.value,
            net=node.net,
            known_txs_var=node.known_txs_var,
            mining_txs_var=node.mining_txs_var,   # transactions from getblocktemplate
            mining2_txs_var=node.mining2_txs_var, # transactions sent to miners
        **kwargs)

    # --- v36-0.24 parent-fetch failover: no single peer can wedge convergence --
    @staticmethod
    def _peer_key(peer):
        return getattr(peer, 'addr', None) or id(peer)

    def _prune_fetch_failures(self, now):
        for h in list(self._share_fetch_failures):
            d = self._share_fetch_failures[h]
            for k in list(d):
                if now - d[k] > self._fetch_failure_ttl:
                    del d[k]
            if not d:
                del self._share_fetch_failures[h]

    def _record_fetch_failure(self, share_hash, peer):
        self._share_fetch_failures.setdefault(share_hash, {})[self._peer_key(peer)] = time.time()

    def _failed_peer_keys(self, share_hash, now):
        d = self._share_fetch_failures.get(share_hash)
        if not d:
            return set()
        return set(k for k, ts in d.iteritems() if now - ts <= self._fetch_failure_ttl)

    def _choose_download_peer(self, share_hash, advertised_addr):
        '''Pick a connected peer to request share_hash from, EXCLUDING peers that
        have failed to serve this exact hash within _fetch_failure_ttl. Prefer the
        peer that advertised the hash if it is still eligible. Returns None iff
        every connected peer has recently failed this hash -- the caller then
        backs off and lets the failures age out. This is the attack-vector fix:
        a single black-hole/slow/malicious peer can never wedge a parent fetch,
        because the node always fails over to another peer that may have it.'''
        peers = list(self.peers.values())
        if not peers:
            return None
        now = time.time()
        failed = self._failed_peer_keys(share_hash, now)
        eligible = [p for p in peers if self._peer_key(p) not in failed]
        if not eligible:
            return None
        if advertised_addr is not None:
            for p in eligible:
                if getattr(p, 'addr', None) == advertised_addr:
                    return p
        return random.choice(eligible)

    def _parent_abandoned(self, share_hash):
        '''True iff there ARE connected peers and every one has recently failed to
        serve share_hash -- the parent is unfetchable from the whole peer set, so
        a head waiting on it may be reaped (memory stays bounded, and a peer that
        merely re-advertises an unservable fragment cannot pin the tracker). With
        no peers, or any peer not yet failed, returns False: the download is still
        viable, so clean_tracker keeps protecting the head.'''
        peers = list(self.peers.values())
        if not peers:
            return False
        now = time.time()
        failed = self._failed_peer_keys(share_hash, now)
        return all(self._peer_key(p) in failed for p in peers)

    def handle_shares(self, shares, peer):
        if len(shares) > 5:
            print 'Processing %i shares from %s...' % (len(shares), '%s:%i' % peer.addr if peer is not None else None)
        
        new_count = 0
        all_new_txs = {}
        for share, new_txs in shares:
            if new_txs is not None:
                all_new_txs.update((bitcoin_data.hash256(bitcoin_data.tx_type.pack(new_tx)), new_tx) for new_tx in new_txs)
            
            if share.hash in self.node.tracker.items:
                #print 'Got duplicate share, ignoring. Hash: %s' % (p2pool_data.format_hash(share.hash),)
                continue
            
            new_count += 1
            
            #print 'Received share %s from %r' % (p2pool_data.format_hash(share.hash), share.peer_addr)
            
            self.node.tracker.add(share)
        
        self.node.known_txs_var.add(all_new_txs)
        
        if new_count:
            self.node.set_best_share()
        
        if len(shares) > 5:
            print '... done processing %i shares. New: %i Have: %i/~%i' % (len(shares), new_count, len(self.node.tracker.items), 2*self.node.net.CHAIN_LENGTH)
    
    @defer.inlineCallbacks
    def handle_share_hashes(self, hashes, peer):
        new_hashes = [x for x in hashes if x not in self.node.tracker.items]
        if not new_hashes:
            return
        try:
            shares = yield peer.get_shares(
                hashes=new_hashes,
                parents=0,
                stops=[],
            )
        except (defer.TimeoutError, error.ConnectionLost, error.ConnectionDone, error.ConnectError):
            pass
        except:
            log.err(None, 'in handle_share_hashes:')
            peer.badPeerHappened(30)
        else:
            self.handle_shares([(share, []) for share in shares], peer)
    
    def handle_get_shares(self, hashes, parents, stops, peer):
        parents = min(parents, 1000//len(hashes))
        stops = set(stops)
        shares = []
        for share_hash in hashes:
            for share in self.node.tracker.get_chain(share_hash, min(parents + 1, self.node.tracker.get_height(share_hash))):
                if share.hash in stops:
                    break
                shares.append(share)
        if len(shares) > 0:
            print 'Sending %i shares to %s:%i' % (len(shares), peer.addr[0], peer.addr[1])
        return shares
    
    def handle_bestblock(self, header, peer):
        if self.node.net.PARENT.POW_FUNC(bitcoin_data.block_header_type.pack(header)) > header['bits'].target:
            raise p2p.PeerMisbehavingError('received block header fails PoW test')
        self.node.handle_header(header)
    
    def broadcast_share(self, share_hash):
        shares = []
        for share in self.node.tracker.get_chain(share_hash, min(5, self.node.tracker.get_height(share_hash))):
            if share.hash in self.shared_share_hashes:
                break
            self.shared_share_hashes.add(share.hash)
            shares.append(share)
        
        for peer in self.peers.itervalues():
            peer.sendShares([share for share in shares if share.peer_addr != peer.addr], self.node.tracker, self.node.known_txs_var.value, include_txs_with=[share_hash])
    
    def start(self):
        p2p.Node.start(self)
        
        self.shared_share_hashes = set(self.node.tracker.items)
        self.node.tracker.removed.watch_weakref(self, lambda self, share: self.shared_share_hashes.discard(share.hash))
        
        @apply
        @defer.inlineCallbacks
        def download_shares():
            while True:
                desired = yield self.node.desired_var.get_when_satisfies(lambda val: len(val) != 0)
                peer_addr, share_hash = random.choice(desired)

                if len(self.peers) == 0:
                    yield deferral.sleep(1)
                    continue
                # v36-0.24: fail over across peers instead of picking a fully random
                # one. Skip peers that have failed to serve THIS hash within the TTL
                # and prefer the peer that advertised it. If every peer has recently
                # failed this exact hash, back off briefly and let the loop pick a
                # DIFFERENT desired parent -- a single black-hole/slow/malicious peer
                # can no longer trap us re-requesting one unservable parent forever
                # (the kr1z1s wall-parent loop: one hash requested 247x in 40 min).
                self._prune_fetch_failures(time.time())
                peer = self._choose_download_peer(share_hash, peer_addr)
                if peer is None:
                    yield deferral.sleep(1)
                    continue

                print 'Requesting parent share %s from %s' % (p2pool_data.format_hash(share_hash), '%s:%i' % peer.addr)
                try:
                    shares = yield peer.get_shares(
                        hashes=[share_hash],
                        parents=random.randrange(500), # randomize parents so that we eventually get past a too large block of shares
                        stops=list(set(self.node.tracker.heads) | set(
                            self.node.tracker.get_nth_parent_hash(head, min(max(0, self.node.tracker.get_height_and_last(head)[0] - 1), 10)) for head in self.node.tracker.heads
                        ))[:100],
                    )
                except defer.TimeoutError:
                    print 'Share request timed out!'
                    self._record_fetch_failure(share_hash, peer)
                    continue
                except (error.ConnectionLost, error.ConnectionDone, error.ConnectError):
                    print 'Lost connection to %s:%i during share download' % peer.addr
                    self._record_fetch_failure(share_hash, peer)
                    continue
                except:
                    log.err(None, 'in download_shares:')
                    self._record_fetch_failure(share_hash, peer)
                    peer.badPeerHappened(30)
                    continue

                if not shares:
                    # this peer does not have the requested parent -- remember that
                    # so we ask a DIFFERENT peer next time, and only abandon the head
                    # once the whole peer set has failed it (see _parent_abandoned).
                    self._record_fetch_failure(share_hash, peer)
                    yield deferral.sleep(1) # sleep so we don't keep rerequesting the same share nobody has
                    continue
                # got shares -> this parent is fetchable again; clear its failure memory
                self._share_fetch_failures.pop(share_hash, None)
                self.handle_shares([(share, []) for share in shares], peer)
        
        
        @self.node.best_block_header.changed.watch
        def _(header):
            for peer in self.peers.itervalues():
                peer.send_bestblock(header=header)
        
        # send share when the chain changes to their chain
        self.node.best_share_var.changed.watch(self.broadcast_share)
        
        @self.node.tracker.verified.added.watch
        def _(share):
            if not (share.pow_hash <= share.header['bits'].target):
                return
            
            def spread():
                if (self.node.get_height_rel_highest(share.header['previous_block']) > -5 or
                    self.node.bitcoind_work.value['previous_block'] in [share.header['previous_block'], share.header_hash]):
                    self.broadcast_share(share.hash)
            spread()
            reactor.callLater(5, spread) # so get_height_rel_highest can update
        

class Node(object):
    def __init__(self, factory, bitcoind, shares, known_verified_share_hashes, net):
        self.factory = factory
        self.bitcoind = bitcoind
        self.net = net
        self.cur_share_ver = None
        
        # Block broadcaster for parallel propagation (set externally)
        self.broadcaster = None
        # Merged mining broadcasters: {chainid: MergedMiningBroadcaster}
        self.merged_broadcasters = {}

        self.known_txs_var = variable.VariableDict({}) # hash -> tx
        self.mining_txs_var = variable.Variable({}) # hash -> tx
        self.mining2_txs_var = variable.Variable({}) # hash -> tx
        self.best_share_var = variable.Variable(None)
        self.desired_var = variable.Variable(None)
        self.punish = False

        self.tracker = p2pool_data.OkayTracker(self.net)
        

        for share in shares:
            self.tracker.add(share)
        
        for share_hash in known_verified_share_hashes:
            if share_hash in self.tracker.items:
                self.tracker.verified.add(self.tracker.items[share_hash])
        
        self.p2p_node = None # overwritten externally

    def check_and_purge_txs(self):
        if self.cur_share_ver < 34:
            return
        best_share = self.tracker.items.get(
                self.best_share_var.value, None)
        if not best_share:
            return
        prev_block = best_share.header['previous_block']
        if prev_block != self.bitcoind_work.value['previous_block']:
            self.known_txs_var.set({})

    @defer.inlineCallbacks
    def start(self):
        stop_signal = variable.Event()
        self.stop = stop_signal.happened
        
        # BITCOIND WORK
        
        self.bitcoind_work = variable.Variable((yield helper.getwork(self.bitcoind, self.net)))

        @defer.inlineCallbacks
        def work_poller():
            while stop_signal.times == 0:
                flag = self.factory.new_block.get_deferred()
                try:
                    self.bitcoind_work.set((yield helper.getwork(self.bitcoind, self.net, self.bitcoind_work.value['use_getblocktemplate'])))
                    self.check_and_purge_txs()
                except:
                    log.err()
                yield defer.DeferredList([flag, deferral.sleep(15)], fireOnOneCallback=True)
        work_poller()
        
        # PEER WORK
        
        self.best_block_header = variable.Variable(None)
        def handle_header(new_header):
            # check that header matches current target
            if not (self.net.PARENT.POW_FUNC(bitcoin_data.block_header_type.pack(new_header)) <= self.bitcoind_work.value['bits'].target):
                return
            bitcoind_best_block = self.bitcoind_work.value['previous_block']
            if (self.best_block_header.value is None
                or (
                    new_header['previous_block'] == bitcoind_best_block and
                    bitcoin_data.hash256(bitcoin_data.block_header_type.pack(self.best_block_header.value)) == bitcoind_best_block
                ) # new is child of current and previous is current
                or (
                    bitcoin_data.hash256(bitcoin_data.block_header_type.pack(new_header)) == bitcoind_best_block and
                    self.best_block_header.value['previous_block'] != bitcoind_best_block
                )): # new is current and previous is not a child of current
                self.best_block_header.set(new_header)
        self.handle_header = handle_header

        @defer.inlineCallbacks
        def poll_header():
            if self.factory.conn.value is None:
                return
            handle_header((yield self.factory.conn.value.get_block_header(self.bitcoind_work.value['previous_block'])))
        self.bitcoind_work.changed.watch(lambda _: poll_header())
        yield deferral.retry('Error while requesting best block header:')(poll_header)()
        
        # BEST SHARE
        
        self.get_height_rel_highest, self.get_height = yield height_tracker.get_height_funcs(self.bitcoind, self.factory, lambda: self.bitcoind_work.value['previous_block'], self.net)
        self.bitcoind_work.changed.watch(lambda _: self.set_best_share())
        self.set_best_share()
        
        # setup p2p logic and join p2pool network
        
        # update mining_txs according to getwork results
        @self.bitcoind_work.changed.run_and_watch
        def _(_=None):
            new_mining_txs = dict(zip(self.bitcoind_work.value['transaction_hashes'], self.bitcoind_work.value['transactions']))
            added_known_txs = {hsh:tx for hsh,tx in new_mining_txs.iteritems() if not hsh in self.known_txs_var.value}
            self.mining_txs_var.set(new_mining_txs)
            self.known_txs_var.add(added_known_txs)
        # add p2p transactions from bitcoind to known_txs
        @self.factory.new_tx.watch
        def _(tx):
            self.known_txs_var.add({
                bitcoin_data.hash256(bitcoin_data.tx_type.pack(tx)): tx,
            })

        if self.cur_share_ver < 34:
            # forward transactions seen to bitcoind
            @self.known_txs_var.transitioned.watch
            @defer.inlineCallbacks
            def _(before, after):
                yield deferral.sleep(random.expovariate(1/1))
                if self.factory.conn.value is None:
                    return
                for tx_hash in set(after) - set(before):
                    self.factory.conn.value.send_tx(tx=after[tx_hash])
        
        @self.tracker.verified.added.watch
        def _(share):
            if not (share.pow_hash <= share.header['bits'].target):
                return
            
            if share.VERSION >= 34:
                print 'GOT BLOCK FROM PEER! %s %s%064x' % (
                        p2pool_data.format_hash(share.hash),
                        self.net.PARENT.BLOCK_EXPLORER_URL_PREFIX,
                        share.header_hash)
                return
            block = share.as_block(self.tracker, self.known_txs_var.value)
            if block is None:
                print >>sys.stderr, 'GOT INCOMPLETE BLOCK FROM PEER! %s bitcoin: %s%064x' % (p2pool_data.format_hash(share.hash), self.net.PARENT.BLOCK_EXPLORER_URL_PREFIX, share.header_hash)
                return
            helper.submit_block(block, True, self)
            print
            print 'GOT BLOCK FROM PEER! Passing to bitcoind! %s bitcoin: %s%064x' % (p2pool_data.format_hash(share.hash), self.net.PARENT.BLOCK_EXPLORER_URL_PREFIX, share.header_hash)
            print

        def forget_old_txs():
            new_known_txs = {}
            if self.p2p_node is not None:
                for peer in self.p2p_node.peers.itervalues():
                    new_known_txs.update(peer.remembered_txs)
            new_known_txs.update(self.mining_txs_var.value)
            new_known_txs.update(self.mining2_txs_var.value)
            for share in self.tracker.get_chain(self.best_share_var.value, min(120, self.tracker.get_height(self.best_share_var.value))):
                if share.VERSION >= 34:
                    continue
                for tx_hash in share.new_transaction_hashes:
                    if tx_hash in self.known_txs_var.value:
                        new_known_txs[tx_hash] = self.known_txs_var.value[tx_hash]
            self.known_txs_var.set(new_known_txs)
        if self.cur_share_ver < 34:
            t = deferral.RobustLoopingCall(forget_old_txs)
            t.start(10)
            stop_signal.watch(t.stop)
        
        t = deferral.RobustLoopingCall(self.clean_tracker)
        t.start(5)
        stop_signal.watch(t.stop)
    
    def set_best_share(self):
        oldpunish = self.punish
        best, desired, decorated_heads, bad_peer_addresses, self.punish= self.tracker.think(self.get_height_rel_highest, self.get_height, self.bitcoind_work.value['previous_block'], self.bitcoind_work.value['bits'], self.known_txs_var.value)
        if self.punish and not oldpunish and best == self.best_share_var.value: # need to reissue work with lower difficulty
            self.best_share_var.changed.happened(best) # triggers wb.new_work_event to reissue work

        self.best_share_var.set(best)
        self.desired_var.set(desired)
        try:
            self.cur_share_ver = self.tracker.items[best].VERSION
        except KeyError:
            self.cur_share_ver = p2pool_data.BaseShare.VERSION
        if self.p2p_node is not None:
            for bad_peer_address in bad_peer_addresses:
                # XXX O(n)
                for peer in self.p2p_node.peers.itervalues():
                    if peer.addr == bad_peer_address:
                        peer.badPeerHappened()
                        break
    
    def get_current_txouts(self):
        return p2pool_data.get_expected_payouts(self.tracker, self.best_share_var.value, self.bitcoind_work.value['bits'].target, self.bitcoind_work.value['subsidy'], self.net)
    
    def _desired_parent_abandoned(self, parent_hash):
        '''True iff every connected peer has recently failed to serve parent_hash,
        so a head still requesting it may be reaped. p2p_node absent / no peers /
        any peer not-yet-failed -> False (the download is still viable, keep the
        head). Never raises: any error degrades to "not abandoned" (protect).'''
        p2p = self.p2p_node
        if p2p is None:
            return False
        try:
            return p2p._parent_abandoned(parent_hash)
        except Exception:
            return False

    def clean_tracker(self):
        best, desired, decorated_heads, bad_peer_addresses, self.punish = self.tracker.think(self.get_height_rel_highest, self.get_height, self.bitcoind_work.value['previous_block'], self.bitcoind_work.value['bits'], self.known_txs_var.value)

        # v36-0.24 convergence: the parents think() still wants (its `desired`
        # set). A head whose missing parent is here is one the node is ACTIVELY
        # downloading -- it must not be purged just because a peer is slow, or the
        # partial chain gets thrown away and re-downloaded from scratch forever
        # (the kr1z1s 12,929-request relapse loop, which the self-lapsing 300s
        # frontier timer below could not stop). The protection is bounded by
        # _desired_parent_abandoned: once EVERY connected peer has failed to serve
        # the parent it is unfetchable and the head becomes reapable, so a
        # malicious peer re-advertising an unservable fragment cannot pin memory.
        desired_parents = set(d[1] for d in desired)

        # eat away at heads
        if decorated_heads:
            top5 = set(head_hash for score, head_hash in decorated_heads[-5:])
            for i in xrange(1000):
                to_remove = set()
                for share_hash, tail in self.tracker.heads.iteritems():
                    if share_hash in top5:
                        #print 1
                        continue
                    if self.tracker.items[share_hash].time_seen > time.time() - 300:
                        #print 2
                        continue
                    # PRIMARY protection: the node still wants this head's missing
                    # parent and the peer set has not collectively given up on it.
                    if tail in desired_parents and not self._desired_parent_abandoned(tail):
                        continue
                    # v36 convergence fix: protect any head whose FRONTIER (the
                    # oldest-downloaded shares, reverse[tail]) received a share in
                    # the last 300s -- i.e. a chain that is actively being
                    # downloaded/extended toward a common ancestor. On master this
                    # protection (originally 120s) was gated on
                    # 'share_hash not in verified.items', so the instant the
                    # incremental verifier (data.py think, budgeted) verified the
                    # head it LOST protection and was purged mid-catch-up -- the
                    # foreign majority chain could never finish syncing and the
                    # minority node re-downloaded the same shares forever (kr1z1s:
                    # spb re-downloaded 505,935 shares in 76 min without adopting).
                    # We protect the head regardless of its verification state; the
                    # window still self-lapses 300s after download activity stops,
                    # so genuinely dead chains are still reaped.
                    reverse_tail = self.tracker.reverse.get(tail)
                    if reverse_tail and max(self.tracker.items[after_tail_hash].time_seen for after_tail_hash in reverse_tail) > time.time() - 300:
                        #print 3
                        continue
                    to_remove.add(share_hash)
                if not to_remove:
                    break
                for share_hash in to_remove:
                    if share_hash in self.tracker.verified.items:
                        self.tracker.verified.remove(share_hash)
                    self.tracker.remove(share_hash)
                #print "_________", to_remove
        
        # drop tails
        for i in xrange(1000):
            to_remove = set()
            for tail, heads in self.tracker.tails.iteritems():
                if min(self.tracker.get_height(head) for head in heads) < 2*self.tracker.net.CHAIN_LENGTH + 10:
                    continue
                to_remove.update(self.tracker.reverse.get(tail, set()))
            if not to_remove:
                break
            # if removed from this, it must be removed from verified
            #start = time.time()
            for aftertail in to_remove:
                if self.tracker.items[aftertail].previous_hash not in self.tracker.tails:
                    print "erk", aftertail, self.tracker.items[aftertail].previous_hash
                    continue
                if aftertail in self.tracker.verified.items:
                    self.tracker.verified.remove(aftertail)
                self.tracker.remove(aftertail)
            #end = time.time()
            #print "removed! %i %f" % (len(to_remove), (end - start)/len(to_remove))
        
        self.set_best_share()
