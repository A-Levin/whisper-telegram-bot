import asyncio
import datetime
import re
import time
from typing import Any

import httpx
import pytest
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteMessage, EditMessageText, GetFile, SendMessage, TelegramMethod
from aiogram.types import Chat, File, Message, Update, User, Voice

import app

TELEGRAM_LIMIT = 4096
CHAT = Chat(id=1001, type="private")
USER = User(id=1001, is_bot=False, first_name="User")
BOT_USER = User(id=42, is_bot=True, first_name="Bot")

ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span",
    "tg-spoiler", "a", "code", "pre", "blockquote", "tg-emoji",
}
TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)(\s[^<>]*)?>")
ENTITY_RE = re.compile(r"&(lt|gt|amp|quot|#[0-9]+|#x[0-9a-fA-F]+);")
NAMED = {"lt": "<", "gt": ">", "amp": "&", "quot": '"'}
ITALIC_TAGS = {"i", "em"}


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def parse_html(raw: str, method: TelegramMethod) -> tuple[str, str]:
    out = []
    italic = []
    depth = 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "<":
            m = TAG_RE.match(raw, i)
            if not m or m.group(1).lower() not in ALLOWED_TAGS:
                raise TelegramBadRequest(
                    method=method,
                    message=f"Bad Request: can't parse entities: unsupported start tag at byte offset {i}",
                )
            if m.group(1).lower() in ITALIC_TAGS:
                depth += -1 if raw[i + 1] == "/" else 1
            i = m.end()
            continue
        elif ch == ">":
            raise TelegramBadRequest(
                method=method, message=f"Bad Request: can't parse entities: unexpected '>' at {i}"
            )
        elif ch == "&":
            m = ENTITY_RE.match(raw, i)
            if not m:
                raise TelegramBadRequest(
                    method=method, message=f"Bad Request: can't parse entities: bare '&' at {i}"
                )
            name = m.group(1)
            if name in NAMED:
                ch = NAMED[name]
            elif name.startswith("#x"):
                ch = chr(int(name[2:], 16))
            else:
                ch = chr(int(name[1:]))
            i = m.end()
        else:
            i += 1
        out.append(ch)
        if depth > 0:
            italic.append(ch)
    return "".join(out), "".join(italic)


def italic_from_entities(text: str, entities) -> str:
    encoded = text.encode("utf-16-le")
    parts = []
    for e in entities or []:
        if e.type == "italic":
            parts.append(encoded[2 * e.offset : 2 * (e.offset + e.length)].decode("utf-16-le"))
    return "".join(parts)


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.next_id = 100
        self.messages: dict[int, str] = {}
        self.italic: dict[int, str] = {}
        self.text_calls = 0
        self.flood_at: dict[int, int] = {}
        self.fail_at: set[int] = set()
        self.slept = 0.0
        self.started = time.monotonic()
        self.blocked_until = 0.0
        self.flood_hits = 0

    def now(self) -> float:
        return self.slept + time.monotonic() - self.started

    def _faults(self, method, visible: str) -> None:
        if self.now() < self.blocked_until:
            self.flood_hits += 1
            if self.flood_hits > 50:
                raise AssertionError("bot keeps hitting flood control without waiting")
            wait = int(self.blocked_until - self.now()) + 1
            raise TelegramRetryAfter(
                method=method, message=f"Flood control exceeded. Retry in {wait} seconds", retry_after=wait
            )
        if visible.startswith("🎤"):
            return
        n = self.text_calls
        self.text_calls += 1
        if n in self.flood_at:
            wait = self.flood_at.pop(n)
            self.blocked_until = self.now() + wait
            raise TelegramRetryAfter(
                method=method, message=f"Flood control exceeded. Retry in {wait} seconds", retry_after=wait
            )
        if n in self.fail_at:
            self.fail_at.discard(n)
            raise TelegramBadRequest(method=method, message="Bad Request: message to send is invalid")

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):
        yield b"OggS fake voice payload"

    def _visible(self, bot, method) -> tuple[str, str]:
        parse_mode = method.parse_mode
        if isinstance(parse_mode, Default):
            parse_mode = bot.default[parse_mode.name]
        text = method.text
        if parse_mode is None:
            visible, italic = text, italic_from_entities(text, method.entities)
        elif str(getattr(parse_mode, "value", parse_mode)).upper() == "HTML":
            visible, italic = parse_html(text, method)
        else:
            raise AssertionError(f"test fake does not render parse_mode={parse_mode!r}")
        if not visible.strip():
            raise TelegramBadRequest(method=method, message="Bad Request: message text is empty")
        if u16(visible) > TELEGRAM_LIMIT:
            raise TelegramBadRequest(method=method, message="Bad Request: MESSAGE_TOO_LONG")
        self._faults(method, visible)
        return visible, italic

    def _message(self, message_id: int, text: str) -> Message:
        return Message(
            message_id=message_id,
            date=datetime.datetime.now(datetime.timezone.utc),
            chat=CHAT,
            from_user=BOT_USER,
            text=text,
        )

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            visible, italic = self._visible(bot, method)
            self.next_id += 1
            self.messages[self.next_id] = visible
            self.italic[self.next_id] = italic
            return self._message(self.next_id, visible).as_(bot)
        if isinstance(method, EditMessageText):
            visible, italic = self._visible(bot, method)
            assert method.message_id in self.messages, "edit of unknown message"
            self.messages[method.message_id] = visible
            self.italic[method.message_id] = italic
            return self._message(method.message_id, visible).as_(bot)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="voice-uniq", file_path="voice/file_0.oga").as_(bot)
        if isinstance(method, DeleteMessage):
            self.messages.pop(method.message_id, None)
            self.italic.pop(method.message_id, None)
            return True
        if method.__returning__ is bool:
            return True
        raise AssertionError(f"unexpected Telegram call in test: {type(method).__name__}")

    def chat(self) -> list[str]:
        return [self.messages[k] for k in sorted(self.messages)]

    def chat_italic(self) -> list[str]:
        return [self.italic[k] for k in sorted(self.italic)]


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(app.bot, "session", fake)

    async def virtual_sleep(delay, result=None):
        fake.slept += delay
        return result

    monkeypatch.setattr(asyncio, "sleep", virtual_sleep)
    return fake


