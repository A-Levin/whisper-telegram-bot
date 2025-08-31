# 🎧 Whisper Telegram Bot

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://docker.com)

Телеграм бот для транскрипции аудио сообщений с помощью Whisper AI от OpenAI.

## ✨ Возможности

- 🎤 Транскрипция голосовых сообщений
- 📁 Обработка аудио файлов
- 🎬 Извлечение звука из видео
- 📝 Поддержка различных форматов медиа
- 🔍 Логирование в MongoDB (опционально)
- 🐳 Контейнеризация с Docker

## 🔧 Технические детали

Бот работает совместно с [faster-whisper-fastapi](https://github.com/cucumberian/faster-whisper-fastapi) бэкэндом, который выполняет обработку аудио на основе модели [Whisper от OpenAI](https://github.com/openai/whisper).



## 📱 Использование

1. Отправьте боту аудио сообщение или файл
2. Дождитесь обработки
3. Получите транскрипцию в текстовом виде

## 📋 Поддерживаемые форматы

**Ограничения:** файлы до 20 МБ

**Поддерживаемые типы:**
- 🎵 Аудио файлы (MP3, WAV, M4A и др.)
- 🎤 Голосовые сообщения
- 🎥 Видео файлы
- 📹 Видео-записки (кружочки)
- 📎 Документы с медиа-контентом

## 🚀 Быстрый старт

### 📋 Предварительные требования

- Python 3.9+
- Токен бота от [@BotFather](https://t.me/BotFather)
- Запущенный [faster-whisper-fastapi](https://github.com/cucumberian/faster-whisper-fastapi) бэкэнд

### ⚙️ Переменные окружения

Создайте файл `.env` или установите переменные:

```env
WHISPER_MIBOT_TOKEN=your_bot_token_here
WHISPER_BACKEND_URL=http://127.0.0.1:8000/transcribe
```

### 🐍 Запуск без Docker

```bash
# Клонируйте репозиторий
git clone https://github.com/A-Levin/whisper-telegram-bot.git
cd whisper-telegram-bot

# Установите зависимости
cd src
pip install -r requirements.txt

# Запустите бота
python app.py
```
### 🐳 Запуск с Docker

1. Создайте файл `.env`:
```env
WHISPER_MIBOT_TOKEN=your_bot_token_here
WHISPER_BACKEND_URL=http://127.0.0.1:8000/transcribe
```

2. Запустите контейнер:
```bash
docker-compose up -d
```

### 🔧 Запуск как systemd сервис

1. Создайте файл `/etc/systemd/system/whisper-mibot.service`:
```ini
[Unit]
Description=Whisper Telegram Bot
After=syslog.target network.target

[Service]
Type=simple
User=your_username
Group=your_usergroup
WorkingDirectory=/path/to/whisper-telegram-bot/src
ExecStart=/path/to/python3 /path/to/whisper-telegram-bot/src/app.py
RestartSec=30
Restart=always
KillMode=control-group
EnvironmentFile=/path/to/.env

[Install]
WantedBy=multi-user.target
```

2. Включите и запустите сервис:
```bash
sudo systemctl enable whisper-mibot.service
sudo systemctl start whisper-mibot.service
sudo systemctl status whisper-mibot.service
```

## 📊 Логирование (опционально)

Бот поддерживает логирование событий в MongoDB для аналитики использования.

### Что логируется:
- 🔐 Хеш пользователя (на основе имени и Telegram ID)
- ⏰ Время обращения к боту
- 📈 Статистика использования

### Настройка MongoDB

Добавьте в `.env` или `config.py`:
```env
MONGODB_CONNECTION_STRING=mongodb://localhost:27017
MONGODB_DATABASE_NAME=whisper_bot_logs
```

**Примечание:** Если MongoDB не настроена, логирование просто отключается без ошибок.

## 🤝 Вклад в проект

1. Форкните репозиторий
2. Создайте feature branch (`git checkout -b feature/amazing-feature`)
3. Закоммитьте изменения (`git commit -m 'Add amazing feature'`)
4. Запушьте branch (`git push origin feature/amazing-feature`)
5. Откройте Pull Request

## 📄 Лицензия

Этот проект распространяется под лицензией MIT. См. файл [LICENSE](LICENSE) для подробностей.

## 🆘 Поддержка

Если у вас возникли проблемы или есть вопросы:

- 🐛 [Создайте Issue](https://github.com/A-Levin/whisper-telegram-bot/issues)
- 💬 Опишите проблему максимально подробно
- 📋 Приложите логи, если возможно

---

⭐ **Поставьте звезду, если проект оказался полезным!**