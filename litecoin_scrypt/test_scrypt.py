#!/usr/bin/env python
"""
Test suite for ltc_scrypt C extension module.
Compatible with Python 2.7 / PyPy.

All expected hashes were cross-validated against the independent py-scrypt
package (scrypt.hash(header, header, N=1024, r=1, p=1, buflen=32)).
"""

import sys
import binascii

# Each vector: (name, 80-byte header hex, expected scrypt hash hex)
TEST_VECTORS = [
    # Real Litecoin genesis block (block #0)
    # nVersion=1, nTime=1317972665, nBits=0x1e0ffff0, nNonce=2084524493
    ("Litecoin genesis block",
     "01000000"
     "0000000000000000000000000000000000000000000000000000000000000000"
     "d9ced4ed1130f7b7faad9be25323ffafa33232a17c3edf6cfd97bee6bafbdd97"
     "b9aa8e4e"
     "f0ff0f1e"
     "cd513f7c",
     "001e67b013726fd7382e9acb69165b4b6316227fb3156b5b414ba6340c050000"),

    # Litecoin block #1
    ("Litecoin block #1",
     "01000000"
     "e2bf047e7e5a191aa4ef34d314979dc9986e0f19251edaba5940fd1fe365a712"
     "af7e6fce4be1c5e40a2c22d6e6973bcf04e89e60cda014a58edd62f748d34e7c"
     "b9aa8e4e"
     "f0ff0f1e"
     "8a100100",
     "a7524064e642010747a3e06dcbaf396d75f5c7da8834676bd1df4d6b74b83cef"),

    # All-zero input (edge case)
    ("All-zero 80-byte input",
     "00" * 80,
     "161d0876f3b93b1048cda1bdeaa7332ee210f7131b42013cb43913a6553a4b69"),

    # All-0xff input (edge case)
    ("All-0xff 80-byte input",
     "ff" * 80,
     "5253069c14ecedf978745486375ee37415e977f55cdbedac31ebee8bf33dd127"),
]


def test_scrypt():
    print("Testing ltc_scrypt module...")
    print("Python: %s" % sys.version)
    print("")

    try:
        import ltc_scrypt
    except ImportError as e:
        print("FAIL: cannot import ltc_scrypt: %s" % e)
        print("      build with: pypy setup.py build")
        return False

    passed = 0
    failed = 0

    for name, header_hex, expected_hex in TEST_VECTORS:
        header = binascii.unhexlify(header_hex)
        expected = binascii.unhexlify(expected_hex)

        result = ltc_scrypt.getPoWHash(header)

        if len(result) != 32:
            print("FAIL: %s — output length %d, expected 32" % (name, len(result)))
            failed += 1
            continue

        if result == expected:
            print("PASS: %s" % name)
            passed += 1
        else:
            print("FAIL: %s" % name)
            print("      expected: %s" % expected_hex)
            print("      got:      %s" % binascii.hexlify(result))
            failed += 1

    print("")
    print("%d passed, %d failed" % (passed, failed))
    return failed == 0


if __name__ == '__main__':
    ok = test_scrypt()
    sys.exit(0 if ok else 1)
