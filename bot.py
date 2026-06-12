import os
import asyncio
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat
import anthropic

API_ID        = int(os.environ["TELEGRAM_API_ID"])
API_HASH      = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_ID      = int(os.environ["OWNER_TELEGRAM_ID"])
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

ai = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
pending_messages: dict[int, list[str]] = {}
SUMMARY_EVERY = 10

def is_important(text: str) -> bool:
    try:
        resp = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=10,
            system="אתה מסנן הודעות ספורט. ענה YES אם יש תוצאה, גול, העברה, פציעה או חדשות דחופות. אחרת NO.",
            messages=[{"role": "user", "content": text[:500]}],
        )
        return resp.content[0].text.strip().upper().startswith("YES")
    except Exception:
        return False

def tag_message(text: str) -> str:
    try:
        resp = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=20,
            system="תן תגית אחת קצרה בעברית. דוגמאות: תוצאה | גול | העברה | פציעה. ענה רק את התגית.",
            messages=[{"role": "user", "content": text[:300]}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return "כללי"

def summarize(chat_name: str, messages: list[str]) -> str:
    try:
        joined = "\n".join(f"- {m}" for m in messages[-30:])
        resp = ai.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system="סכם עדכוני ספורט בעברית. היה תמציתי.",
            messages=[{"role": "user", "content": f"סכם הודעות מ'{chat_name}':\n{joined}"}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"שגיאה: {e}"

async def main():
    user_client = TelegramClient("user_session", API_ID, API_HASH)
    bot_client  = TelegramClient("bot_session",  API_ID, API_HASH)

    @user_client.on(events.NewMessage)
    async def on_message(event):
        try:
            chat = await event.get_chat()
            if not isinstance(chat, (Channel, Chat)):
                return
            text = event.raw_text or ""
            if len(text) < 5:
                return
            chat_id   = event.chat_id
            chat_name = getattr(chat, "title", str(chat_id))
            pending_messages.setdefault(chat_id, []).append(text)
            if is_important(text):
                tag   = tag_message(text)
                alert = f"🔔 *התראה* מ־{chat_name}\n🏷️ [{tag}]\n\n{text[:600]}"
                await bot_client.send_message(OWNER_ID, alert, parse_mode="markdown")
            if len(pending_messages[chat_id]) >= SUMMARY_EVERY:
                msgs    = pending_messages.pop(chat_id)
                summary = summarize(chat_name, msgs)
                now     = datetime.now().strftime("%H:%M")
                await bot_client.send_message(OWNER_ID, f"📋 *סיכום* מ־{chat_name} ({now})\n\n{summary}", parse_mode="markdown")
        except Exception as e:
            print(f"שגיאה בהודעה: {e}")

    @bot_client.on(events.NewMessage(pattern="/summary"))
    async def cmd_summary(event):
        if event.sender_id != OWNER_ID:
            return
        if not pending_messages:
            await event.respond("אין הודעות חדשות.")
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
                lines.append(f"• {dialog.name} — {count} הודעות")
        text = "📡 *קבוצות:*\n" + "\n".join(lines) if lines else "לא נמצאו קבוצות."
        await event.respond(text, parse_mode="markdown")

    @bot_client.on(events.NewMessage(pattern="/help"))
    async def cmd_help(event):
        if event.sender_id != OWNER_ID:
            return
        await event.respond("🤖 *פקודות:*\n/summary — סיכום\n/groups — קבוצות\n/help — עזרה", parse_mode="markdown")

    print("מתחבר...")
    await user_client.start()
    await bot_client.start(bot_token=BOT_TOKEN)
    print("הסוכן פעיל ✅")
    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected(),
    )

if __name__ == "__main__":
    asyncio.run(main())
