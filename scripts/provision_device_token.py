#!/usr/bin/env python3
"""Create one local pairing token without printing it to logs."""

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / "secrets/device-token.txt"
HEADER_PATH = ROOT / "firmware/include/DeviceSecret.hpp"
ENV_PATH = ROOT / "server/.env"


def main() -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""
    if not token:
        token = secrets.token_urlsafe(32)
        TOKEN_PATH.write_text(token + "\n")
    HEADER_PATH.write_text(
        "#pragma once\n"
        "// Generated locally by scripts/provision_device_token.py; never commit.\n"
        f'#define STACKCHAN_DEVICE_TOKEN "{token}"\n'
    )
    existing = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    retained = [line for line in existing if not line.startswith("STACKCHAN_DEVICE_TOKEN=")]
    retained.append(f"STACKCHAN_DEVICE_TOKEN={token}")
    ENV_PATH.write_text("\n".join(retained) + "\n")
    for path in (TOKEN_PATH, HEADER_PATH, ENV_PATH):
        path.chmod(0o600)
    print("Local Stack-chan pairing token provisioned without displaying it.")


if __name__ == "__main__":
    main()
