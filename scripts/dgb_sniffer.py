#!/usr/bin/env python2
"""
DGB P2Pool Protocol Sniffer

Connects to a live DGB p2pool node, performs handshake, requests shares,
and extracts the IDENTIFIER from the share ref_type data.

Usage: pypy scripts/dgb_sniffer.py [host] [port]
"""

import hashlib
import random
import socket
import struct
import sys
import time

# Known PREFIX from previous sniffing (message magic bytes)
PREFIX = '1c0553f23ebfcffe'.decode('hex')

HOST = sys.argv[1] if len(sys.argv) > 1 else 'usa.p2p-spb.xyz'
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5024


def sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def make_packet(command, payload):
    """Build a p2pool P2P packet."""
    cmd_padded = command + '\x00' * (12 - len(command))
    checksum = sha256d(payload)[:4]
    return PREFIX + cmd_padded + struct.pack('<I', len(payload)) + checksum + payload


def pack_varint(n):
    if n < 0xfd:
        return struct.pack('<B', n)
    elif n <= 0xffff:
        return '\xfd' + struct.pack('<H', n)
    elif n <= 0xffffffff:
        return '\xfe' + struct.pack('<I', n)
    else:
        return '\xff' + struct.pack('<Q', n)


def read_varint(data, pos):
    b = ord(data[pos])
    if b < 0xfd:
        return b, pos + 1
    elif b == 0xfd:
        return struct.unpack('<H', data[pos + 1:pos + 3])[0], pos + 3
    elif b == 0xfe:
        return struct.unpack('<I', data[pos + 1:pos + 5])[0], pos + 5
    else:
        return struct.unpack('<Q', data[pos + 1:pos + 9])[0], pos + 9


def read_varstr(data, pos):
    length, pos = read_varint(data, pos)
    return data[pos:pos + length], pos + length


def pack_address(services, address, port):
    """Pack a p2pool address structure: services(8) + address(16) + port(2)"""
    # p2pool uses a custom address packing
    import socket as _socket
    addr_bytes = _socket.inet_aton(address)
    # IPv4 mapped to IPv6
    addr_ipv6 = '\x00' * 10 + '\xff\xff' + addr_bytes
    return struct.pack('<Q', services) + addr_ipv6 + struct.pack('>H', port)


def make_version_payload(my_port=5024):
    """Build version message payload matching p2pool protocol."""
    version = struct.pack('<I', 3501)  # protocol version
    services = struct.pack('<Q', 0)  # services

    # addr_to
    addr_to = pack_address(0, HOST, PORT)
    # addr_from
    addr_from = pack_address(0, '0.0.0.0', my_port)
    # nonce
    nonce = struct.pack('<Q', random.randint(0, 2 ** 64 - 1))
    # sub_version (varstr)
    sub_ver = 'dgb-sniffer/0.1'
    sub_version = pack_varint(len(sub_ver)) + sub_ver
    # mode
    mode = struct.pack('<I', 1)
    # best_share_hash (PossiblyNoneType - pack as 0 for "None")
    best_share_hash = '\x00' * 32

    return version + services + addr_to + addr_from + nonce + sub_version + mode + best_share_hash


def recv_all(sock, timeout=15):
    """Receive all available data."""
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            sock.settimeout(1)  # Short timeout for subsequent reads
    except socket.timeout:
        pass
    return ''.join(chunks)


def parse_messages(data, prefix):
    """Parse p2pool messages from raw data. Yields (command, payload)."""
    pos = 0
    while pos < len(data) - 28:  # Minimum message: 8 prefix + 12 cmd + 4 len + 4 checksum
        # Find prefix
        idx = data.find(prefix, pos)
        if idx == -1:
            break
        pos = idx + len(prefix)
        if pos + 20 > len(data):
            break

        command = data[pos:pos + 12].rstrip('\x00')
        pos += 12
        length = struct.unpack('<I', data[pos:pos + 4])[0]
        pos += 4
        checksum = data[pos:pos + 4]
        pos += 4

        if pos + length > len(data):
            print "  [truncated message: %s, need %d more bytes]" % (command, pos + length - len(data))
            break

        payload = data[pos:pos + length]
        pos += length

        # Verify checksum
        calc_checksum = sha256d(payload)[:4]
        if calc_checksum != checksum:
            print "  [bad checksum for %s]" % command
            continue

        yield command, payload


