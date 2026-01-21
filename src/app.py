import asyncio
import logging
import os
import tempfile

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = os.getenv("BOT_TOKEN")
VOICE_TYPE_URL = os.getenv("VOICE_TYPE_URL", "http://host.docker.internal:9876")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


async def transcribe(file_id: str) -> dict | None:
    file = await bot.get_file(file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await bot.download_file(file.file_path, tmp)
        tmp_path = tmp.name

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with open(tmp_path, "rb") as f:
                r = await client.post(
                    f"{VOICE_TYPE_URL}/transcribe",
                    files={"file": ("audio.ogg", f, "audio/ogg")},
                )
        if r.status_code == 200:
            return r.json()
    finally:
        os.unlink(tmp_path)
    return None


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("👋 Отправь голосовое или аудио — транскрибирую в текст.")


@dp.message(Command("ping"))
async def cmd_ping(message: Message):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{VOICE_TYPE_URL}/health")
        if r.status_code == 200:
            d = r.json()
            await message.answer(f"✅ Online | {d.get('model_size')} | uptime {d.get('uptime', 0):.0f}s")
        else:
            await message.answer("⚠️ Ошибка")
    except Exception:
        await message.answer("❌ Недоступен")


@dp.message(F.voice | F.audio | F.video_note | F.video | F.document)
async def handle_audio(message: Message):
    entity = message.voice or message.audio or message.video_note or message.video or message.document
    if not entity:
        return

    msg = await message.reply("🎤 ...")

    try:
        data = await transcribe(entity.file_id)
        if data and data.get("text"):
            text = data["text"].strip()
            lang = data.get("language", "?")
            dur = data.get("audio_duration", 0)
            await msg.edit_text(f"{text}\n\n<i>🌐 {lang} | ⏱ {dur:.1f}s</i>")
        else:
            await msg.edit_text("🤷 Пусто")
    except httpx.ConnectError:
        await msg.edit_text("❌ Сервис недоступен")
    except Exception as e:
        logger.exception("Error")
        await msg.edit_text(f"❌ {e}")


async def main():
    logger.info(f"Starting bot, voice-type: {VOICE_TYPE_URL}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
