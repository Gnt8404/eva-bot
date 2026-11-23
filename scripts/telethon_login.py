"""Create a read-only Telethon StringSession for the Aetherlyn reader.

Run this once on a trusted machine, then put the printed value into TG_SESSION.
The script never sends messages and never writes the session to disk by default.

    TG_API_ID=123456 TG_API_HASH=... python scripts/telethon_login.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> int:
    api_id = int(os.getenv("TG_API_ID", "0") or 0)
    api_hash = os.getenv("TG_API_HASH", "").strip()
    if not api_id or not api_hash:
        print("Set TG_API_ID and TG_API_HASH in the environment first.", file=sys.stderr)
        return 2
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()  # interactive: phone, code, optional 2FA password
    try:
        print("Read-only StringSession (treat it as a password):", file=sys.stderr)
        print(StringSession.save(client.session))
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
