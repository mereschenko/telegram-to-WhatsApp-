import asyncio
import json
import logging
import re
from typing import List, Sequence, Union, Optional, Type

from telethon import types
from twilio.base.exceptions import TwilioRestException


def split_csv(raw: str) -> List[str]:
    """Split a comma-separated string into a list of trimmed values."""
    return [p.strip() for p in raw.split(',') if p.strip()]


def parse_ids(raw: str) -> List[Union[int, str]]:
    """Parse numeric IDs or usernames from a comma-separated string."""
    out: List[Union[int, str]] = []
    for x in split_csv(raw):
        out.append(int(x) if re.fullmatch(r"-?\d+", x) else x.lstrip('@'))
    return out


def sender_matches(
    sender: types.User,
    sender_id: int,
    bot_ids: Sequence[Union[int, str]],
) -> bool:
    """Return True if the sender matches any ID or username in bot_ids."""
    if not bot_ids:
        return True
    if sender_id in bot_ids:
        return True
    if sender and getattr(sender, 'username', None) and sender.username in bot_ids:
        return True
    return False


async def send_whatsapp(
    body: str,
    media_urls: Optional[Sequence[str]] = None,
    *,
    twilio_client,
    wa_from: str,
    wa_targets: Sequence[str],
    text_tpl: Optional[str] = None,
    img_tpl: Optional[str] = None,
    max_body: int = 1024,
    exc_cls: Type[Exception] = TwilioRestException,
) -> None:
    """Send a WhatsApp message via Twilio."""
    logging.info("Sending WhatsApp: body=%r media_urls=%r", body, media_urls)
    loop = asyncio.get_running_loop()

    async def _send(to_num: str):
        logging.info("Sending to %s", to_num)

        def _create(template: bool = False):
            urls = list(media_urls or [])
            kwargs = {"from_": wa_from, "to": to_num}
            if template:
                tpl = img_tpl if urls else text_tpl
                if not tpl:
                    return None
                kwargs["content_sid"] = tpl
                kwargs["content_variables"] = json.dumps({"1": body[:max_body]})
                if urls:
                    kwargs["media_url"] = urls[:1]
            else:
                kwargs["body"] = body
                if urls:
                    kwargs["media_url"] = urls[:1]
            return twilio_client.messages.create(**kwargs)

        try:
            await loop.run_in_executor(None, _create)
            logging.info("Sent to %s", to_num)
        except exc_cls as e:
            logging.exception("Twilio error sending to %s", to_num)
            if getattr(e, "code", None) == 63016 and (text_tpl or img_tpl):
                try:
                    await loop.run_in_executor(None, lambda: _create(True))
                    logging.info("Sent to %s via template", to_num)
                except exc_cls:
                    logging.exception("Twilio template error sending to %s", to_num)

    await asyncio.gather(*(_send(n) for n in wa_targets))
