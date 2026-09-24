"""
T4 mempool fill/spike driver -- widens the coind (regtest) template so the
stratum-swarm harness (swarm_sim.py) actually exercises the O(users x tx)
get_work rebuild that the G1 DOA fix targets.

Why this exists
---------------
swarm_sim.py measures reactor stall + DOA% under N distinct connections. But the
reactor stall it hunts for only appears when generate_transaction has a WIDE tx
set to rebuild on every get_work. Against an ~empty regtest mempool the template
is a coinbase + a handful of txs, and an UNFIXED node looks as flat as a G1-fixed
one -- the benchmark proves nothing. This driver fills (and on demand spikes) the
regtest mempool to a target width so the fixed-vs-unfixed gap becomes visible and
the DOA%/reactor-stall numbers are the crossing-safety proof (#41 methodology).

Pairing
-------
  1. start regtest coind (litecoin/dogecoin/digibyte -regtest, txindex not needed)
  2. python mempool_fill.py --port <rpcport> --user u --password p \
         --target-bytes 4000000        # sustain a ~4 MB template
  3. in parallel: python swarm_sim.py --port <stratumport> --n 300 --seconds 120
  4. compare DOA%/inter-notify gap: unfixed tracks kr1z1s 10-12%, G1 stays ~1-2%.

REGTEST ONLY. It spends real coins and mints blocks with generatetoaddress; it
refuses to run unless the node reports the regtest chain.

Python2 / stdlib only (urllib2 + json) -- no p2pool package import, so it runs
even when the tree does not import, and matches swarm_sim.py being self-contained.
"""
import json, sys, time, base64, urllib2


class RPC(object):
    def __init__(self, host, port, user, password, timeout=30):
        self.url = "http://%s:%d/" % (host, port)
        tok = base64.b64encode("%s:%s" % (user, password)).strip()
        self.auth = "Basic %s" % tok
        self.timeout = timeout
        self._id = 0

    def __call__(self, method, *params):
        self._id += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self._id,
                           "method": method, "params": list(params)})
        req = urllib2.Request(self.url, body,
                              {"Authorization": self.auth,
                               "Content-Type": "application/json"})
        try:
            resp = urllib2.urlopen(req, timeout=self.timeout)
            payload = json.loads(resp.read())
        except urllib2.HTTPError as e:
            # coind returns the JSON-RPC error body with a non-2xx status
            try:
                payload = json.loads(e.read())
            except Exception:
                raise
        if payload.get("error"):
            raise RuntimeError("%s: %s" % (method, payload["error"]))
        return payload["result"]


def _require_regtest(rpc):
    info = rpc("getblockchaininfo")
    chain = info.get("chain")
    if chain != "regtest":
        sys.exit("REFUSING: node chain is %r, not regtest. This tool mints "
                 "blocks and spends coins; run it only against -regtest." % chain)
    return info


def _new_address(rpc):
    try:
        return rpc("getnewaddress")
    except RuntimeError:
        # some forks require an account arg on legacy wallets
        return rpc("getnewaddress", "")


def _ensure_spendable(rpc, want, addr):
    """Mature enough coinbase so we have balance to fan out. Regtest only."""
    bal = rpc("getbalance")
    if bal >= want:
        return bal
    # coinbase needs 100 confs to be spendable; mint a cushion.
    print("fill: balance %.4f < %.4f, minting blocks to mature coinbase" % (bal, want))
    minted = 0
    while rpc("getbalance") < want and minted < 5000:
        rpc("generatetoaddress", 20, addr)
        minted += 20
    bal = rpc("getbalance")
    print("fill: matured balance = %.4f (%d blocks minted)" % (bal, minted))
    return bal


def _mempool_bytes(rpc):
    mi = rpc("getmempoolinfo")
    # bytes on older forks, usage/bytes on newer; prefer serialized bytes
    return int(mi.get("bytes", mi.get("usage", 0))), int(mi.get("size", 0))


def _spray(rpc, addr, fanout, amount):
    """One sendmany fanning a small amount to `fanout` fresh outputs -> widens
    both the mempool tx and the future-spend UTXO set."""
    dests = {}
    for _ in range(fanout):
        dests[_new_address(rpc)] = round(amount, 8)
    return rpc("sendmany", "", dests)


def run(rpc, target_bytes, target_count, fanout, amount, spike, settle):
    _require_regtest(rpc)
    addr = _new_address(rpc)
    # need enough balance for a sustained fill; heuristic cushion.
    _ensure_spendable(rpc, max(50.0, fanout * amount * 4), addr)

    def report(tag):
        b, n = _mempool_bytes(rpc)
        print("mempool[%s]  txs=%-6d  bytes=%-9d (%.2f MB)"
              % (tag, n, b, b / 1e6))
        return b, n

    report("start")
    if spike:
        print("fill: SPIKE mode -- %d sendmany bursts of fanout=%d" % (spike, fanout))
        for i in range(spike):
            try:
                _spray(rpc, addr, fanout, amount)
            except RuntimeError as e:
                print("fill: spray stopped (%s) -- likely out of spendable UTXOs" % e)
                break
        report("post-spike")
        return

    print("fill: sustaining target bytes=%s count=%s (fanout=%d amount=%.8f)"
          % (target_bytes, target_count, fanout, amount))
    stalls = 0
    while True:
        b, n = _mempool_bytes(rpc)
        if target_bytes and b >= target_bytes:
            break
        if target_count and n >= target_count:
            break
        try:
            _spray(rpc, addr, fanout, amount)
            stalls = 0
        except RuntimeError as e:
            stalls += 1
            print("fill: spray failed (%s)" % e)
            if stalls >= 3:
                # out of UTXOs: mint a block to reset coinbase, keep going
                print("fill: minting 1 block to replenish spendable UTXOs")
                rpc("generatetoaddress", 1, addr)
                stalls = 0
            time.sleep(0.2)
        if settle:
            time.sleep(settle)
    report("target-reached")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="T4 regtest mempool fill/spike driver")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True, help="coind RPC port")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--target-bytes", type=int, default=2000000,
                    help="sustain mempool until serialized bytes >= this (0=off)")
    ap.add_argument("--target-count", type=int, default=0,
                    help="alternatively sustain until tx count >= this (0=off)")
    ap.add_argument("--fanout", type=int, default=100,
                    help="outputs per sendmany (tx width knob)")
    ap.add_argument("--amount", type=float, default=0.001,
                    help="amount per fanned output")
    ap.add_argument("--spike", type=int, default=0,
                    help="one-shot: emit N sendmany bursts then exit (spike test)")
    ap.add_argument("--settle-ms", type=float, default=0.0,
                    help="sleep between sprays (pace the fill)")
    a = ap.parse_args()
    rpc = RPC(a.host, a.port, a.user, a.password)
    run(rpc, a.target_bytes, a.target_count, a.fanout, a.amount,
        a.spike, a.settle_ms / 1e3)