def parse_version(payload):
    """Parse a version message payload."""
    pos = 0
    version = struct.unpack('<I', payload[pos:pos + 4])[0]
    pos += 4
    services = struct.unpack('<Q', payload[pos:pos + 8])[0]
    pos += 8
    # addr_to: 8 + 16 + 2 = 26 bytes
    pos += 26
    # addr_from: 26 bytes
    addr_from_services = struct.unpack('<Q', payload[pos:pos + 8])[0]
    addr_from_ip_raw = payload[pos + 8:pos + 24]
    addr_from_port = struct.unpack('>H', payload[pos + 24:pos + 26])[0]
    pos += 26
    nonce = struct.unpack('<Q', payload[pos:pos + 8])[0]
    pos += 8
    sub_version, pos = read_varstr(payload, pos)
    mode = struct.unpack('<I', payload[pos:pos + 4])[0]
    pos += 4
    best_share_hash = payload[pos:pos + 32]
    best_share_int = int(best_share_hash.encode('hex'), 16)

    return {
        'version': version,
        'services': services,
        'nonce': nonce,
        'sub_version': sub_version,
        'mode': mode,
        'best_share_hash': best_share_hash.encode('hex') if best_share_int != 0 else None,
    }


def make_sharereq(share_hashes, parents=500, id_=None):
    """Build a sharereq message.
    sharereq = id(256) + hashes(list of 256) + parents(varstr->varint) + stops(list of 256)
    """
    if id_ is None:
        id_ = random.randint(0, 2 ** 256 - 1)

    # id: 256-bit int (32 bytes, LE)
    payload = ''
    # Pack id as 32 bytes little-endian
    id_bytes = ''
    tmp = id_
    for _ in range(32):
        id_bytes += chr(tmp & 0xff)
        tmp >>= 8
    payload += id_bytes

    # hashes: list of 256-bit ints
    payload += pack_varint(len(share_hashes))
    for h in share_hashes:
        h_bytes = ''
        tmp = h
        for _ in range(32):
            h_bytes += chr(tmp & 0xff)
            tmp >>= 8
        payload += h_bytes

    # parents: varint
    payload += pack_varint(parents)

    # stops: empty list
    payload += pack_varint(0)

    return payload, id_


def parse_sharereply(payload):
    """Parse start of sharereply to get share type and raw data."""
    pos = 0

    # id: 32 bytes
    reply_id = payload[pos:pos + 32]
    reply_id_int = int(reply_id[::-1].encode('hex'), 16) if reply_id != '\x00' * 32 else 0
    pos += 32

    # result: varint (0=good, 1=too long, 2=unkown, 3=x11, 4=x12)
    result, pos = read_varint(payload, pos)

    # shares: list of (type, contents)
    num_shares, pos = read_varint(payload, pos)

    return {
        'id': reply_id.encode('hex'),
        'result': result,
        'num_shares': num_shares,
        'remaining_data': payload[pos:],
        'remaining_pos': pos,
    }


def extract_identifier_from_share(payload, share_start_pos):
    """Try to find the IDENTIFIER (8 bytes) in the share data.

    The identifier is in ref_type which is:
        identifier: FixedStr(8)
        share_info: ...

    In the share, ref_type data is part of the ref_merkle_link calculation.
    The raw ref_type data appears in the share contents.

    For V35 share format (share_type=35):
    share_contents = {
        min_header:     ...
        share_info:     ...  (complex nested structure)
        ref_merkle_link: ... (merkle link)
        last_txout_nonce: ...
        hash_link:       ...
        merkle_link:     ...
    }

    The ref_merkle_link's extra data contains the ref_type.
    Actually, the identifier is NOT directly in the share contents wire format.
    It's used in the ref_hash calculation:
        ref_hash = check_merkle_link(hash256(ref_type.pack({identifier, share_info})), ref_merkle_link)

    So the IDENTIFIER is baked into the ref_hash, not transmitted raw.
    We can't extract it by just parsing the share wire format.

    BUT: We can brute-force it! The identifier is 8 bytes, and we know
    the share_info from the share contents. We also know the ref_merkle_link.
    So we can try candidate identifiers and see which one produces the
    correct ref_hash.

    However, there might be a simpler way: look at the _get_ref_hash function
    and see if identifier is fixed for a network.
    """
    pass


