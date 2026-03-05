"""
Scrypt PoW hash shim for p2pool.

This file sits at the repo root and is found by Python BEFORE any installed
ltc_scrypt C extension, so it must actively locate and load the C extension
rather than relying on normal import resolution.

Load priority:
  1. C extension built in litecoin_scrypt/  (cd litecoin_scrypt && python setup.py build)
  2. C extension installed in site-packages (cd litecoin_scrypt && python setup.py install)
  3. py-scrypt package                      (pip install 'scrypt>=0.8.0,<=0.8.22')

PyPy 2.7 / Python 2.7 users: build the C extension (option 1 or 2) — py-scrypt
0.8.23+ requires Python 3 f-strings.
"""

import sys as _sys
import os as _os

_this_dir = _os.path.dirname(_os.path.abspath(__file__))
_ext_dir  = _os.path.join(_this_dir, 'litecoin_scrypt')


def _load_c_ext():
    """Load ltc_scrypt.so by file path, bypassing sys.path shadowing."""
    import glob as _glob

    # Candidate directories: litecoin_scrypt/, its build subdirs, and
    # any sys.path entry that is NOT the repo root (where this .py lives).
    _search = [_ext_dir]
    _search += _glob.glob(_os.path.join(_ext_dir, 'build', 'lib*'))
    _search += [p for p in _sys.path
                if p and _os.path.realpath(p) != _os.path.realpath(_this_dir)]

    for _d in _search:
        _hits = (_glob.glob(_os.path.join(_d, 'ltc_scrypt*.so')) +
                 _glob.glob(_os.path.join(_d, 'ltc_scrypt*.pyd')))
        if not _hits:
            continue

        # Python 2.7 / PyPy — imp.load_dynamic works for any .so
        try:
            import imp as _imp
            return _imp.load_dynamic('ltc_scrypt', _hits[0])
        except (ImportError, OSError):
            pass

        # Python 3.4+ — importlib (module name must match C's PyInit_ltc_scrypt)
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location('ltc_scrypt', _hits[0])
            _mod  = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            return _mod
        except (ImportError, AttributeError, OSError):
            pass

    return None


# --- Priority 1 & 2: C extension (PyPy 2.7 / Python 2 / Python 3) ----------
_c_ext = _load_c_ext()
if _c_ext is not None:
    getPoWHash = _c_ext.getPoWHash

# --- Priority 3: py-scrypt (Python 3, no compiled extension) ----------------
else:
    try:
        import scrypt as _scrypt
        # Eagerly verify the C library loads (catches GLIBC mismatches etc.)
        _scrypt.hash(b'\x00' * 80, b'\x00' * 80, N=1024, r=1, p=1, buflen=32)

        def getPoWHash(header):
            return _scrypt.hash(header, header, N=1024, r=1, p=1, buflen=32)

    except (ImportError, OSError):
        raise ImportError(
            "ltc_scrypt C extension not found and py-scrypt package not installed.\n"
            "\n"
            "For PyPy 2.7 / Python 2 (recommended for p2pool):\n"
            "  cd litecoin_scrypt && pypy setup.py build\n"
            "  # or: pypy setup.py install --user\n"
            "\n"
            "For Python 3:\n"
            "  pip install 'scrypt>=0.8.0,<=0.8.22'\n"
            "  # or: cd litecoin_scrypt && python3 setup_py3.py install --user\n"
        )
