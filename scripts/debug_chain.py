"""Debug: trace what changes between session entry and receipt entry."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

# Set up isolated env
tmp = Path(tempfile.mkdtemp())
home = tmp / "home"
home.mkdir()
repo = tmp / "repo"
repo.mkdir()

os.environ["HOME"] = str(home)

import workproof.keyring as kr

kr.DEFAULT_KEYRING_DIR = home / ".workproof"
kr.PRIVATE_KEY_PATH = home / ".workproof" / "id_ed25519"
kr.PUBLIC_KEY_PATH = home / ".workproof" / "id_ed25519.pub"

os.chdir(repo)
subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
(repo / "README.md").write_text("hi\n")
subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)

from typer.testing import CliRunner
from workproof.cli import app

runner = CliRunner()
runner.invoke(app, ["init"])

(repo / "app.py").write_text("x = 1\n")
subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=repo, check=True)
head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

runner.invoke(app, ["run", "--", "python3", "-c", "print('test')"])

# Read session directly
session_path = repo / ".workproof" / "session.jsonl"
print("=== SESSION ENTRY (raw line) ===")
session_line = session_path.read_text().splitlines()[0]
print(session_line[:400])
session_entry = json.loads(session_line)

# Now attest
runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "x"])

receipt_path = repo / ".workproof" / "receipts" / f"{head_sha}.json"
receipt = json.loads(receipt_path.read_text())

print("\n=== RECEIPT ENTRY (from statement.predicate.entries[0]) ===")
receipt_entry = receipt["statement"]["predicate"]["entries"][0]
print(json.dumps(receipt_entry, sort_keys=True)[:400])

print("\n=== DIFF ===")
session_keys = set(session_entry.keys())
receipt_keys = set(receipt_entry.keys())
print("Keys only in session:", session_keys - receipt_keys)
print("Keys only in receipt:", receipt_keys - session_keys)

for k in session_keys & receipt_keys:
    if session_entry[k] != receipt_entry[k]:
        print(f"VALUE DIFFERS for {k}:")
        print(f"  session: {session_entry[k]!r}")
        print(f"  receipt: {receipt_entry[k]!r}")

# Now check hash
from workproof.chain import compute_entry_hash

session_hash = compute_entry_hash(session_entry)
receipt_hash = compute_entry_hash(receipt_entry)
print(f"\nSession entry hash: {session_hash}")
print(f"Receipt entry hash: {receipt_hash}")
print(f"Receipt entry stored hash: {receipt_entry['hash']}")
