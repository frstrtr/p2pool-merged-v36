#!/bin/bash
# Start P2Pool for Bitcoin Testnet
# This connects to the Bitcoin Core testnet node running on 192.168.80.182

cd "$(dirname "$0")"

python2 run_p2pool.py \
    --net bitcoin_testnet \
    --bitcoind-address 192.168.80.182 \
    --bitcoind-rpc-port 18332 \
    --bitcoind-rpc-username bitcoinrpc \
    --bitcoind-rpc-password 1a804315d3bcd2d163dd0f4102f8c4547b874f8197b798c2b1848ed754ed74f8 \
    --worker-port 19332 \
    --p2pool-port 19333 \
    --give-author 0 \
    --max-conns 40 \
    "$@"
