# P2Pool Installation Guide (Litecoin + Dogecoin Merged Mining)

Complete installation guide for P2Pool on Ubuntu/Debian systems with Litecoin (scrypt) mining and Dogecoin merged mining.

## Table of Contents
- [System Requirements](#system-requirements)
- [Litecoin Core Installation](#litecoin-core-installation)
- [Dogecoin Core Installation](#dogecoin-core-installation)
- [Python Environment Setup](#python-environment-setup)
- [P2Pool Installation](#p2pool-installation)
- [Configuration](#configuration)
- [Running P2Pool](#running-p2pool)
- [MM-Adapter Setup](#mm-adapter-setup)
- [Troubleshooting](#troubleshooting)

---

## System Requirements

### Minimum Requirements
- **OS**: Ubuntu 20.04+ or Debian 11+
- **CPU**: 2+ cores
- **RAM**: 4GB minimum, 8GB recommended
- **Disk**: 20GB+ (for Litecoin blockchain) + 80GB+ (for Dogecoin blockchain)
- **Network**: Stable internet connection

### Required Ports
- **9333**: Litecoin P2P (incoming connections)
- **9332**: Litecoin RPC (localhost only)
- **22556**: Dogecoin P2P (incoming connections)
- **22555**: Dogecoin RPC (localhost only)
- **9327**: P2Pool Stratum (for miners)
- **9326**: P2Pool P2P (for peer connections)
- **44556**: MM-Adapter RPC (internal, localhost)

---

## Litecoin Core Installation

### 1. Install Dependencies
```bash
sudo apt-get update
sudo apt-get install -y build-essential libtool autotools-dev automake pkg-config \
    bsdmainutils python3 libssl-dev libevent-dev libboost-system-dev \
    libboost-filesystem-dev libboost-chrono-dev libboost-test-dev \
    libboost-thread-dev libminiupnpc-dev libzmq3-dev git wget
```

### 2. Install Litecoin Core (Binary)
```bash
cd ~
wget https://download.litecoin.org/litecoin-0.21.4/linux/litecoin-0.21.4-x86_64-linux-gnu.tar.gz
tar xzf litecoin-0.21.4-x86_64-linux-gnu.tar.gz
sudo install -m 0755 litecoin-0.21.4/bin/* /usr/local/bin/
```

Or build from source:
```bash
cd ~
git clone https://github.com/litecoin-project/litecoin.git
cd litecoin
git checkout v0.21.4
./autogen.sh
./configure --without-gui --disable-tests --disable-bench
make -j$(nproc)
sudo make install
```

### 3. Configure Litecoin Core
Create `~/.litecoin/litecoin.conf`:
```bash
mkdir -p ~/.litecoin
cat > ~/.litecoin/litecoin.conf << EOF
server=1
daemon=1
rpcuser=litecoinrpc
rpcpassword=$(openssl rand -hex 32)
rpcallowip=127.0.0.1
rpcport=9332
txindex=1
EOF
```

### 4. Start Litecoin Core and Sync
```bash
litecoind

# Monitor sync progress
litecoin-cli getblockchaininfo

# Wait until "blocks" equals "headers" (may take several hours)
```

---

## Dogecoin Core Installation

### 1. Install Dogecoin Core (Binary)
```bash
cd ~
wget https://github.com/dogecoin/dogecoin/releases/download/v1.14.9/dogecoin-1.14.9-x86_64-linux-gnu.tar.gz
tar xzf dogecoin-1.14.9-x86_64-linux-gnu.tar.gz
sudo install -m 0755 dogecoin-1.14.9/bin/* /usr/local/bin/
```

### 2. Configure Dogecoin Core
Create `~/.dogecoin/dogecoin.conf`:
```bash
mkdir -p ~/.dogecoin
cat > ~/.dogecoin/dogecoin.conf << EOF
server=1
daemon=1
rpcuser=dogecoinrpc
rpcpassword=$(openssl rand -hex 32)
rpcallowip=127.0.0.1
rpcport=22555
txindex=1
EOF
```

### 3. Start Dogecoin Core and Sync
```bash
dogecoind

# Monitor sync progress
dogecoin-cli getblockchaininfo
```

---

## Python Environment Setup

### Problem: Python 2 is End-of-Life
P2Pool requires Python 2.7, which is no longer available in modern Ubuntu/Debian distributions. We use PyPy as a solution.

### 1. Install PyPy (Python 2.7 Alternative)
```bash
# Install PyPy via snap (recommended)
sudo snap install pypy --classic

# Verify installation
pypy --version
# Should show: Python 2.7.18
```

Or install via tarball:
```bash
wget https://downloads.python.org/pypy/pypy2.7-v7.3.20-linux64.tar.bz2
tar xjf pypy2.7-v7.3.20-linux64.tar.bz2
export PATH="$PWD/pypy2.7-v7.3.20-linux64/bin:$PATH"
```

### 2. Install Python Dependencies

#### Install pip for PyPy
```bash
cd /tmp
wget https://bootstrap.pypa.io/pip/2.7/get-pip.py
pypy get-pip.py
```

#### Install Required Packages
```bash
# Core dependencies (includes scrypt hashing)
pypy -m pip install twisted==19.10.0 pycryptodome 'scrypt>=0.8.0,<=0.8.22'

# ECDSA library (required for share messaging signature verification)
pypy -m pip install ecdsa

# Optional: For web interface SSL
pypy -m pip install pyasn1 pyasn1-modules service_identity
```

**Note on scrypt package version**: Versions above 0.8.22 use Python 3 f-strings and are incompatible with Python 2.7/PyPy.

### 3. Handle OpenSSL Import Warnings

**Problem**: You may see `ImportError: No module named OpenSSL` warnings in logs.

**Solution**: These are non-fatal warnings from Twisted trying to import SSL for HTTPS. If they bother you:

```bash
# Option 1: Install pyOpenSSL (may cause issues with snap PyPy)
pypy -m pip install pyOpenSSL

# Option 2: Ignore the warnings (recommended)
# They don't affect P2Pool functionality for local mining
```

---

## P2Pool Installation

### 1. Clone P2Pool Repository
```bash
cd ~
git clone https://github.com/frstrtr/p2pool-merged-v36.git
cd p2pool-merged-v36
```

### 2. Verify Scrypt Hashing

The `ltc_scrypt.py` wrapper auto-detects the scrypt library. Verify it works:

```bash
pypy -c "import ltc_scrypt; print('scrypt hashing OK')"
```

If this fails, ensure `scrypt` is installed:
```bash
pypy -m pip install 'scrypt>=0.8.0,<=0.8.22'
```

**Legacy fallback**: If the pip `scrypt` package won't install (e.g., missing libssl-dev), you can build the C extension:
```bash
cd litecoin_scrypt
pypy setup.py install --user
cd ..
```

---

## Configuration

### Network Modes

P2Pool can run in two modes:

#### 1. Standalone Mode (Testing/Solo Mining)
Edit `p2pool/networks/litecoin.py`:
```python
PERSIST = False
```

**Pros**: 
- No peers required
- Works immediately
- Good for testing

**Cons**:
- No share chain benefits
- Mining alone (higher variance)

#### 2. Multi-Node Mode (Production/Pool Mining)
Edit `p2pool/networks/litecoin.py`:
```python
PERSIST = True
```

**Pros**:
- Part of P2Pool share chain
- Lower variance
- Collaborative mining

**Cons**:
- Requires peer connections
- Must sync P2Pool share chain

### Generate Mining Address

```bash
# Create new wallet (if needed)
litecoin-cli createwallet "mining"

# Get new legacy address (required for P2Pool)
litecoin-cli getnewaddress "" legacy
# Example output: LYourLitecoinAddressHere
```

**Important**: Use a **legacy** address (starts with `L` on mainnet, `m`/`n` on testnet). Bech32 (`ltc1...`) addresses are not directly supported for P2Pool share chain payouts.

---

## Running P2Pool

### Start P2Pool (Litecoin Only)

```bash
cd ~/p2pool-merged-v36

# For Litecoin mainnet
pypy run_p2pool.py \
    --net litecoin \
    --bitcoind-address 127.0.0.1 \
    --bitcoind-rpc-port 9332 \
    -a YOUR_LTC_ADDRESS \
    litecoinrpc YOUR_LTC_RPC_PASSWORD

# For Litecoin testnet
pypy run_p2pool.py \
    --net litecoin_testnet \
    --bitcoind-address 127.0.0.1 \
    --bitcoind-rpc-port 19332 \
    -a YOUR_LTC_TESTNET_ADDRESS \
    litecoinrpc YOUR_LTC_RPC_PASSWORD
```

### Start P2Pool with Merged Mining (Litecoin + Dogecoin)

See the main README for full merged mining startup with MM-Adapter.

```bash
pypy run_p2pool.py \
    --net litecoin \
    --coind-address 127.0.0.1 \
    --coind-rpc-port 9332 \
    --coind-p2p-port 9333 \
    --merged-coind-address 127.0.0.1 \
    --merged-coind-rpc-port 44556 \
    --merged-coind-rpc-user dogecoinrpc \
    --merged-coind-rpc-password YOUR_DOGE_RPC_PASSWORD \
    --address YOUR_LEGACY_LTC_ADDRESS \
    --give-author 1 \
    -f 1 \
    --disable-upnp \
    litecoinrpc YOUR_LTC_RPC_PASSWORD
```

### Run as Background Service

```bash
cd ~/p2pool-merged-v36
nohup pypy run_p2pool.py \
    --net litecoin \
    --bitcoind-address 127.0.0.1 \
    --bitcoind-rpc-port 9332 \
    -a YOUR_LTC_ADDRESS \
    litecoinrpc YOUR_LTC_RPC_PASSWORD \
    > p2pool.log 2>&1 &

# Monitor logs
tail -f p2pool.log

# Stop P2Pool
pkill -f "pypy.*run_p2pool"
```

### Create Systemd Service (Optional)

Create `/etc/systemd/system/p2pool.service`:
```ini
[Unit]
Description=P2Pool (Litecoin + Dogecoin Merged Mining)
After=litecoind.service
Requires=litecoind.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/p2pool-merged-v36
ExecStart=/snap/bin/pypy /home/YOUR_USERNAME/p2pool-merged-v36/run_p2pool.py \
    --net litecoin \
    --bitcoind-address 127.0.0.1 \
    --bitcoind-rpc-port 9332 \
    -a YOUR_LTC_ADDRESS \
    litecoinrpc YOUR_LTC_RPC_PASSWORD
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable p2pool
sudo systemctl start p2pool
sudo systemctl status p2pool
```

---

## MM-Adapter Setup

The MM-Adapter bridges P2Pool and Dogecoin Core for merged mining. See [mm-adapter/README.md](mm-adapter/README.md) for the full reference.

### Installation

```bash
cd mm-adapter
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Copy the appropriate example config:

```bash
# Mainnet
cp config.example.yaml config.yaml

# Or testnet
cp config.example.testnet.yaml config.yaml
```

Edit `config.yaml` — the key sections are:

```yaml
# Adapter server (P2Pool connects here)
server:
  host: "127.0.0.1"
  port: 44556                 # --merged-coind-rpc-port in P2Pool
  rpc_user: "dogecoinrpc"    # --merged-coind-rpc-user in P2Pool
  rpc_password: "CHANGE_ME"  # --merged-coind-rpc-password in P2Pool

# Upstream Dogecoin daemon
upstream:
  host: "127.0.0.1"
  port: 22555                 # dogecoin.conf rpcport (testnet: 44555)
  rpc_user: "dogecoinrpc"    # dogecoin.conf rpcuser
  rpc_password: "CHANGE_ME"  # dogecoin.conf rpcpassword
  timeout: 30

# Chain identification
chain:
  name: "dogecoin"
  chain_id: 98
  network_magic: "c0c0c0c0"  # mainnet: c0c0c0c0 | testnet: fcc1b7dc
```

**Credential alignment** — credentials must match on both sides:
- `server.rpc_user` / `server.rpc_password` must match P2Pool's `--merged-coind-rpc-user` / `--merged-coind-rpc-password`
- `upstream.rpc_user` / `upstream.rpc_password` must match `rpcuser` / `rpcpassword` in `dogecoin.conf`

### Running the Adapter

```bash
source venv/bin/activate
python3 adapter.py --config config.yaml
```

**Tip**: Start the adapter before P2Pool. P2Pool will connect to it on startup.

---

## Mining to P2Pool

### Point Your Miner to P2Pool

**Stratum URL**: `stratum+tcp://YOUR_IP:9327`

Example with cpuminer-multi:
```bash
cd ~
git clone https://github.com/tpruvot/cpuminer-multi.git
cd cpuminer-multi
./build.sh

# Mine with limited threads
./cpuminer -t 4 \
    -a scrypt \
    -o stratum+tcp://127.0.0.1:9327 \
    -u YOUR_LTC_ADDRESS \
    -p x
```

### Monitor P2Pool

```bash
# View web interface: http://YOUR_IP:9327/

# Check logs
tail -f ~/p2pool-merged-v36/p2pool.log

# Monitor shares
# P2Pool will show:
# "P2Pool: X shares in chain (Y verified/Z total) Peers: N"
# "Local: XXX kH/s in last N seconds"
```

---

## Troubleshooting

### Common Issues

#### 1. ImportError: No module named ltc_scrypt
```bash
pypy -m pip install 'scrypt>=0.8.0,<=0.8.22'
```

#### 2. AttributeError: ComposedWithContextualOptionalsType
Update to latest version:
```bash
cd ~/p2pool-merged-v36 && git pull
```

#### 3. ValueError: Block not found
Update to latest version:
```bash
cd ~/p2pool-merged-v36 && git pull
```

#### 4. "p2pool is not connected to any peers"
**Fixed**: P2Pool no longer requires peer connections to generate work. Works standalone even with PERSIST=True.

#### 5. PyPy Cache Issues
After code changes, clear bytecode cache:
```bash
cd ~/p2pool-merged-v36
find . -name "*.pyc" -delete
rm -rf __pycache__ */__pycache__ */*/__pycache__
```

#### 6. High CPU Usage
Limit miner threads:
```bash
./cpuminer -t 4 -a scrypt -o stratum+tcp://127.0.0.1:9327 -u ADDRESS -p x
```

#### 7. Stratum Connection Refused
```bash
ps aux | grep pypy          # Check P2Pool is running
ss -tuln | grep 9327        # Check port is listening
sudo ufw allow 9327/tcp     # Check firewall
```

### Log Analysis

```bash
grep -i error ~/p2pool-merged-v36/p2pool.log
grep "New work for worker" ~/p2pool-merged-v36/p2pool.log
grep "GOT SHARE" ~/p2pool-merged-v36/p2pool.log
tail -f ~/p2pool-merged-v36/p2pool.log | grep -E "(shares|Local:|Peers:)"
```

---

## Performance Tuning

### Litecoin Core Optimizations

Add to `~/.litecoin/litecoin.conf`:
```ini
dbcache=2000
maxconnections=125
```

### System Optimizations

```bash
echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf
sudo sysctl -w net.core.rmem_max=16777216
sudo sysctl -w net.core.wmem_max=16777216
```

---

## Updating P2Pool

```bash
cd ~/p2pool-merged-v36
pkill -f "pypy.*run_p2pool"
git pull
find . -name "*.pyc" -delete
pypy run_p2pool.py --net litecoin --bitcoind-address 127.0.0.1 --bitcoind-rpc-port 9332 -a YOUR_ADDRESS litecoinrpc YOUR_PASSWORD
```

---

## Security Considerations

1. **Firewall Configuration**:
```bash
sudo ufw allow 9333/tcp   # Litecoin P2P
sudo ufw allow 22556/tcp  # Dogecoin P2P
sudo ufw allow 9326/tcp   # P2Pool P2P
sudo ufw allow 9327/tcp   # Stratum (if mining remotely)
sudo ufw enable
```

2. **RPC Security**:
   - Keep Litecoin RPC on localhost only (never expose port 9332)
   - Keep Dogecoin RPC on localhost only (never expose port 22555)
   - Use strong random passwords

3. **P2Pool Security**:
   - Keep software updated
   - Monitor logs for suspicious activity
   - Use fail2ban for stratum port if publicly exposed

---

## Getting Help

- **GitHub Issues**: https://github.com/frstrtr/p2pool-merged-v36/issues
- **P2Pool Wiki**: https://en.bitcoin.it/wiki/P2Pool

---

## Credits

- P2Pool original: forrestv
- Litecoin/Scrypt adaptation and merged mining: community contributors

## License

See LICENSE file in repository.
