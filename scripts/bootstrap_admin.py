"""Secure, local-only first Admin bootstrap for Change Sentinel."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Allow the documented ``python scripts/bootstrap_admin.py`` invocation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cs.auth import AuthError, AuthStore
from cs.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first Change Sentinel Admin account.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        parser.error("Passwords do not match.")
    try:
        user = AuthStore(Settings.from_env().db_path).bootstrap_admin(args.username, args.email, password)
    except AuthError as exc:
        parser.error(str(exc))
    print(f"Created Admin account '{user.username}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
