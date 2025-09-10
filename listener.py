import asyncio
import os
import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import requests

from dotenv import load_dotenv
from telethon import TelegramClient, events, types
from telethon.sessions import StringSession
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioRestException

from util import split_csv, parse_ids, send_whatsapp, sender_matches

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

load_dotenv()

REQUIRED_VARS = [
    "TG_API_ID",
    "TG_API_HASH",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_FROM",
    "TWILIO_WHATSAPP_TO",
    "TG_GROUP_IDS",
]

missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
if missing:
    joined = ", ".join(missing)
    raise SystemExit(f"Missing required environment variables: {joined}")

API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
TG_SESSION = os.getenv("TG_SESSION", "").strip() or None

MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL")
if MEDIA_BASE_URL:
    parsed = urlparse(MEDIA_BASE_URL)
    if parsed.scheme not in ("http", "https"):
        raise SystemExit("MEDIA_BASE_URL must start with http:// or https://")
    if parsed.hostname == "localhost":
        raise SystemExit(
            "MEDIA_BASE_URL cannot point to localhost. Twilio needs a public URL"
        )

# Twilio
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
WA_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
WA_TARGETS = split_csv(os.getenv("TWILIO_WHATSAPP_TO"))

# WhatsApp templates
TEXT_TPL = os.getenv("TWILIO_TEMPLATE_SID")
IMG_TPL = os.getenv("TWILIO_MEDIA_TEMPLATE_SID")
MAX_BODY = 1024

logging.info("WhatsApp from: %r, targets: %r", WA_FROM, WA_TARGETS)

twilio = TwilioClient(TWILIO_SID, TWILIO_TOKEN)

# ─────────────── helpers ────────────────────────────────────────────────

TG_GROUP_IDS = parse_ids(os.getenv("TG_GROUP_IDS"))
BOT_IDS = set(parse_ids(os.getenv("BOT_IDS", "")))

async def upload_fileio(local_path: Path) -> str:
    """Upload a file to file.io and return the public URL."""
    loop = asyncio.get_running_loop()
    url = "https://file.io/?expires=1w"

    def _post() -> str:
        with open(local_path, "rb") as f:
            resp = requests.post(url, files={"file": f})
            resp.raise_for_status()
            return resp.json()["link"]

    return await loop.run_in_executor(None, _post)


async def media_to_urls(msg: types.Message) -> List[str]:
    """Download photo/document media and upload to file.io."""
    urls: List[str] = []
    if getattr(msg, "photo", None) or getattr(msg, "document", None):
        path = await msg.download_media()
        if path:
            try:
                urls.append(await upload_fileio(Path(path)))
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
    return urls



# ─────────────── main ──────────────────────────────────────────────────
async def main():
    # Create or read the session string
    session_str = os.getenv("TG_SESSION", "").strip()
    session = StringSession(session_str or None)     # None -> create an empty one

    client = TelegramClient(session, API_ID, API_HASH)
    await client.start()            # If session is empty, this prompts for login/code

    # If the session was just created, tell the user where to put it
    if not session_str:
        new_string = client.session.save()
        print("\n===========  IMPORTANT!  ==========")
        print("Session created. Add the TG_SESSION line to your .env:\n")
        print(f"TG_SESSION={new_string}\n")
        print("Subsequent runs won't require confirmation.")
        print("================================\n")

    @client.on(events.NewMessage(chats=TG_GROUP_IDS))
    async def handler(event):
        if getattr(event.message, "grouped_id", None) is not None:
            return

        sender_ok = sender_matches(
            await event.get_sender(), event.sender_id, BOT_IDS
        )
        if not sender_ok:
            return

        text = event.message.message or event.message.raw_text
        media_urls = await media_to_urls(event.message)
        if not text and not media_urls:
            return

        try:
            await send_whatsapp(
                text,
                media_urls,
                twilio_client=twilio,
                wa_from=WA_FROM,
                wa_targets=WA_TARGETS,
                text_tpl=TEXT_TPL,
                img_tpl=IMG_TPL,
                max_body=MAX_BODY,
                exc_cls=TwilioRestException,
            )
            if text:
                logging.info("Forwarded: %s", text.splitlines()[0])
            else:
                logging.info("Forwarded media without text")
        except Exception as e:
            logging.error("Twilio error: %s", e)

    @client.on(events.Album(chats=TG_GROUP_IDS))
    async def album_handler(event):
        sender_ok = sender_matches(
            await event.get_sender(), event.sender_id, BOT_IDS
        )
        if not sender_ok:
            return

        urls: List[str] = []
        for msg in event.messages:
            urls.extend(await media_to_urls(msg))

        text = event.text or ""
        if not text and not urls:
            return

        try:
            await send_whatsapp(
                text,
                urls,
                twilio_client=twilio,
                wa_from=WA_FROM,
                wa_targets=WA_TARGETS,
                text_tpl=TEXT_TPL,
                img_tpl=IMG_TPL,
                max_body=MAX_BODY,
                exc_cls=TwilioRestException,
            )
            logging.info("Forwarded album with %d items", len(urls))
        except Exception as e:
            logging.error("Twilio error: %s", e)

    print("👂  Listening for messages …")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