async def deliver(monkeypatch, telegram, text, lang="ru", duration=12.5) -> list[str]:
    async def fake_transcribe(file_id):
        return {"text": text, "language": lang, "audio_duration": duration}

    return await deliver_with(monkeypatch, telegram, fake_transcribe, duration)


async def deliver_with(monkeypatch, telegram, fake_transcribe, duration=12.5) -> list[str]:
    monkeypatch.setattr(app, "transcribe", fake_transcribe)
    incoming = Message(
        message_id=1,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=CHAT,
        from_user=USER,
        voice=Voice(file_id="voice-file", file_unique_id="voice-uniq", duration=int(duration)),
    )
    await app.dp.feed_update(app.bot, Update(update_id=1, message=incoming))
    return telegram.chat()


def caption(lang, duration) -> str:
    return f"🌐 {lang} | ⏱ {duration}s"


def split_body(messages: list[str], cap: str) -> list[str]:
    assert all("❌" not in m for m in messages), messages
    assert sum(m.count("🌐") for m in messages) == 1, "caption must appear exactly once"
    assert messages[-1].rstrip().endswith(cap), messages[-1][-200:]
    bodies = list(messages)
    bodies[-1] = bodies[-1].rstrip()[: -len(cap)]
    return bodies


def norm(s: str) -> str:
    return " ".join(s.split())


def assert_within_limit(messages: list[str]) -> None:
    for n, m in enumerate(messages):
        assert u16(m) <= TELEGRAM_LIMIT, f"message #{n} has {u16(m)} UTF-16 units"


def numbered_words(count: int) -> str:
    return " ".join(f"слово{i:05d}" for i in range(count))


async def test_short_text_is_single_edited_message_with_caption(monkeypatch, telegram):
    text = "Привет, это короткое голосовое."
    messages = await deliver(monkeypatch, telegram, text, duration=12.5)

    assert len(messages) == 1
    assert messages[0].startswith(text)
    assert messages[0].rstrip().endswith(caption("ru", 12.5))
    assert "🎤" not in messages[0]
    assert "❌" not in messages[0]


async def test_medium_text_under_limit_is_not_split(monkeypatch, telegram):
    text = numbered_words(300)
    assert len(text) < 3600
    messages = await deliver(monkeypatch, telegram, text, duration=200.0)

    assert len(messages) == 1
    assert norm(split_body(messages, caption("ru", 200.0))[0]) == norm(text)


async def test_long_transcript_is_delivered_in_full_across_messages(monkeypatch, telegram):
    text = numbered_words(690)
    assert 7500 < len(text) < 8500
    messages = await deliver(monkeypatch, telegram, text, duration=1320.5)

    assert len(messages) >= 2
    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 1320.5))
    assert norm(" ".join(bodies)) == norm(text)


async def test_long_transcript_is_not_cut_inside_words(monkeypatch, telegram):
    text = numbered_words(1500)
    vocabulary = set(text.split())
    messages = await deliver(monkeypatch, telegram, text, duration=2700.0)

    assert len(messages) >= 4
    assert_within_limit(messages)
    for body in split_body(messages, caption("ru", 2700.0)):
        broken = [w for w in body.split() if w not in vocabulary]
        assert not broken, f"word cut in half: {broken[:3]}"


