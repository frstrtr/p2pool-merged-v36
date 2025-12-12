#!/bin/bash
# P2Pool-Dash REGTEST start script for testing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="$SCRIPT_DIR/p2pool_regtest.log"

# Kill existing regtest instances
pkill -f "pypy.*run_p2pool.*dash_regtest" 2>/dev/null

# Regtest address (generate one with: dash-cli -regtest getnewaddress)
REGTEST_ADDRESS="yTodnMujHcP34cfsjX2RFgJ56LJiYyDhZc"

echo "Starting P2Pool in REGTEST mode..."
echo "Log file: $LOG_FILE"

pypy run_p2pool.py --net dash_regtest \
    --dashd-address 127.0.0.1 \
    --dashd-rpc-port 19998 \
    -a "$REGTEST_ADDRESS" \
    --give-author 0 \
    "$@" 2>&1 | tee -a "$LOG_FILE"
