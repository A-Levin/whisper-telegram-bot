import asyncio
import html
import logging
import os
import tempfile

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = os.getenv("BOT_TOKEN")
VOICE_TYPE_URL = os.getenv("VOICE_TYPE_URL", "http://host.docker.internal:9876")
TELEGRAM_LIMIT = 4096
FLOOD_RETRIES = 5

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
        async with httpx.AsyncClient(timeout=300.0) as client:
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


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_text(text: str, limit: int) -> list[str]:
    chunks = []
    while utf16_len(text) > limit:
        fit = min(len(text), limit)
        while utf16_len(text[:fit]) > limit:
            fit -= (utf16_len(text[:fit]) - limit + 1) // 2
        cut = max(text.rfind(" ", 0, fit + 1), text.rfind("\n", 0, fit + 1))
        if cut <= 0:
            cut = fit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks


async def send_waiting_flood(send):
    for attempt in range(FLOOD_RETRIES):
        try:
            return await send()
        except TelegramRetryAfter as e:
            if attempt == FLOOD_RETRIES - 1:
                raise
            await asyncio.sleep(e.retry_after)


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
    delivered = False

    try:
        data = await transcribe(entity.file_id)
        text = ((data or {}).get("text") or "").strip()
        if text:
            lang = data.get("language", "?")
            dur = data.get("audio_duration", 0)
            caption = f"🌐 {lang} | ⏱ {dur:.1f}s"
            chunks = [
                html.escape(c, quote=False)
                for c in split_text(text, TELEGRAM_LIMIT - 2 - utf16_len(caption))
            ]
            chunks[-1] += f"\n\n<i>{html.escape(caption, quote=False)}</i>"
            await send_waiting_flood(lambda: msg.edit_text(chunks[0]))
            delivered = True
            for chunk in chunks[1:]:
                await send_waiting_flood(lambda: message.answer(chunk))
        else:
            await msg.edit_text("🤷 Пусто")
    except httpx.ConnectError:
        await msg.edit_text("❌ Сервис недоступен")
    except httpx.ReadTimeout:
        await msg.edit_text("⏱ Таймаут — файл слишком длинный, попробуй ещё раз")
    except Exception as e:
        logger.exception("Error")
        error = f"❌ {html.escape(str(e), quote=False)}"
        if delivered:
            await message.answer(error)
        else:
            await msg.edit_text(error)


async def main():
    logger.info(f"Starting bot, voice-type: {VOICE_TYPE_URL}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