async def test_text_fitting_alone_but_not_with_caption_is_delivered(monkeypatch, telegram):
    text = numbered_words(372)
    assert TELEGRAM_LIMIT - 30 < len(text) <= TELEGRAM_LIMIT
    messages = await deliver(monkeypatch, telegram, text, duration=300.0)

    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 300.0))
    assert norm(" ".join(bodies)) == norm(text)


async def test_long_text_without_spaces_is_still_delivered(monkeypatch, telegram):
    text = "".join(chr(ord("а") + i % 32) for i in range(9000))
    messages = await deliver(monkeypatch, telegram, text, duration=600.0)

    assert len(messages) >= 3
    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 600.0))
    assert "".join(b.strip() for b in bodies) == text


async def test_html_special_chars_are_shown_verbatim(monkeypatch, telegram):
    text = 'если a < b && c > d, то <i>это не тег</i> & "кавычки" &amp; тоже текст'
    messages = await deliver(monkeypatch, telegram, text, duration=5.0)

    assert len(messages) == 1
    assert "❌" not in messages[0]
    assert messages[0].startswith(text)
    assert messages[0].rstrip().endswith(caption("ru", 5.0))


async def test_long_text_with_html_special_chars_is_delivered_verbatim(monkeypatch, telegram):
    text = " ".join(f"<{i:04d}>&" for i in range(1100))
    assert len(text) > 2 * TELEGRAM_LIMIT
    messages = await deliver(monkeypatch, telegram, text, duration=900.0)

    assert len(messages) >= 3
    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 900.0))
    assert norm(" ".join(bodies)) == norm(text)


async def test_empty_transcript_still_reports_empty(monkeypatch, telegram):
    messages = await deliver(monkeypatch, telegram, "")

    assert len(messages) == 1
    assert "🤷" in messages[0]


async def test_long_html_special_chars_without_spaces_are_delivered_verbatim(monkeypatch, telegram):
    text = "a<b&c>d" * 1500
    messages = await deliver(monkeypatch, telegram, text, duration=900.0)

    assert len(messages) >= 3
    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 900.0))
    assert "".join(b.strip() for b in bodies) == text


def ascii_text(length: int) -> str:
    text = ("abcdefg " * (length // 8 + 2))[:length]
    return text[:-1] + "z" if text.endswith(" ") else text


SHORT_CAPTION = caption("ru", 12.5)
CAPTION_BUDGET = TELEGRAM_LIMIT - 2 - u16(SHORT_CAPTION)


async def test_short_reply_is_text_blank_line_and_italic_caption(monkeypatch, telegram):
    text = "Привет, это короткое голосовое."
    messages = await deliver(monkeypatch, telegram, text, duration=12.5)

    assert messages == [f"{text}\n\n{SHORT_CAPTION}"]
    assert telegram.chat_italic() == [SHORT_CAPTION]


async def test_text_exactly_filling_one_message_with_caption_stays_single(monkeypatch, telegram):
    text = ascii_text(CAPTION_BUDGET)
    messages = await deliver(monkeypatch, telegram, text, duration=12.5)

    assert messages == [f"{text}\n\n{SHORT_CAPTION}"]
    assert u16(messages[0]) == TELEGRAM_LIMIT


async def test_text_one_utf16_unit_over_is_split_into_two(monkeypatch, telegram):
    text = ascii_text(CAPTION_BUDGET + 1)
    messages = await deliver(monkeypatch, telegram, text, duration=12.5)

    assert len(messages) == 2
    assert_within_limit(messages)
    bodies = split_body(messages, SHORT_CAPTION)
    assert norm(" ".join(bodies)) == norm(text)


async def test_single_word_one_utf16_unit_over_is_split_into_two(monkeypatch, telegram):
    text = "a" * (CAPTION_BUDGET + 1)
    messages = await deliver(monkeypatch, telegram, text, duration=12.5)

    assert len(messages) == 2
    assert_within_limit(messages)
    bodies = split_body(messages, SHORT_CAPTION)
    assert "".join(b.strip() for b in bodies) == text


async def test_words_separated_only_by_newlines_are_not_cut(monkeypatch, telegram):
    text = "\n".join(f"слово{i:05d}" for i in range(1500))
    vocabulary = set(text.split())
    messages = await deliver(monkeypatch, telegram, text, duration=2700.0)

    assert len(messages) >= 4
    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 2700.0))
    for body in bodies:
        broken = [w for w in body.split() if w not in vocabulary]
        assert not broken, f"word cut in half: {broken[:3]}"
    assert norm(" ".join(bodies)) == norm(text)