def main():
    print "=" * 60
    print "DGB P2Pool Protocol Sniffer"
    print "=" * 60
    print "Target: %s:%d" % (HOST, PORT)
    print "Known PREFIX: %s" % PREFIX.encode('hex')
    print

    # Connect
    print "[1] Connecting..."
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((HOST, PORT))
    except Exception as e:
        print "Connection failed: %s" % e
        return
    print "    Connected!"

    # Send version
    print "[2] Sending version handshake..."
    version_payload = make_version_payload()
    version_packet = make_packet('version', version_payload)
    sock.sendall(version_packet)

    # Receive response
    print "[3] Waiting for response..."
    data = recv_all(sock, timeout=5)
    print "    Received %d bytes" % len(data)

    if not data:
        print "    No data received!"
        sock.close()
        return

    # Parse messages
    print "[4] Parsing messages..."
    best_share_hash = None
    for command, payload in parse_messages(data, PREFIX):
        print "    MSG: %-15s (%d bytes)" % (command, len(payload))
        if command == 'version':
            info = parse_version(payload)
            print "         version=%d sub_version=%s mode=%d" % (info['version'], info['sub_version'], info['mode'])
            print "         best_share_hash=%s" % (info['best_share_hash'][:32] + '...' if info['best_share_hash'] else 'None')
            best_share_hash = info['best_share_hash']
        elif command == 'addrme':
            port = struct.unpack('<H', payload[:2])[0] if len(payload) >= 2 else '?'
            print "         port=%s" % port
        elif command == 'addrs':
            print "         (peer addresses)"
        elif command == 'have_tx':
            print "         (tx hashes)"
        elif command == 'shares':
            print "         RAW SHARE DATA (first 200 bytes hex):"
            print "         %s" % payload[:200].encode('hex')
        else:
            print "         first 50 bytes: %s" % payload[:50].encode('hex')

    # If we have a best_share_hash, request shares
    if best_share_hash:
        print
        print "[5] Requesting shares (best_share_hash=%s...)" % best_share_hash[:16]

        # Convert hex hash to int
        share_hash_int = int(best_share_hash, 16)
        sharereq_payload, req_id = make_sharereq([share_hash_int], parents=5)
        sharereq_packet = make_packet('sharereq', sharereq_payload)
        sock.sendall(sharereq_packet)

        print "    Waiting for sharereply..."
        time.sleep(3)
        data2 = recv_all(sock, timeout=10)
        print "    Received %d bytes" % len(data2)

        for command, payload in parse_messages(data2, PREFIX):
            print "    MSG: %-15s (%d bytes)" % (command, len(payload))
            if command == 'sharereply':
                info = parse_sharereply(payload)
                print "         id=%s..." % info['id'][:16]
                print "         result=%d (0=good)" % info['result']
                print "         num_shares=%d" % info['num_shares']
                
                if info['num_shares'] > 0 and info['result'] == 0:
                    remaining = info['remaining_data']
                    print
                    print "[6] Parsing share data..."
                    # Each share in the list is: (type:varint, contents:varstr)
                    pos = 0
                    for i in range(min(info['num_shares'], 3)):
                        share_type, pos = read_varint(remaining, pos)
                        contents_len, pos = read_varint(remaining, pos)
                        contents = remaining[pos:pos + contents_len]
                        pos += contents_len
                        print "    Share %d: type=%d, contents_len=%d" % (i, share_type, contents_len)
                        print "    First 200 bytes: %s" % contents[:200].encode('hex')
                        
                        # The identifier is in the ref_type which is computed from
                        # the share_info. We need to find it differently.
                        # 
                        # Strategy: The IDENTIFIER is typically the same for all shares
                        # on the same network. It's compiled into the network config.
                        # For a V35 share (type=35), the contents structure is:
                        #   min_header (fixed size)
                        #   share_info (complex)
                        #   ref_merkle_link
                        #   last_txout_nonce
                        #   hash_link
                        #   merkle_link
                        #
                        # Since we can't easily parse V35 share_info without the full
                        # pack/unpack machinery, let's dump the raw data and look for
                        # patterns.
                        
                        # Try alternate approach: search for common identifier patterns
                        # Identifier is 8 bytes. Let's check if the farsider350 identifier
                        # or the known PREFIX shows up
                        known_ids = [
                            '1bfe01eff5ba4e38',  # farsider350 IDENTIFIER
                            '1c0553f23ebfcffe',  # Known PREFIX (might also be IDENTIFIER?)
                        ]
                        for kid in known_ids:
                            kid_bytes = kid.decode('hex')
                            if kid_bytes in contents:
                                idx = contents.index(kid_bytes)
                                print "    *** FOUND %s at offset %d in contents! ***" % (kid, idx)
                        
                        print
            elif command == 'shares':
                print "         (unsolicited shares, %d bytes)" % len(payload)
                # shares message format: list of (type, contents)
                spos = 0
                num_shares, spos = read_varint(payload, spos)
                print "         num_shares=%d" % num_shares
                for i in range(min(num_shares, 3)):
                    share_type, spos = read_varint(payload, spos)
                    contents_len, spos = read_varint(payload, spos)
                    contents = payload[spos:spos + contents_len]
                    spos += contents_len
                    print "    Share %d: type=%d, contents_len=%d" % (i, share_type, contents_len)
                    print "    First 200 bytes: %s" % contents[:200].encode('hex')
                    
                    for kid in ['1bfe01eff5ba4e38', '1c0553f23ebfcffe']:
                        kid_bytes = kid.decode('hex')
                        if kid_bytes in contents:
                            idx = contents.index(kid_bytes)
                            print "    *** FOUND %s at offset %d in contents! ***" % (kid, idx)
                    print
    else:
        print
        print "[!] No best_share_hash received - can't request shares"

    # Also dump all raw data for offline analysis
    print
    print "[7] Dumping all raw data to /tmp/dgb_p2pool_sniff.bin"
    all_data = data + (data2 if 'data2' in dir() else '')
    with open('/tmp/dgb_p2pool_sniff.bin', 'wb') as f:
        f.write(all_data)
    print "    Wrote %d bytes" % len(all_data)

    sock.close()
    print
    print "Done!"


if __name__ == '__main__':
    main()
