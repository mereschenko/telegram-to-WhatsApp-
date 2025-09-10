# telegram-to-WhatsApp
Forward Telegram messages to WhatsApp

## Prerequisites

- **Python** with `pip` installed
- A Twilio account with access to the WhatsApp Sandbox
  (Twilio numbers must be written as `whatsapp:+123456789`)

## Installing dependencies

Use `pip` for the Python listener. The `requirements.txt` file pins the
exact versions that have been tested:

```bash
pip install -r requirements.txt
```
The file currently specifies:
- `Telethon==1.40.0`
- `python-dotenv==1.1.1`
- `pytest==8.4.1`
- `pytest-asyncio==1.1.0`
- `twilio==9.7.0`
- `requests==2.31.0`
- `aiohttp==3.9.5`
- `Pillow==10.3.0`

## Configuration

Copy `.env.example` to `.env` and fill in the values for your Telegram and Twilio credentials:

```bash
cp .env.example .env
# edit .env in your favourite editor
```

The `.env` file defines the following variables:

- `TG_API_ID` and `TG_API_HASH` – your Telegram API credentials
- `TG_SESSION` – the Telethon string session
 - `BOT_IDS` – optional comma-separated list of allowed bot IDs or usernames
- `TG_GROUP_IDS` – comma-separated list of source chat IDs
- `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` – your Twilio credentials
- `TWILIO_WHATSAPP_FROM` – the Twilio number to send from (in `whatsapp:+123456789` format)
- `TWILIO_WHATSAPP_TO` – comma-separated list of WhatsApp recipient numbers (same format)
New variables `BOT_IDS` and `TG_GROUP_IDS` let you limit forwarding to certain
bots and chats.

### Obtaining the values

* **TG_API_ID** and **TG_API_HASH** – create an application at
  [my.telegram.org](https://my.telegram.org) and copy the generated *API ID* and
  *API Hash* from the "API development tools" section.
* **TG_SESSION** – generate a Telethon string session using the snippet below.
  Install Telethon first with `pip install telethon`, then run the code and log
  in with your API credentials. Paste the printed string into `.env`.

  ```python
  from telethon.sync import TelegramClient
  from telethon.sessions import StringSession

  with TelegramClient(StringSession(), TG_API_ID, TG_API_HASH) as client:
      print(client.session.save())
  ```
* **BOT_IDS** – optional IDs or usernames of Telegram bots whose messages
  should be forwarded. Mix numeric IDs and usernames (without `@`) as needed.
  You can obtain the ID of a bot via `tg://user?id=<bot_id>` or with
  `@userinfobot`.
* **TG_GROUP_IDS** – comma-separated list of source chat IDs or usernames.
  Use a tool like `@username_to_id_bot` or Telethon's `get_peer_id` to retrieve
  the numeric IDs of groups or channels.
* **TWILIO_ACCOUNT_SID** and **TWILIO_AUTH_TOKEN** – sign in to [Twilio](https://www.twilio.com/), open the Console and copy the "Account SID" and "Auth Token".
* **TWILIO_WHATSAPP_FROM** – the WhatsApp-enabled Twilio number (or sandbox number) in the `whatsapp:+123456789` format.
* **TWILIO_WHATSAPP_TO** – comma-separated list of your personal WhatsApp numbers in the same format. Make sure each number is joined to your Twilio Sandbox.
* **TWILIO_TEMPLATE_SID** – optional SID of a pre-approved WhatsApp template.
* **TWILIO_MEDIA_TEMPLATE_SID** – SID of a template used when sending media.
* **MEDIA_PORT** – local port used by `telegram_to_whatsapp.py` to serve
  downloaded media (default `8080`).
 * **MEDIA_BASE_URL** – public base URL where that media server can be reached
  by Twilio. When running locally, expose `MEDIA_PORT` with a tool such as
  [ngrok](https://ngrok.com/) or a Cloudflare Tunnel and set this variable to
  the resulting URL. The value must start with `http://` or `https://` and
  **cannot** point to `localhost`.
* **MEDIA_ROUTE** – path segment where downloaded files are served (default
  `/media`).

When `telegram_to_whatsapp.py` is used, it starts an HTTP server on
`MEDIA_PORT` to serve downloaded files. Twilio retrieves those files from
`MEDIA_BASE_URL` joined with `MEDIA_ROUTE`, so the URL must be publicly
reachable. If you're running the script on your own machine, run `ngrok http
$MEDIA_PORT` (or an equivalent tunnelling service) and copy the HTTPS URL into
`MEDIA_BASE_URL`.

WhatsApp only allows free-form messages to users who have contacted you within the last 24 hours. Outside this window you must use a pre-approved template. If you send a non-template message, Twilio will return error **63016**, indicating that a template is required. See [Twilio's documentation](https://www.twilio.com/docs/errors/63016) for more details.

## Launching

Two scripts can forward Telegram messages:

- `listener.py` uploads any attached media to **file.io**, which removes the files after one week. This variant requires no public HTTP server and works well for forwarding plain text or short-lived media.
- `telegram_to_whatsapp.py` saves photos and documents under `Img/` and serves them via a local aiohttp server on `MEDIA_PORT`. Twilio fetches the files from `MEDIA_BASE_URL` combined with `MEDIA_ROUTE`, so the URL must be reachable from the internet. A nightly cleanup task removes files older than 24 hours.
When a Telegram album has multiple images, the script stitches them into a single collage and sends just one WhatsApp message.

For most setups simply run:

```bash
python listener.py
```

Use `telegram_to_whatsapp.py` only if you need to host media yourself and can expose the local server publicly.

Twilio's official Python SDK is fully synchronous. The listener therefore wraps the `twilio_client.messages.create` call in `loop.run_in_executor` so that the Telethon event handler remains non-blocking. If you adapt the code and need non-blocking behaviour, use the same pattern (see `listener.py` lines 83–91).


## Running tests

The repository includes unit tests for the listener. Install the development
dependencies with `pip` and then invoke `pytest`:

```bash
pip install -r requirements.txt
pytest
```

The tests mock Twilio and Telegram so no real credentials are required.

## Troubleshooting

### Twilio error 21620: "Invalid media URL(s)"

Twilio must be able to download any media you attach to a WhatsApp message. If
you see an error like the following when running `telegram_to_whatsapp.py`:

```
Unable to create record: Invalid media URL(s)
```

your local media server is not reachable from the internet. Expose the server
on a public address (for example with `ngrok http $MEDIA_PORT` or a Cloudflare
Tunnel) and set `MEDIA_BASE_URL` in `.env` to the resulting **HTTPS** URL so that
Twilio can fetch your files.


## License

This project is licensed under the [MIT License](LICENSE).
