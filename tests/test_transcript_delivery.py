import datetime
import re
from typing import Any

import pytest
from aiogram.client.default import Default
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update, User, Voice

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


def parse_html(raw: str, method: TelegramMethod) -> str:
    out = []
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
            i = m.end()
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
                out.append(NAMED[name])
            elif name.startswith("#x"):
                out.append(chr(int(name[2:], 16)))
            else:
                out.append(chr(int(name[1:])))
            i = m.end()
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.next_id = 100
        self.messages: dict[int, str] = {}

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):
        raise AssertionError("no downloads expected in tests")
        yield b""

    def _visible(self, bot, method) -> str:
        parse_mode = method.parse_mode
        if isinstance(parse_mode, Default):
            parse_mode = bot.default[parse_mode.name]
        text = method.text
        if parse_mode is None:
            visible = text
        elif str(getattr(parse_mode, "value", parse_mode)).upper() == "HTML":
            visible = parse_html(text, method)
        else:
            raise AssertionError(f"test fake does not render parse_mode={parse_mode!r}")
        if not visible.strip():
            raise TelegramBadRequest(method=method, message="Bad Request: message text is empty")
        if len(visible) > TELEGRAM_LIMIT:
            raise TelegramBadRequest(method=method, message="Bad Request: MESSAGE_TOO_LONG")
        return visible

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
            visible = self._visible(bot, method)
            self.next_id += 1
            self.messages[self.next_id] = visible
            return self._message(self.next_id, visible).as_(bot)
        if isinstance(method, EditMessageText):
            visible = self._visible(bot, method)
            assert method.message_id in self.messages, "edit of unknown message"
            self.messages[method.message_id] = visible
            return self._message(method.message_id, visible).as_(bot)
        if isinstance(method, DeleteMessage):
            self.messages.pop(method.message_id, None)
            return True
        if method.__returning__ is bool:
            return True
        raise AssertionError(f"unexpected Telegram call in test: {type(method).__name__}")

    def chat(self) -> list[str]:
        return [self.messages[k] for k in sorted(self.messages)]


@pytest.fixture
def telegram(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(app.bot, "session", fake)
    return fake


async def deliver(monkeypatch, telegram, text, lang="ru", duration=12.5) -> list[str]:
    async def fake_transcribe(file_id):
        return {"text": text, "language": lang, "audio_duration": duration}

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
        assert len(m) <= TELEGRAM_LIMIT, f"message #{n} has {len(m)} chars"


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
