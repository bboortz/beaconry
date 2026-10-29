---
name: telegram-patterns
description: Telegram bot patterns for beaconry — python-telegram-bot v20 async, notification format, callbacks
metadata:
  type: reference
---

## Setup

Create bot via @BotFather. Required env vars:
```
TELEGRAM_BOT_TOKEN=<token from BotFather>
TELEGRAM_CHAT_ID=<your chat ID — get via @userinfobot>
```

## python-telegram-bot v20 async pattern

Run alongside FastAPI by starting polling in the FastAPI lifespan:

```python
from telegram.ext import Application, CallbackQueryHandler

@asynccontextmanager
async def lifespan(app: FastAPI):
    tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CallbackQueryHandler(on_button_click))
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    yield
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
```

`updater.start_polling()` is non-blocking — starts a background task in the same event loop as FastAPI.

## Notification format

All four elements always (per design-decisions.md):

```python
def format_finding(f: Finding) -> str:
    emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡"}.get(f.severity, "⚪")
    return (
        f"{emoji} *{f.severity} CVE — {f.cve_id}*\n"
        f"CVSS: `{f.cvss:.1f}` · Host: `{f.host}`\n"
        f"Target: `{f.source_ref}` ({f.source_type})\n"
        f"Package: `{f.package} {f.version}` → fixed in `{f.fixed_version}`\n"
        f"\n_{f.description[:160]}_\n\n"
        f"`{fix_command}`"
    )
```

## Inline keyboard for findings

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🔧 Fix now", callback_data=f"fix:{finding.id}"),
        InlineKeyboardButton("⏰ Snooze 4h", callback_data=f"snooze:{finding.id}:4"),
    ],
    [InlineKeyboardButton("✅ Accept risk", callback_data=f"ack:{finding.id}")],
])

await bot.send_message(
    chat_id=TELEGRAM_CHAT_ID,
    text=text,
    parse_mode="Markdown",
    reply_markup=keyboard,
)
```

## Callback handler

```python
async def on_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()                      # acknowledge the tap immediately

    parts = query.data.split(":")
    action, finding_id = parts[0], parts[1]

    # edit the original message with progress/result
    await query.edit_message_text("⏳ Working…", parse_mode="Markdown")
    # ... do work ...
    await query.edit_message_text("✅ Done", parse_mode="Markdown")
```

`edit_message_text` replaces the inline keyboard — removes buttons after action.

## Sending from outside the handler (e.g. from FastAPI endpoint)

Access `tg_app.bot` directly (set as a module-level variable during lifespan):
```python
await tg_app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="...", parse_mode="Markdown")
```

## Markdown v1 vs v2

Use `parse_mode="Markdown"` (v1) for simplicity. v1 supports `*bold*`, `_italic_`, `` `code` ``.
v2 requires escaping `.`, `!`, `-`, `(`, `)`, etc. — avoid in the POC.

Special chars in v1 that must be escaped: `*`, `_`, `` ` ``, `[`.
Escape with `\` or wrap in backticks.

## Finding deduplication

Generate a stable ID per finding to avoid re-notifying on repeat scans:
```python
import hashlib
finding_id = hashlib.sha256(
    f"{host}:{cve_id}:{source_ref}:{package}".encode()
).hexdigest()[:16]
```

Store in SQLite. `INSERT OR IGNORE` — only notify when the row is genuinely new.