@pytest.mark.parametrize("chunk", [0, 1, 2])
async def test_flood_control_on_any_chunk_is_waited_out(monkeypatch, telegram, chunk):
    text = numbered_words(1500)
    telegram.flood_at = {chunk: 7}
    messages = await deliver(monkeypatch, telegram, text, duration=2700.0)

    assert telegram.slept >= 7
    assert len(messages) >= 4
    assert_within_limit(messages)
    bodies = split_body(messages, caption("ru", 2700.0))
    assert norm(" ".join(bodies)) == norm(text)


async def test_unrecoverable_error_on_later_chunk_keeps_delivered_chunks(monkeypatch, telegram):
    text = numbered_words(1500)
    telegram.fail_at = {1}
    messages = await deliver(monkeypatch, telegram, text, duration=2700.0)

    errors = [n for n, m in enumerate(messages) if "❌" in m]
    assert len(errors) == 1, messages
    assert errors[0] > 0, "first chunk was overwritten by the error"
    first = messages[0]
    assert first.split() and norm(text).startswith(norm(first)), first[:200]
    assert first.split()[-1] in set(text.split())


async def test_whitespace_only_transcript_reports_empty(monkeypatch, telegram):
    messages = await deliver(monkeypatch, telegram, "   \n ")

    assert len(messages) == 1
    assert "🤷" in messages[0]
    assert "🌐" not in messages[0]


async def test_error_text_with_html_chars_is_shown_verbatim(monkeypatch, telegram):
    detail = 'boom <tag> & "quoted" &amp;'

    async def failing_transcribe(file_id):
        raise RuntimeError(detail)

    messages = await deliver_with(monkeypatch, telegram, failing_transcribe)

    assert not any("🎤" in m for m in messages), messages
    assert any("❌" in m and detail in m for m in messages), messages


@pytest.mark.parametrize(
    "text",
    [
        "😀a" * 5000,
        "😀a" * 2000,
        " ".join(f"😀слово{i:04d}🎉" for i in range(1200)),
    ],
    ids=["no_spaces_long", "no_spaces_under_codepoint_limit", "with_spaces"],
)
async def test_text_with_non_bmp_chars_is_delivered_in_full(monkeypatch, telegram, text):
    messages = await deliver(monkeypatch, telegram, text, duration=900.0)

    assert len(messages) >= 2
    assert_within_limit(messages)
    for m in messages:
        m.encode("utf-16-le")
    bodies = split_body(messages, caption("ru", 900.0))
    if " " in text:
        assert norm(" ".join(bodies)) == norm(text)
    else:
        assert "".join(b.strip() for b in bodies) == text


TIMEOUT_TEXT = "⏱ Таймаут — файл слишком длинный, попробуй ещё раз"


@pytest.fixture
def voice_type(monkeypatch):
    calls = []

    async def handle(transport, request):
        calls.append(request)
        if calls_behaviour["timeout"]:
            raise httpx.ReadTimeout("timed out", request=request)
        if calls_behaviour["connect"]:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(
            200, json={"text": "Распознанный текст.", "language": "ru", "audio_duration": 1332.0}, request=request
        )

    calls_behaviour = {"timeout": False, "connect": False}
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return calls, calls_behaviour


async def feed_voice(duration=1332):
    incoming = Message(
        message_id=1,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=CHAT,
        from_user=USER,
        voice=Voice(file_id="voice-file", file_unique_id="voice-uniq", duration=duration),
    )
    await app.dp.feed_update(app.bot, Update(update_id=1, message=incoming))


async def test_voice_type_request_waits_at_least_300_seconds(telegram, voice_type):
    calls, _ = voice_type
    await feed_voice()

    transcribe_calls = [r for r in calls if r.url.path.endswith("/transcribe")]
    assert len(transcribe_calls) == 1, [str(r.url) for r in calls]
    read_timeout = transcribe_calls[0].extensions["timeout"]["read"]
    assert read_timeout is None or read_timeout >= 300, f"read timeout is {read_timeout}s"
    assert telegram.chat()[0].startswith("Распознанный текст.")


async def test_voice_type_read_timeout_shows_timeout_message(telegram, voice_type):
    _, behaviour = voice_type
    behaviour["timeout"] = True
    await feed_voice()

    assert telegram.chat() == [TIMEOUT_TEXT]


async def test_voice_type_unreachable_is_not_reported_as_timeout(telegram, voice_type):
    _, behaviour = voice_type
    behaviour["connect"] = True
    await feed_voice()

    assert telegram.chat() == ["❌ Сервис недоступен"]
