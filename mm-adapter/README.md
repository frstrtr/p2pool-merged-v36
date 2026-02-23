# Merged Mining RPC Adapter

A Python 3 service that acts as a bridge between P2Pool and standard cryptocurrency daemons
for merged mining support.

## Purpose

P2Pool's merged mining implementation expects certain RPC methods that standard daemons
may not provide (or provide differently). This adapter:

1. **Accepts** RPC calls from P2Pool on its configured port
2. **Translates** them to standard daemon RPC calls
3. **Builds AuxPOW proofs** when the parent chain finds a block
4. **Submits** blocks to the aux chain daemon with proper AuxPOW

## Architecture

```
┌─────────────┐   JSON-RPC   ┌──────────────┐   JSON-RPC   ┌─────────────┐
│   P2Pool    │◀────────────▶│  MM Adapter  │◀────────────▶│  Dogecoin   │
│  (PyPy 2.7) │  Port 44556  │ (Python 3)   │  Port 22555  │  (Standard) │
└─────────────┘              └──────────────┘              └─────────────┘
```

## Requirements

- Python 3.10+
- aiohttp
- Standard Dogecoin (or compatible) daemon with `getauxblock` support

## Installation

```bash
cd mm-adapter
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your settings
```

A testnet-specific example is also provided:
```bash
cp config.example.testnet.yaml config.yaml
```

### Config File Structure

```yaml
# ── Adapter server (P2Pool connects here) ───────────────
server:
  host: "127.0.0.1"          # Bind address
  port: 44556                 # Port for P2Pool's --merged-coind-rpc-port
  rpc_user: "dogecoinrpc"    # Must match P2Pool's --merged-coind-rpc-user
  rpc_password: "changeme"   # Must match P2Pool's --merged-coind-rpc-password

# ── Upstream Dogecoin daemon ────────────────────────────
upstream:
  host: "127.0.0.1"          # Dogecoin Core RPC host
  port: 22555                 # RPC port (mainnet 22555, testnet 44555)
  rpc_user: "dogecoinrpc"    # From dogecoin.conf rpcuser
  rpc_password: "changeme"   # From dogecoin.conf rpcpassword
  timeout: 30                 # RPC timeout (seconds)

# ── Chain identification ────────────────────────────────
chain:
  name: "dogecoin"            # Label (no runtime effect)
  chain_id: 98                # Dogecoin AuxPOW chain ID
  network_magic: "c0c0c0c0"  # mainnet: c0c0c0c0 | testnet: fcc1b7dc

# ── Pool branding ──────────────────────────────────────
coinbase_text: "c2poolmerged" # OP_RETURN data in merged blocks

# ── Logging ─────────────────────────────────────────────
logging:
  level: "INFO"               # DEBUG | INFO | WARNING | ERROR
  format: "text"              # text | json
  file: null                  # null = stdout only, or path to file
```

### Mainnet Example

```yaml
server:
  host: "127.0.0.1"
  port: 44556
  rpc_user: "dogecoinrpc"
  rpc_password: "YOUR_SECURE_PASSWORD"

upstream:
  host: "127.0.0.1"
  port: 22555
  rpc_user: "dogecoinrpc"
  rpc_password: "YOUR_SECURE_PASSWORD"
  timeout: 30

chain:
  name: "dogecoin"
  chain_id: 98
  network_magic: "c0c0c0c0"

coinbase_text: "c2poolmerged"

logging:
  level: "INFO"
  format: "text"
  file: null
```

### Testnet Example

```yaml
server:
  host: "127.0.0.1"
  port: 44556
  rpc_user: "dogecoinrpc"
  rpc_password: "testpass"

upstream:
  host: "127.0.0.1"
  port: 44555              # Dogecoin testnet RPC
  rpc_user: "dogecoinrpc"
  rpc_password: "testpass"
  timeout: 30

chain:
  name: "dogecoin_testnet"
  chain_id: 98
  network_magic: "fcc1b7dc"

coinbase_text: "c2poolmerged"

logging:
  level: "DEBUG"
  format: "text"
  file: null
```

### Credential Alignment

The adapter sits between P2Pool and Dogecoin Core. Credentials must match on both sides:

```
P2Pool flags                    ←→  config.yaml server.*
  --merged-coind-rpc-user       =   server.rpc_user
  --merged-coind-rpc-password   =   server.rpc_password
  --merged-coind-rpc-port       =   server.port

config.yaml upstream.*          ←→  dogecoin.conf
  upstream.rpc_user             =   rpcuser
  upstream.rpc_password         =   rpcpassword
  upstream.port                 =   rpcport
```

## Running

```bash
# Activate venv if not already
source venv/bin/activate

# Run the adapter
python3 adapter.py --config config.yaml
```

Or with Docker:

```bash
docker build -t mm-adapter .
docker run -p 44556:44556 -v $(pwd)/config.yaml:/app/config.yaml mm-adapter
```

## P2Pool Configuration

Point P2Pool's merged mining settings to this adapter:

```bash
python run_p2pool.py \
    --net litecoin \
    ... \
    --merged-coind-address 127.0.0.1 \
    --merged-coind-rpc-port 44556 \
    --merged-coind-rpc-user dogecoinrpc \
    --merged-coind-rpc-password YOUR_SECURE_PASSWORD \
    ...
```

## Supported Aux Chains

- Dogecoin (Scrypt, chain_id=98)
- More coming...

## How It Works

### 1. Work Request (P2Pool → Adapter → Dogecoin)

```
P2Pool calls: getblocktemplate()
Adapter calls: getauxblock() on Dogecoin
Adapter returns: Modified template with aux chain data
```

### 2. Block Submission (P2Pool → Adapter → Dogecoin)

```
P2Pool calls: submitauxblock(aux_hash, auxpow_hex)
Adapter calls: getauxblock(aux_hash, auxpow_hex) on Dogecoin
Adapter returns: Success/failure
```

## Development

```bash
# Run tests
pytest tests/

# Run with debug logging
python3 adapter.py --config config.yaml --debug
```

## License

Same as P2Pool - GNU GPL v3
