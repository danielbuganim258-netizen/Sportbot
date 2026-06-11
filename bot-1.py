import os
import asyncio
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat
import anthropic

# ── הגדרות ──────────────────────────────────────────────
API_ID       = int(os.environ["TELEGRAM_API_ID"])
API_HASH     = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_ID     = int(os.environ["OWNER_TELEGRAM_ID"])   # ה-ID שלך
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

# ── לקוחות ──────────────────────────────────────────────
user_client = TelegramClient("user_session", API_ID, API_HASH)
bot_client  = TelegramClient("bot_session",  API_ID, API_HASH)
ai          = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── זיכרון קצר מועד ─────────────────────────────────────
pending_messages: dict[int, list[str]] = {}   # chat_id -> [הודעות]
SUMMARY_EVERY = 10                             # לסכם כל X הודעות

# ── Claude: מסנן חשיבות ─────────────────────────────────
def is_important(text: str) -> bool:
    """מחזיר True אם ההודעה חשובה (תוצאה, העברה, חדשות גדולות)."""
    try:
        resp = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=10,
            system=(
                "אתה מסנן הודעות ספורט. "
                "ענה אך ורק YES אם ההודעה מכילה: תוצאה סופית, גול, העברת שחקן, פציעה, "
                "השעיה, ביטול משחק או חדשות דחופות אחרות. "
                "אחרת ענה NO."
            ),
            messages=[{"role": "user", "content": text[:500]}],
        )
        return resp.content[0].text.strip().upper().startswith("YES")
    except Exception:
        return False

# ── Claude: תיוג נושא ────────────────────────────────────
def tag_message(text: str) -> str:
    """מחזיר תגית קצרה לנושא ההודעה."""
    try:
        resp = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=20,
            system=(
                "תן תגית אחת קצרה בעברית לנושא הודעת הספורט. "
                "דוגמאות: תוצאה | גול | העברה | פציעה | לוח משחקים | ספקולציה. "
                "ענה רק את התגית, ללא הסבר."
            ),
            messages=[{"role": "user", "content": text[:300]}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return "כללי"

# ── Claude: סיכום קבוצה ─────────────────────────────────
def summarize(chat_name: str, messages: list[str]) -> str:
    """מסכם רשימת הודעות מקבוצה."""
    try:
        joined = "\n".join(f"- {m}" for m in messages[-30:])
        resp = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system="אתה עוזר שמסכם עדכוני ספורט בעברית. היה תמציתי וברור.",
            messages=[{
                "role": "user",
                "content": f"סכם את ההודעות האחרונות מהקבוצה '{chat_name}':\n{joined}"
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"שגיאה בסיכום: {e}"

# ── מאזין להודעות נכנסות ────────────────────────────────
@user_client.on(events.NewMessage)
async def on_message(event):
    # רק מקבוצות / ערוצים (לא צ'אטים פרטיים)
    chat = await event.get_chat()
    if not isinstance(chat, (Channel, Chat)):
        return

    text = event.raw_text or ""
    if len(text) < 5:
        return

    chat_id   = event.chat_id
    chat_name = getattr(chat, "title", str(chat_id))

    # שמור להיסטוריה
    pending_messages.setdefault(chat_id, []).append(text)

    # ── בדוק חשיבות ──────────────────────────────────────
    if is_important(text):
        tag = tag_message(text)
        alert = (
            f"🔔 *התראה חשובה* מ־{chat_name}\n"
            f"🏷️ [{tag}]\n\n"
            f"{text[:600]}"
        )
        await bot_client.send_message(OWNER_ID, alert, parse_mode="markdown")

    # ── סיכום אוטומטי כל X הודעות ────────────────────────
    if len(pending_messages[chat_id]) >= SUMMARY_EVERY:
        msgs   = pending_messages.pop(chat_id)
        summary = summarize(chat_name, msgs)
        now    = datetime.now().strftime("%H:%M")
        report = (
            f"📋 *סיכום* מ־{chat_name} ({now})\n\n"
            f"{summary}"
        )
        await bot_client.send_message(OWNER_ID, report, parse_mode="markdown")

# ── פקודות הבוט ─────────────────────────────────────────
@bot_client.on(events.NewMessage(pattern="/summary"))
async def cmd_summary(event):
    if event.sender_id != OWNER_ID:
        return
    if not pending_messages:
        await event.respond("אין הודעות חדשות לסיכום כרגע.")
        return
    for chat_id, msgs in list(pending_messages.items()):
        if not msgs:
            continue
        try:
            chat = await user_client.get_entity(chat_id)
            name = getattr(chat, "title", str(chat_id))
        except Exception:
            name = str(chat_id)
        summary = summarize(name, msgs)
        pending_messages[chat_id] = []
        await event.respond(f"📋 *{name}*\n\n{summary}", parse_mode="markdown")

@bot_client.on(events.NewMessage(pattern="/groups"))
async def cmd_groups(event):
    if event.sender_id != OWNER_ID:
        return
    lines = []
    async for dialog in user_client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            count = len(pending_messages.get(dialog.id, []))
            lines.append(f"• {dialog.name} — {count} הודעות ממתינות")
    text = "📡 *הקבוצות שלך:*\n" + "\n".join(lines) if lines else "לא נמצאו קבוצות."
    await event.respond(text, parse_mode="markdown")

@bot_client.on(events.NewMessage(pattern="/help"))
async def cmd_help(event):
    if event.sender_id != OWNER_ID:
        return
    await event.respond(
        "🤖 *פקודות הסוכן:*\n\n"
        "/summary — סיכום של כל הקבוצות\n"
        "/groups — רשימת קבוצות + כמה הודעות ממתינות\n"
        "/help — עזרה",
        parse_mode="markdown"
    )

# ── הפעלה ────────────────────────────────────────────────
async def main():
    print("מתחבר...")
    await user_client.start()
    await bot_client.start(bot_token=BOT_TOKEN)
    print("הסוכן פעיל ✅")
    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected(),
    )

if __name__ == "__main__":
    import sys
    if sys.version_info >= (3, 10):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    else:
        asyncio.run(main())
