#!/bin/bash
# ============================================================================
#  P2Pool Multi-Chain Startup: DigiByte + Litecoin + DOGE Merged Mining
# ============================================================================
#
# Starts two p2pool instances (DGB + LTC), each with DOGE merged mining,
# and optionally the multi-pool dashboard proxy.
#
# Architecture:
#   ┌──────────────────────────────────┐
#   │    Multi-Pool Dashboard (:8080)  │ ← Optional unified web UI
#   └────────────┬─────────────────────┘
#                │
#        ┌───────┴────────┐
#        ▼                ▼
#   ┌─────────┐     ┌─────────┐
#   │ P2Pool  │     │ P2Pool  │
#   │  DGB    │     │  LTC    │
#   │ :5025   │     │ :9327   │
#   └────┬────┘     └────┬────┘
#        │               │
#   ┌────▼────┐     ┌────▼────┐
#   │DGB Core │     │LTC Core │
#   │ :14022  │     │ :9332   │
#   └─────────┘     └─────────┘
#        │               │
#   mm-adapter       mm-adapter
#   :44557           :44556
#        │               │
#   ┌────▼────┐     ┌────▼────┐
#   │DOGE Core│     │DOGE Core│
#   │ :22555  │     │ :22555  │
#   └─────────┘     └─────────┘
#
# Usage:
#   ./start_multichain.sh [--no-proxy] [--dgb-only] [--ltc-only]
#
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
P2POOL_DIR="$(dirname "$SCRIPT_DIR")"

# PyPy for p2pool (Python 2.7)
PYPY_PATH="$HOME/pypy2.7-v7.3.20-linux64/bin"
export PATH="$PYPY_PATH:$PATH"

# ── Configuration ─────────────────────────────────────────────────────────
# DGB P2Pool
DGB_ADDRESS="dgb1qdw25jdsnt56q6r90qx7esyqzd7gefr0jlm4mm9"
DGB_RPC_HOST="192.168.86.24"
DGB_RPC_PORT="14022"
DGB_P2P_PORT="12024"
DGB_RPC_USER="dgbrpc"
DGB_RPC_PASS="08ddf2a537609f297d4dd0ab612fd45f"
DGB_WORKER_PORT="5025"
DGB_P2POOL_PORT="5024"

# LTC P2Pool
LTC_ADDRESS="YOUR_LTC_ADDRESS"      # <── SET THIS
LTC_RPC_HOST="YOUR_LTC_NODE_IP"     # <── SET THIS  
LTC_RPC_PORT="9332"
LTC_P2P_PORT="9333"
LTC_RPC_USER="litecoinrpc"
LTC_RPC_PASS="YOUR_LTC_RPC_PASS"    # <── SET THIS
LTC_WORKER_PORT="9327"
LTC_P2POOL_PORT="9338"

# DOGE (shared by both chains via mm-adapter)
DOGE_RPC_HOST="192.168.86.27"
DOGE_RPC_PORT="22555"
DOGE_P2P_HOST="192.168.86.27"
DOGE_P2P_PORT="22556"

# MM-Adapter for DGB
DGB_ADAPTER_HOST="127.0.0.1"
DGB_ADAPTER_PORT="44557"
DGB_ADAPTER_USER="dogecoinrpc"
DGB_ADAPTER_PASS="dogecoinrpc_dgb_merged_2026"

# MM-Adapter for LTC
LTC_ADAPTER_HOST="127.0.0.1"
LTC_ADAPTER_PORT="44556"
LTC_ADAPTER_USER="dogecoinrpc"
LTC_ADAPTER_PASS="dogecoinrpc_ltc_merged_2026"

# Dashboard proxy
PROXY_PORT="8080"

# ── Parse arguments ───────────────────────────────────────────────────────
START_DGB=true
START_LTC=true
START_PROXY=true

for arg in "$@"; do
    case "$arg" in
        --no-proxy)   START_PROXY=false ;;
        --dgb-only)   START_LTC=false ;;
        --ltc-only)   START_DGB=false ;;
        --help|-h)
            echo "Usage: $0 [--no-proxy] [--dgb-only] [--ltc-only]"
            echo ""
            echo "Options:"
            echo "  --no-proxy   Don't start the multi-pool dashboard proxy"
            echo "  --dgb-only   Only start the DGB p2pool instance"
            echo "  --ltc-only   Only start the LTC p2pool instance"
            exit 0
            ;;
    esac
done

echo "============================================================"
echo "  P2Pool Multi-Chain Startup"
echo "============================================================"
echo ""

