# Copyright (c) 2017 Pieter Wuille
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""Reference implementation for Bech32, Bech32m and segwit addresses."""

from p2pool.util.math import convertbits

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

# Constants for different Bech32 encoding types
BECH32_CONST = 1  # Original Bech32 (SegWit v0)
BECH32M_CONST = 0x2bc830a3  # Bech32m (SegWit v1+ / Taproot)


def bech32_polymod(values):
    """Internal function that computes the Bech32 checksum."""
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def bech32_hrp_expand(hrp):
    """Expand the HRP into values for checksum computation."""
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def bech32_verify_checksum(hrp, data, const):
    """Verify a checksum given HRP and converted data characters."""
    return bech32_polymod(bech32_hrp_expand(hrp) + data) == const


def bech32_create_checksum(hrp, data, const):
    """Compute the checksum values given HRP and data."""
    values = bech32_hrp_expand(hrp) + data
    polymod = bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def bech32_encode(hrp, data, const):
    """Compute a Bech32 string given HRP and data values."""
    combined = data + bech32_create_checksum(hrp, data, const)
    return hrp + '1' + ''.join([CHARSET[d] for d in combined])


def bech32_decode(bech):
    """Validate a Bech32/Bech32m string, and determine HRP and data."""
    if ((any(ord(x) < 33 or ord(x) > 126 for x in bech)) or
            (bech.lower() != bech and bech.upper() != bech)):
        return (None, None, None)
    bech = bech.lower()
    pos = bech.rfind('1')
    if pos < 1 or pos + 7 > len(bech) or len(bech) > 90:
        return (None, None, None)
    if not all(x in CHARSET for x in bech[pos+1:]):
        return (None, None, None)
    hrp = bech[:pos]
    data = [CHARSET.find(x) for x in bech[pos+1:]]
    
    # Try Bech32m first (for Taproot), then Bech32 (for SegWit v0)
    const = None
    checksum = bech32_polymod(bech32_hrp_expand(hrp) + data)
    if checksum == BECH32M_CONST:
        const = BECH32M_CONST
    elif checksum == BECH32_CONST:
        const = BECH32_CONST
    else:
        return (None, None, None)
    
    return (hrp, data[:-6], const)

def decode(hrp, addr):
    """Decode a segwit address."""
    hrpgot, data, const = bech32_decode(addr)
    if hrpgot != hrp:
        return (None, None)
    decoded = convertbits(data[1:], 5, 8, False)
    if decoded is None or len(decoded) < 2 or len(decoded) > 40:
        return (None, None)
    witver = data[0]
    if witver > 16:
        return (None, None)
    # SegWit v0 uses Bech32, v1+ (Taproot) uses Bech32m
    if witver == 0 and const != BECH32_CONST:
        return (None, None)
    if witver >= 1 and const != BECH32M_CONST:
        return (None, None)
    if witver == 0 and len(decoded) != 20 and len(decoded) != 32:
        return (None, None)
    if witver == 1 and len(decoded) != 32:
        return (None, None)
    return (witver, decoded)


def encode(hrp, witver, witprog):
    """Encode a segwit address."""
    # Use Bech32m for Taproot (witness v1+), Bech32 for SegWit v0
    const = BECH32M_CONST if witver >= 1 else BECH32_CONST
    converted = convertbits(witprog, 8, 5)
    if converted is None:
        return None
    ret = bech32_encode(hrp, [witver] + converted, const)
    if decode(hrp, ret) == (None, None):
        return None
    return ret
