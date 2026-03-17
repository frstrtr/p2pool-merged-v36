#!/bin/bash
# Build script for ltc_scrypt module
# Target: Python 2.7 / PyPy (p2pool runtime)

set -e

echo "=== Building ltc_scrypt module ==="
echo ""

# Detect interpreters (PyPy preferred, Python 2 fallback)
HAS_PYPY=0
HAS_PYTHON2=0

if command -v pypy &> /dev/null; then
    HAS_PYPY=1
    echo "Found PyPy: $(pypy --version 2>&1 | head -1)"
fi

if command -v python2 &> /dev/null; then
    HAS_PYTHON2=1
    echo "Found Python 2: $(python2 --version 2>&1)"
fi

echo ""

if [ $HAS_PYPY -eq 0 ] && [ $HAS_PYTHON2 -eq 0 ]; then
    echo "ERROR: No compatible interpreter found!"
    echo "  p2pool requires PyPy (recommended) or Python 2.7"
    echo "  Install PyPy:     apt-get install pypy"
    echo "  Install Python 2: apt-get install python2-dev"
    exit 1
fi

# Build with preferred interpreter
if [ $HAS_PYPY -eq 1 ]; then
    PYTHON=pypy
    echo "Building with PyPy (recommended for p2pool)..."
else
    PYTHON=python2
    echo "Building with Python 2..."
fi

$PYTHON setup.py build
echo "Installing..."
$PYTHON setup.py install --user
echo ""

echo "=== Build Complete ==="
echo "Interpreter: $($PYTHON --version 2>&1 | head -1)"
echo ""
echo "To test: $PYTHON test_scrypt.py"