# ── Start DGB P2Pool ──────────────────────────────────────────────────────
if $START_DGB; then
    echo ">>> Starting DGB p2pool on port $DGB_WORKER_PORT..."
    
    # Kill existing DGB instance
    pkill -f "run_p2pool.*digibyte" 2>/dev/null || true
    sleep 1
    
    cd "$P2POOL_DIR"
    nohup pypy run_p2pool.py \
        --net digibyte \
        --bitcoind-address "$DGB_RPC_HOST" \
        --bitcoind-rpc-port "$DGB_RPC_PORT" \
        --bitcoind-p2p-port "$DGB_P2P_PORT" \
        -a "$DGB_ADDRESS" \
        -w "$DGB_WORKER_PORT" \
        --p2pool-port "$DGB_P2POOL_PORT" \
        --merged-coind-address "$DGB_ADAPTER_HOST" \
        --merged-coind-rpc-port "$DGB_ADAPTER_PORT" \
        --merged-coind-rpc-user "$DGB_ADAPTER_USER" \
        --merged-coind-rpc-password "$DGB_ADAPTER_PASS" \
        --merged-coind-p2p-port "$DOGE_P2P_PORT" \
        --merged-coind-p2p-address "$DOGE_P2P_HOST" \
        --disable-upnp \
        "$DGB_RPC_USER" "$DGB_RPC_PASS" \
        > /tmp/dgb_p2pool.log 2>&1 &
    
    DGB_PID=$!
    echo "    DGB p2pool started (PID: $DGB_PID)"
    echo "    Web:     http://localhost:$DGB_WORKER_PORT/static/"
    echo "    Stratum: stratum+tcp://localhost:$DGB_WORKER_PORT"
    echo "    Log:     /tmp/dgb_p2pool.log"
    echo ""
fi

# ── Start LTC P2Pool ──────────────────────────────────────────────────────
if $START_LTC; then
    if [ "$LTC_ADDRESS" = "YOUR_LTC_ADDRESS" ]; then
        echo ">>> SKIPPING LTC p2pool: LTC_ADDRESS not configured!"
        echo "    Edit this script and set LTC_ADDRESS, LTC_RPC_HOST, LTC_RPC_PASS"
        echo ""
        START_LTC=false
    else
        echo ">>> Starting LTC p2pool on port $LTC_WORKER_PORT..."
        
        # Kill existing LTC instance
        pkill -f "run_p2pool.*litecoin" 2>/dev/null || true
        sleep 1
        
        cd "$P2POOL_DIR"
        nohup pypy run_p2pool.py \
            --net litecoin \
            --bitcoind-address "$LTC_RPC_HOST" \
            --bitcoind-rpc-port "$LTC_RPC_PORT" \
            --bitcoind-p2p-port "$LTC_P2P_PORT" \
            -a "$LTC_ADDRESS" \
            -w "$LTC_WORKER_PORT" \
            --p2pool-port "$LTC_P2POOL_PORT" \
            --merged-coind-address "$LTC_ADAPTER_HOST" \
            --merged-coind-rpc-port "$LTC_ADAPTER_PORT" \
            --merged-coind-rpc-user "$LTC_ADAPTER_USER" \
            --merged-coind-rpc-password "$LTC_ADAPTER_PASS" \
            --merged-coind-p2p-port "$DOGE_P2P_PORT" \
            --merged-coind-p2p-address "$DOGE_P2P_HOST" \
            --disable-upnp \
            "$LTC_RPC_USER" "$LTC_RPC_PASS" \
            > /tmp/ltc_p2pool.log 2>&1 &
        
        LTC_PID=$!
        echo "    LTC p2pool started (PID: $LTC_PID)"
        echo "    Web:     http://localhost:$LTC_WORKER_PORT/static/"
        echo "    Stratum: stratum+tcp://localhost:$LTC_WORKER_PORT"
        echo "    Log:     /tmp/ltc_p2pool.log"
        echo ""
    fi
fi

# ── Start Multi-Pool Dashboard Proxy ─────────────────────────────────────
if $START_PROXY; then
    echo ">>> Starting multi-pool dashboard proxy on port $PROXY_PORT..."
    
    # Kill existing proxy
    pkill -f "multipool_proxy" 2>/dev/null || true
    sleep 1
    
    cd "$P2POOL_DIR"
    nohup python3 multipool/multipool_proxy.py \
        --config multipool/config.yaml \
        --port "$PROXY_PORT" \
        > /tmp/multipool_proxy.log 2>&1 &
    
    PROXY_PID=$!
    echo "    Proxy started (PID: $PROXY_PID)"
    echo "    Dashboard: http://localhost:$PROXY_PORT/"
    echo "    Health:    http://localhost:$PROXY_PORT/api/health"
    echo "    Log:       /tmp/multipool_proxy.log"
    echo ""
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo "============================================================"
echo "  All services started!"
echo "============================================================"
echo ""
echo "  Direct access:"
$START_DGB && echo "    DGB Dashboard: http://localhost:$DGB_WORKER_PORT/static/"
$START_LTC && echo "    LTC Dashboard: http://localhost:$LTC_WORKER_PORT/static/"
echo ""
$START_PROXY && echo "  Unified Dashboard: http://localhost:$PROXY_PORT/"
echo ""
echo "  Each dashboard has chain-selector tabs (DGB/LTC) in the"
echo "  header. Click a tab to switch to that chain's view."
echo ""
echo "  To add a pool: click the [+] tab → enter the URL."
echo "  To remove a pool: right-click its tab."
echo ""
echo "  Logs:"
$START_DGB && echo "    tail -f /tmp/dgb_p2pool.log"
$START_LTC && echo "    tail -f /tmp/ltc_p2pool.log"
$START_PROXY && echo "    tail -f /tmp/multipool_proxy.log"
echo ""
