"""Forward Telegram messages to WhatsApp with local media hosting."""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
import math
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from aiohttp import web
from dotenv import load_dotenv
from PIL import Image
from telethon import TelegramClient, events, types
from telethon.sessions import StringSession
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

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

MEDIA_PORT = int(os.getenv("MEDIA_PORT", "8080"))
MEDIA_BASE_URL = os.getenv("MEDIA_BASE_URL", f"http://localhost:{MEDIA_PORT}")
MEDIA_ROUTE = "/" + os.getenv("MEDIA_ROUTE", "/media").strip("/")

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

IMG_DIR = Path("Img")
IMG_DIR.mkdir(exist_ok=True)


TG_GROUP_IDS = parse_ids(os.getenv("TG_GROUP_IDS"))
BOT_IDS = set(parse_ids(os.getenv("BOT_IDS", "")))


async def start_media_server() -> web.AppRunner:
    async def serve_media(request: web.Request) -> web.StreamResponse:
        filename = request.match_info.get("filename")
        path = IMG_DIR / filename
        if not path.exists():
            logging.error("Requested file missing: %s", path)
            raise web.HTTPNotFound()
        logging.info("Serving %s", path)
        return web.FileResponse(path)

    app = web.Application()
    app.router.add_get(f"{MEDIA_ROUTE}/{{filename}}", serve_media)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, port=MEDIA_PORT)
    await site.start()
    logging.info("Media server running at %s%s", MEDIA_BASE_URL.rstrip("/"), MEDIA_ROUTE)
    return runner


async def save_media(msg: types.Message) -> Optional[Tuple[Path, str]]:
    """Download media and return the local path and public URL."""
    if not getattr(msg, "photo", None) and not getattr(msg, "document", None):
        return None

    filename = f"{msg.id}_{int(msg.date.timestamp())}"
    path_str = await msg.download_media(file=IMG_DIR / filename)
    if not path_str:
        logging.error("Failed to download media for message %s", msg.id)
        return None

    logging.info("Downloaded media to %s", path_str)

    path = Path(path_str)
    if path.suffix.lower() == ".webp":
        jpg_path = path.with_suffix(".jpg")
        try:
            Image.open(path).convert("RGB").save(jpg_path, "JPEG")
            path.unlink()
            logging.info("Converted %s to %s", path, jpg_path)
            path = jpg_path
        except Exception as e:
            logging.error("Failed to convert %s: %s", path, e)

    base = MEDIA_BASE_URL.rstrip("/")
    url = f"{base}{MEDIA_ROUTE}/{path.name}"
    return path, url


async def media_to_urls(msg: types.Message) -> List[str]:
    saved = await save_media(msg)
    if not saved:
        return []
    return [saved[1]]


def make_collage(paths: List[Path]) -> Path:
    """Create a simple two-column collage from the given image paths."""
    if not paths:
        raise ValueError("no images for collage")

    images = []
    for p in paths:
        with Image.open(p) as im:
            images.append(im.convert("RGB"))
    cell_w = max(im.width for im in images)
    cell_h = max(im.height for im in images)

    cols = 2
    rows = math.ceil(len(images) / cols)
    collage = Image.new("RGB", (cell_w * cols, cell_h * rows), color="white")

    for idx, im in enumerate(images):
        im.thumbnail((cell_w, cell_h))
        x = (idx % cols) * cell_w
        y = (idx // cols) * cell_h
        collage.paste(im, (x, y))
        im.close()

    name = f"collage_{int(datetime.now().timestamp())}.jpg"
    out_path = IMG_DIR / name
    collage.save(out_path, "JPEG")
    collage.close()
    return out_path




async def nightly_cleanup(
    directory: Path = IMG_DIR,
    *,
    now_func: callable = datetime.now,
    run_once: bool = False,
) -> None:
    """Delete files older than 24 hours.

    Parameters
    ----------
    directory:
        Directory to clean. Defaults to ``IMG_DIR``.
    now_func:
        Function returning the current ``datetime``. This is injected for
        easier testing and defaults to :func:`datetime.now`.
    run_once:
        If ``True`` the cleanup is executed immediately and the coroutine
        returns instead of looping and scheduling sleeps.
    """

    def _cleanup() -> None:
        cutoff = now_func() - timedelta(hours=24)
        for path in directory.iterdir():
            if not path.is_file():
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
                    logging.info("Deleted old file %s", path)
            except Exception as e:
                logging.error("Failed to delete %s: %s", path, e)

    if run_once:
        _cleanup()
        return

    while True:
        now = now_func()
        target = datetime.combine(now.date(), time(23, 59))
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        _cleanup()




# ─────────────── main ──────────────────────────────────────────────────
async def main():
    await start_media_server()
    asyncio.create_task(nightly_cleanup())

    session_str = TG_SESSION or ""
    session = StringSession(session_str or None)

    client = TelegramClient(session, API_ID, API_HASH)
    await client.start()

    if not session_str:
        new_string = client.session.save()
        print("\n===========  IMPORTANT  ==========")
        print("Session created. Add this string to your .env:\n")
        print(f"TG_SESSION={new_string}\n")
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
        paths: List[Path] = []
        for msg in event.messages:
            saved = await save_media(msg)
            if saved:
                path, url = saved
                paths.append(path)
                urls.append(url)

        text = event.text or ""
        if not text and not urls:
            return

        try:
            if len(urls) > 1:
                collage_path = make_collage(paths)
                base = MEDIA_BASE_URL.rstrip("/")
                collage_url = f"{base}{MEDIA_ROUTE}/{collage_path.name}"
                await send_whatsapp(
                    text,
                    [collage_url],
                    twilio_client=twilio,
                    wa_from=WA_FROM,
                    wa_targets=WA_TARGETS,
                    text_tpl=TEXT_TPL,
                    img_tpl=IMG_TPL,
                    max_body=MAX_BODY,
                    exc_cls=TwilioRestException,
                )
            elif urls:
                await send_whatsapp(
                    text,
                    [urls[0]],
                    twilio_client=twilio,
                    wa_from=WA_FROM,
                    wa_targets=WA_TARGETS,
                    text_tpl=TEXT_TPL,
                    img_tpl=IMG_TPL,
                    max_body=MAX_BODY,
                    exc_cls=TwilioRestException,
                )
            else:
                await send_whatsapp(
                    text,
                    None,
                    twilio_client=twilio,
                    wa_from=WA_FROM,
                    wa_targets=WA_TARGETS,
                    text_tpl=TEXT_TPL,
                    img_tpl=IMG_TPL,
                    max_body=MAX_BODY,
                    exc_cls=TwilioRestException,
                )
            logging.info("Forwarded album with %d images", len(urls))
        except Exception as e:
            logging.error("Twilio error: %s", e)

    print("👂  Listening for messages …")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())

