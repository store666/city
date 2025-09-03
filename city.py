import os
import json
import unicodedata
import asyncio
import nest_asyncio
nest_asyncio.apply()
import threading
from dataclasses import dataclass, field
from typing import Dict, Set, List, Optional, Tuple
from collections import defaultdict
from dotenv import load_dotenv
from flask import Flask

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)

# ---------- Загрузка токена ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN.strip() == "":
    raise ValueError("❌ BOT_TOKEN не найден. Укажите его в .env или переменной окружения.")

# ---------- Загрузка списка городов ----------
def load_cities(file_path: str = "cities.json") -> List[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise RuntimeError(f"Ошибка загрузки cities.json: {e}")

def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s).strip().lower()
    s = s.replace("ё", "е").replace("-", "").replace(" ", "")
    return ''.join(c for c in s if c.isalpha() or c in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя')

def build_city_index(cities: List[str]) -> Tuple[Set[str], Dict[str, Set[str]]]:
    all_norm = {normalize_text(c) for c in cities if normalize_text(c)}
    by_first: Dict[str, Set[str]] = defaultdict(set)
    for c in all_norm:
        if c:
            by_first[c[0]].add(c)
    return all_norm, by_first

RAW_CITIES = load_cities("cities.json")
CITIES, CITIES_BY_FIRST = build_city_index(RAW_CITIES)

TAIL_SKIP = {"ь", "ъ", "ы"}

def last_working_letter(city: str) -> Optional[str]:
    c = normalize_text(city)
    for ch in reversed(c):
        if ch not in TAIL_SKIP:
            return ch
    return None

@dataclass
class Game:
    players: List[int] = field(default_factory=list)
    usernames: Dict[int, str] = field(default_factory=dict)
    used: Set[str] = field(default_factory=set)
    turn_idx: int = 0
    need_letter: Optional[str] = None
    started: bool = False

    def current_player(self) -> Optional[int]:
        return self.players[self.turn_idx % len(self.players)] if self.players else None

    def next_turn(self):
        self.turn_idx = (self.turn_idx + 1) % len(self.players)

    def add_used(self, city_norm: str):
        self.used.add(city_norm)

def validate_move(game: Game, raw_city: str) -> Tuple[bool, str]:
    city = normalize_text(raw_city)
    if not city or last_working_letter(city) is None:
        return False, "invalid_name"
    if city not in CITIES:
        return False, "not_in_db"
    if city in game.used:
        return False, "already_used"
    if game.need_letter and not city.startswith(game.need_letter):
        return False, "wrong_letter"
    return True, "ok"

def has_moves(game: Game) -> bool:
    if game.need_letter is None:
        return True
    candidates = CITIES_BY_FIRST.get(game.need_letter, set())
    return len(candidates - game.used) > 0

games: Dict[int, Game] = {}
locks: Dict[int, asyncio.Lock] = {}

# ---------- Хендлеры ----------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 Игра в города\n"
        "/start — создать игру\n"
        "/join — присоединиться\n"
        "/status — статус\n"
        "/restart — сброс\n"
        "/help — справка\n"
        "Просто отправьте название города, чтобы сделать ход."
    )

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    async with locks.setdefault(chat_id, asyncio.Lock()):
        if chat_id in games:
            await update.message.reply_text("Игра уже создана. Используйте /join.")
            return
        g = Game(players=[user.id], usernames={user.id: user.username or user.first_name or "Аноним"})
        games[chat_id] = g
        locks[chat_id] = asyncio.Lock()
    await update.message.reply_text(f"Создано лобби. {g.usernames[user.id]} — игрок 1. Второму игроку: /join")

async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    async with locks.get(chat_id, asyncio.Lock()):
        g = games.get(chat_id)
        if not g:
            await update.message.reply_text("Нет активной игры. Используйте /start.")
            return
        if user.id in g.players:
            await update.message.reply_text("Вы уже в игре.")
            return
        if len(g.players) >= 2:
            await update.message.reply_text("Уже есть 2 игрока.")
            return
        g.players.append(user.id)
        g.usernames[user.id] = user.username or user.first_name or "Аноним"
        g.started = True
        first = g.usernames[g.players[g.turn_idx]]
    await update.message.reply_text(f"Игроки: {', '.join(g.usernames[p] for p in g.players)}. Начинает {first}.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    g = games.get(chat_id)
    if not g:
        await update.message.reply_text("Игра не начата. /start")
        return
    letter = g.need_letter.upper() if g.need_letter else "любая"
    cur_user = g.usernames.get(g.current_player(), "—")
    await update.message.reply_text(f"Ходит: {cur_user}. Использовано: {len(g.used)}. Следующая буква: '{letter}'.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    user = update.effective_user

    async with locks.get(chat_id, asyncio.Lock()):
        g = games.get(chat_id)
        if not g or not g.started or len(g.players) < 2:
            await update.message.reply_text("Игра не активна или ждёт второго игрока.")
            return
        if user.id not in g.players:
            await update.message.reply_text("Вы не в этой игре. /join")
            return
        if user.id != g.current_player():
            await update.message.reply_text(f"Сейчас ходит {g.usernames[g.current_player()]}.")
            return

        ok, reason = validate_move(g, text)
        city_norm = normalize_text(text)

        if not ok:
            reasons = {
                "not_in_db": "Город не найден в базе.",
                "already_used": "Этот город уже был.",
                "wrong_letter": f"Нужен город на букву '{g.need_letter.upper()}'.",
                "invalid_name": "Некорректное название города."
            }
            await update.message.reply_text(reasons.get(reason, "Ошибка."))
            return

        g.add_used(city_norm)
        g.need_letter = last_working_letter(city_norm)
        g.next_turn()
        next_name = g.usernames[g.current_player()]

        if not has_moves(g):
            await update.message.reply_text(
                f"{text.strip().capitalize()} принят. Больше нет городов на '{g.need_letter.upper()}'. "
                f"Победил {g.usernames[user.id]}!"
            )
            del games[chat_id]
            del locks[chat_id]
            return

    await update.message.reply_text(f"{text.strip().capitalize()} принят! Ход {next_name} на '{g.need_letter.upper()}'.")

async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    async with locks.get(chat_id, asyncio.Lock()):
        games.pop(chat_id, None)
        locks.pop(chat_id, None)
    await update.message.reply_text("Игра сброшена. /start чтобы начать новую.")
import asyncio
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "🎮 Telegram бот 'Города' работает!"

async def run_bot_async():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Бот запущен...")
    await app.run_polling()

if __name__ == "__main__":
    import threading

    # Запускаем Flask-сервер в отдельном потоке
    threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))), daemon=True).start()

    # Запускаем Telegram-бот в основном потоке
    asyncio.run(run_bot_async())


