import os
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

DB_PATH = "bot.db"
router = Router()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MOD_CHAT_ID = int(os.getenv("MOD_CHAT_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS submissions(
        mod_msg_id INTEGER PRIMARY KEY,
        user_chat_id INTEGER NOT NULL,
        user_msg_id INTEGER NOT NULL,
        channel_msg_id INTEGER,
        status TEXT NOT NULL DEFAULT 'new'
    )""")
    cur = conn.execute("SELECT value FROM settings WHERE key='mode'")
    if cur.fetchone() is None:
        conn.execute("INSERT INTO settings(key,value) VALUES('mode','moderation')")
        conn.commit()
    return conn

def get_mode() -> str:
    with db() as conn:
        return conn.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0]

def set_mode(mode: str):
    with db() as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='mode'", (mode,))
        conn.commit()

def save_submission(mod_msg_id: int, user_chat_id: int, user_msg_id: int, channel_msg_id: int | None):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO submissions(mod_msg_id,user_chat_id,user_msg_id,channel_msg_id,status) VALUES(?,?,?,?,?)",
            (mod_msg_id, user_chat_id, user_msg_id, channel_msg_id, "new")
        )
        conn.commit()

def get_submission(mod_msg_id: int):
    with db() as conn:
        row = conn.execute(
            "SELECT user_chat_id,user_msg_id,channel_msg_id,status FROM submissions WHERE mod_msg_id=?",
            (mod_msg_id,)
        ).fetchone()
    if not row:
        return None
    return {"user_chat_id": row[0], "user_msg_id": row[1], "channel_msg_id": row[2], "status": row[3]}

def set_status(mod_msg_id: int, status: str):
    with db() as conn:
        conn.execute("UPDATE submissions SET status=? WHERE mod_msg_id=?", (status, mod_msg_id))
        conn.commit()

def kb_for_mod(mod_msg_id: int, mode: str, has_channel_post: bool):
    kb = InlineKeyboardBuilder()
    if mode == "moderation":
        kb.button(text="✅ Опубликовать", callback_data=f"pub:{mod_msg_id}")
        kb.button(text="❌ Отклонить", callback_data=f"rej:{mod_msg_id}")
        kb.adjust(2)
    kb.button(text="✉️ Ответить автору", callback_data=f"rpl:{mod_msg_id}")
    if has_channel_post:
        kb.button(text="🗑 Удалить из канала", callback_data=f"del:{mod_msg_id}")
    kb.button(text=f"⚙️ Режим: {('АВТО' if mode=='auto' else 'МОДЕРАЦИЯ')}", callback_data="mode:toggle")
    kb.adjust(1)
    return kb.as_markup()

@router.message(F.text.startswith("/start"))
async def start(message: Message):
    await message.answer(
        "Привет! Это предложка «Подслушано Лицей 1».\n\n"
        "Пришли сплетню/новость текстом или файлом:\n"
        "• фото • видео • кружок • голосовое\n\n"
        "После отправки я отвечу «Спасибо за сплетню» 🙂"
    )

@router.message(F.text == "/mode")
async def mode_cmd(message: Message):
    if message.chat.id != MOD_CHAT_ID:
        await message.answer("Команда доступна только в чате модерации.")
        return
    mode = get_mode()
    await message.answer(
        f"Текущий режим: {'АВТО' if mode=='auto' else 'МОДЕРАЦИЯ'}\n"
        "Нажми кнопку ниже, чтобы переключить.",
        reply_markup=kb_for_mod(0, mode, False)
    )

@router.callback_query(F.data == "mode:toggle")
async def toggle_mode(call: CallbackQuery):
    if call.message.chat.id != MOD_CHAT_ID:
        await call.answer("Только для модерации", show_alert=True)
        return
    mode = get_mode()
    new_mode = "auto" if mode == "moderation" else "moderation"
    set_mode(new_mode)
    await call.answer(f"Режим теперь: {'АВТО' if new_mode=='auto' else 'МОДЕРАЦИЯ'}")

@router.message()
async def handle_any(message: Message, bot: Bot):
    # Ответ модератора автору (reply в чате модерации)
    if message.chat.id == MOD_CHAT_ID and message.reply_to_message:
        sub = get_submission(message.reply_to_message.message_id)
        if sub:
            await bot.copy_message(
                chat_id=sub["user_chat_id"],
                from_chat_id=MOD_CHAT_ID,
                message_id=message.message_id
            )
            await message.reply("✅ Отправлено автору.")
            return

    # Приём предложек только из лички бота
    if message.chat.type != "private":
        return

    mode = get_mode()

    channel_msg_id = None
    if mode == "auto":
        posted = await bot.copy_message(CHANNEL_ID, message.chat.id, message.message_id)
        channel_msg_id = posted.message_id

    mod_copy = await bot.copy_message(MOD_CHAT_ID, message.chat.id, message.message_id)
    save_submission(mod_copy.message_id, message.chat.id, message.message_id, channel_msg_id)

    await bot.edit_message_reply_markup(
        chat_id=MOD_CHAT_ID,
        message_id=mod_copy.message_id,
        reply_markup=kb_for_mod(mod_copy.message_id, mode, has_channel_post=(channel_msg_id is not None))
    )

    await message.answer("Спасибо за сплетню! ✅")

@router.callback_query(F.data.startswith("pub:"))
async def publish(call: CallbackQuery, bot: Bot):
    if call.message.chat.id != MOD_CHAT_ID:
        await call.answer("Только для модерации", show_alert=True); return
    mod_msg_id = int(call.data.split(":")[1])
    sub = get_submission(mod_msg_id)
    if not sub:
        await call.answer("Не нашёл запись.", show_alert=True); return

    posted = await bot.copy_message(CHANNEL_ID, MOD_CHAT_ID, mod_msg_id)

    with db() as conn:
        conn.execute("UPDATE submissions SET channel_msg_id=?, status=? WHERE mod_msg_id=?",
                     (posted.message_id, "published", mod_msg_id))
        conn.commit()

    await call.answer("Опубликовано ✅")
    mode = get_mode()
    await bot.edit_message_reply_markup(
        chat_id=MOD_CHAT_ID,
        message_id=mod_msg_id,
        reply_markup=kb_for_mod(mod_msg_id, mode, has_channel_post=True)
    )

@router.callback_query(F.data.startswith("rej:"))
async def reject(call: CallbackQuery):
    if call.message.chat.id != MOD_CHAT_ID:
        await call.answer("Только для модерации", show_alert=True); return
    mod_msg_id = int(call.data.split(":")[1])
    if not get_submission(mod_msg_id):
        await call.answer("Не нашёл запись.", show_alert=True); return
    set_status(mod_msg_id, "rejected")
    await call.answer("Отклонено ❌")

@router.callback_query(F.data.startswith("rpl:"))
async def reply_hint(call: CallbackQuery):
    if call.message.chat.id != MOD_CHAT_ID:
        await call.answer("Только для модерации", show_alert=True); return
    await call.message.reply("✉️ Ответь *реплаем* на предложку — я отправлю твой ответ автору.")
    await call.answer("Ок")

@router.callback_query(F.data.startswith("del:"))
async def delete_from_channel(call: CallbackQuery, bot: Bot):
    if call.message.chat.id != MOD_CHAT_ID:
        await call.answer("Только для модерации", show_alert=True); return
    mod_msg_id = int(call.data.split(":")[1])
    sub = get_submission(mod_msg_id)
    if not sub or not sub["channel_msg_id"]:
        await call.answer("Поста в канале нет.", show_alert=True); return
    try:
        await bot.delete_message(CHANNEL_ID, sub["channel_msg_id"])
        set_status(mod_msg_id, "deleted")
        await call.answer("Удалено из канала 🗑")
    except Exception:
        await call.answer("Не смог удалить (проверь права бота в канале).", show_alert=True)

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN в переменных окружения")
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
