#!/bin/bash
set -e

# P2Pool Merged Mining Installer for Ubuntu 24.04
# Sets up PyPy2, P2Pool dependencies, and optional systemd service
# for Litecoin + Dogecoin merged mining.

echo "=== P2Pool Merged Mining (V36) Installer ==="
echo ""

# Update packages
echo "Updating system packages..."
sudo apt-get update
sudo apt-get install -y build-essential git wget tar libbz2-dev zlib1g-dev \
    libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev \
    libffi-dev curl screen

INSTALL_DIR="/opt/p2pool"
PYPY_VERSION="pypy2.7-v7.3.20-linux64"
REPO_URL="https://github.com/frstrtr/p2pool-merged-v36.git"

echo "Creating installation directory at $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo chown "$USER:$USER" "$INSTALL_DIR"
cd "$INSTALL_DIR"

# 1. Install PyPy 2.7
if [ ! -d "$PYPY_VERSION" ]; then
    echo "Downloading PyPy 2.7..."
    wget "https://downloads.python.org/pypy/$PYPY_VERSION.tar.bz2"
    tar xjf "$PYPY_VERSION.tar.bz2"
    rm -f "$PYPY_VERSION.tar.bz2"
else
    echo "PyPy 2.7 already present."
fi

PYPY_BIN="$INSTALL_DIR/$PYPY_VERSION/bin/pypy"

# 2. Install pip for PyPy
echo "Installing pip for PyPy..."
curl -sS https://bootstrap.pypa.io/pip/2.7/get-pip.py -o get-pip.py
"$PYPY_BIN" get-pip.py
rm -f get-pip.py

# 3. Install Python dependencies
echo "Installing Python dependencies..."
"$PYPY_BIN" -m pip install twisted==19.10.0 pycryptodome 'scrypt>=0.8.0,<=0.8.22' ecdsa

# 4. Clone/Update P2Pool
REPO_DIR="$HOME/p2pool-merged-v36"
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning p2pool-merged-v36 repository..."
    git clone "$REPO_URL" "$REPO_DIR"
else
    echo "Repository already exists at $REPO_DIR, pulling latest..."
    cd "$REPO_DIR" && git pull && cd "$INSTALL_DIR"
fi

# 5. Verify scrypt hashing works
echo "Verifying scrypt hashing..."
cd "$REPO_DIR"
"$PYPY_BIN" -c "import ltc_scrypt; print('scrypt hashing OK')" || {
    echo "WARNING: scrypt hashing not available. Check installation."
}

echo ""
echo "=== Installation complete! ==="
echo ""
echo "PyPy:   $PYPY_BIN"
echo "P2Pool: $REPO_DIR"
echo ""
echo "Quick start:"
echo "  cd $REPO_DIR"
echo "  $PYPY_BIN run_p2pool.py \\"
echo "      --net litecoin \\"
echo "      --coind-address 127.0.0.1 \\"
echo "      --coind-rpc-port 9332 \\"
echo "      --address YOUR_LTC_ADDRESS \\"
echo "      litecoinrpc YOUR_LTC_RPC_PASSWORD"
echo ""
echo "For merged mining (LTC+DOGE), see README.md for full setup with MM-Adapter."
echo ""

# 6. Optional: Create systemd service
read -p "Create systemd service? [y/N]: " CREATE_SERVICE
if [[ "$CREATE_SERVICE" =~ ^[Yy]$ ]]; then
    read -p "Litecoin RPC user [litecoinrpc]: " RPC_USER
    RPC_USER=${RPC_USER:-litecoinrpc}
    read -p "Litecoin RPC password: " RPC_PASS
    read -p "Your LTC payout address (legacy L... format): " PAYOUT_ADDR

    SERVICE_FILE="/etc/systemd/system/p2pool.service"
    sudo bash -c "cat > $SERVICE_FILE" <<EOL
[Unit]
Description=P2Pool Litecoin Merged Mining (V36)
After=network.target

[Service]
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$PYPY_BIN $REPO_DIR/run_p2pool.py \\
    --net litecoin \\
    --coind-address 127.0.0.1 \\
    --coind-rpc-port 9332 \\
    --coind-p2p-port 9333 \\
    --address $PAYOUT_ADDR \\
    --give-author 1 \\
    --disable-upnp \\
    $RPC_USER $RPC_PASS
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

    sudo systemctl daemon-reload
    sudo systemctl enable p2pool
    echo ""
    echo "Systemd service created."
    echo "  Start:  sudo systemctl start p2pool"
    echo "  Logs:   sudo journalctl -u p2pool -f"
    echo "  Status: sudo systemctl status p2pool"
fi
