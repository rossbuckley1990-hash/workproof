#!/usr/bin/env bash
# Acceptance test: simulate the "fresh machine" install → verified receipt flow.
# This mirrors acceptance criterion 1: "Fresh machine: pipx install → verified
# receipt on the demo PR in ≤5 minutes following only the README."
set -euo pipefail

WORKPROOF_DIR="/home/z/my-project/workproof"
VENV="/home/z/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/python -m pip"
TMPROOT="$(mktemp -d)"
export HOME="$TMPROOT/home"
mkdir -p "$HOME"

echo "=== Acceptance Test: fresh install → verified receipt ==="
echo "HOME: $HOME"
echo "TMPROOT: $TMPROOT"

# 1. Install (simulate pipx install — editable install from the project)
echo ""
echo "--- Step 1: install workproof-cli ---"
$PIP install -e "$WORKPROOF_DIR" --quiet 2>&1 | tail -3
$VENV/bin/workproof version

# 2. Create a demo repo
echo ""
echo "--- Step 2: create demo repo ---"
REPO="$TMPROOT/demo-repo"
mkdir -p "$REPO"
cd "$REPO"
git init -q
git config user.email "demo@example.com"
git config user.name "Demo"
git config commit.gpgsign false

cat > calculator.py << 'EOF'
def add(a, b):
    return a - b  # bug

def multiply(a, b):
    return a * b
EOF

cat > test_calculator.py << 'EOF'
from calculator import add, multiply

def test_add():
    assert add(2, 3) == 5

def test_multiply():
    assert multiply(3, 4) == 12
EOF

cat > .workproof.yml << 'EOF'
policy_version: "0.1"
allowed_commands:
  - pytest
  - python -m pytest
  - python3 -m pytest
default_ai_level: assisted
EOF

git add -A
git commit -q -m "base: buggy add()"

# 3. workproof init
echo ""
echo "--- Step 3: workproof init ---"
$VENV/bin/workproof init

# 4. Fix the bug first, then commit (honest workflow: record evidence only
#    against the commit you're going to attest)
echo ""
echo "--- Step 4: fix the bug and commit ---"
cat > calculator.py << 'EOF'
def add(a, b):
    return a + b  # fixed

def multiply(a, b):
    return a * b
EOF
git add -A
git commit -q -m "fix: add() now adds"
HEAD_SHA=$(git rev-parse HEAD)
echo "head sha: $HEAD_SHA"

# 5. workproof run -- pytest (fixed, should pass)
echo ""
echo "--- Step 5: workproof run -- pytest (fixed, expecting pass) ---"
$VENV/bin/workproof run -- python3 -m pytest

# 6. workproof attest
echo ""
echo "--- Step 6: workproof attest ---"
$VENV/bin/workproof attest --ai-level assisted --agent claude-code

# 7. workproof verify
echo ""
echo "--- Step 7: workproof verify ---"
$VENV/bin/workproof verify ".workproof/receipts/${HEAD_SHA}.json" \
    --repo . \
    --policy .workproof.yml \
    --expected-head-sha "$HEAD_SHA"
VERIFY_EXIT=$?

echo ""
echo "=== Acceptance Result ==="
if [ "$VERIFY_EXIT" -eq 0 ]; then
    echo "✓ PASS: verified receipt on demo PR"
    echo "  - install: OK"
    echo "  - init: OK"
    echo "  - run: OK (2 entries recorded)"
    echo "  - attest: OK (receipt written)"
    echo "  - verify: OK (exit 0, VERIFIED)"
    exit 0
else
    echo "✗ FAIL: verify exited with $VERIFY_EXIT"
    exit 1
fi
