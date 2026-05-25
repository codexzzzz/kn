import os
import uuid
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatType

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.db")

MOVES = {
    "rock": "🪨 Камень",
    "scissors": "✂️ Ножницы",
    "paper": "📄 Бумага",
}

WINS = {
    "rock": "scissors",
    "scissors": "paper",
    "paper": "rock",
}

games: dict[str, dict] = {}


# ─── Database ────────────────────────────────────────────────────────────────

def db_connect():
    return sqlite3.connect(DB_PATH)


def db_init():
    with db_connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                wins      INTEGER DEFAULT 0,
                losses    INTEGER DEFAULT 0,
                points    INTEGER DEFAULT 0
            )
        """)
        con.commit()


def db_ensure_user(user_id: int, username: str):
    with db_connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        con.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id),
        )
        con.commit()


def db_get_user(user_id: int) -> dict | None:
    with db_connect() as con:
        row = con.execute(
            "SELECT user_id, username, wins, losses, points FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {"user_id": row[0], "username": row[1], "wins": row[2], "losses": row[3], "points": row[4]}


def calc_delta(points: int, won: bool) -> int:
    if points > 10000:
        return +10 if won else -20
    if points > 1000:
        return +10 if won else -10
    return +10 if won else -5


def db_record_result(winner_id: int, winner_name: str, loser_id: int, loser_name: str):
    db_ensure_user(winner_id, winner_name)
    db_ensure_user(loser_id, loser_name)

    winner = db_get_user(winner_id)
    loser = db_get_user(loser_id)

    win_delta = calc_delta(winner["points"], won=True)
    loss_delta = calc_delta(loser["points"], won=False)

    with db_connect() as con:
        con.execute(
            "UPDATE users SET wins = wins + 1, points = points + ? WHERE user_id = ?",
            (win_delta, winner_id),
        )
        con.execute(
            "UPDATE users SET losses = losses + 1, points = MAX(0, points + ?) WHERE user_id = ?",
            (loss_delta, loser_id),
        )
        con.commit()

    return win_delta, loss_delta


def db_top10() -> list[dict]:
    with db_connect() as con:
        rows = con.execute(
            "SELECT user_id, username, wins, losses, points FROM users ORDER BY points DESC LIMIT 10"
        ).fetchall()
    return [{"user_id": r[0], "username": r[1], "wins": r[2], "losses": r[3], "points": r[4]} for r in rows]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_user_mention(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.first_name


def points_tier(points: int) -> str:
    if points >= 10000:
        return "💎 Легенда"
    if points >= 5000:
        return "🏆 Мастер"
    if points >= 2000:
        return "🥇 Эксперт"
    if points >= 1000:
        return "🥈 Опытный"
    if points >= 500:
        return "🥉 Новичок+"
    return "🌱 Новичок"


# ─── Handlers ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or "друг"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🎮 Добавить бота в группу",
            url="https://t.me/CYEFAtelebot?startgroup=only_chat"
        )]
    ])
    await update.message.reply_text(
        f"👋 Привет, {name}!\n\n"
        "⚔️ Я бот для игры в <b>Камень Ножницы Бумага</b> прямо в групповых чатах.\n\n"
        "📋 <b>Команды:</b>\n"
        "/duel — бросить вызов игроку\n"
        "/stats — твоя статистика и очки\n"
        "/top — топ-10 игроков 🎮",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_ensure_user(user.id, user.username or user.first_name)
    data = db_get_user(user.id)

    total = data["wins"] + data["losses"]
    winrate = round(data["wins"] / total * 100) if total else 0
    tier = points_tier(data["points"])

    win_d = calc_delta(data["points"], won=True)
    loss_d = calc_delta(data["points"], won=False)

    await update.message.reply_text(
        f"📊 <b>Статистика игрока</b> {get_user_mention(user)}\n\n"
        f"{tier}  •  <b>{data['points']:,}</b> очков\n\n"
        f"🏆 Побед:    <b>{data['wins']}</b>\n"
        f"💀 Поражений: <b>{data['losses']}</b>\n"
        f"🎯 Винрейт:  <b>{winrate}%</b>  ({total} игр)\n\n"
        f"💡 Сейчас: победа <b>+{win_d}</b> / поражение <b>{loss_d}</b>",
        parse_mode="HTML",
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_top10()
    if not rows:
        await update.message.reply_text("Ещё никто не сыграл ни одной игры!")
        return

    medals = ["🥇", "🥈", "🥉"] + ["▪️"] * 7
    lines = []
    for i, row in enumerate(rows):
        name = f"@{row['username']}" if row["username"] else f"id{row['user_id']}"
        tier = points_tier(row["points"])
        lines.append(
            f"{medals[i]} <b>{i+1}.</b> {name}  —  <b>{row['points']:,}</b> очков  {tier}\n"
            f"    ✅ {row['wins']} побед  •  ❌ {row['losses']} поражений"
        )

    await update.message.reply_text(
        "🏆 <b>Топ-10 игроков</b>\n\n" + "\n\n".join(lines),
        parse_mode="HTML",
    )


async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🎮 Добавить бота в группу",
                url="https://t.me/CYEFAtelebot?startgroup=only_chat"
            )]
        ])
        await update.message.reply_text(
            "✂️🪨📄 Играть в Камень Ножницы Бумага можно только в группах!\n\n"
            "Добавь меня в свою группу и вызывай друзей на дуэль 👇",
            reply_markup=keyboard,
        )
        return

    challenger = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text("Использование: /duel @username")
        return

    raw = args[0].lstrip("@")
    if not raw:
        await update.message.reply_text("Укажи имя пользователя: /duel @username")
        return

    if challenger.username and raw.lower() == challenger.username.lower():
        await update.message.reply_text("Нельзя вызвать самого себя!")
        return

    game_id = str(uuid.uuid4())[:8]
    games[game_id] = {
        "chat_id": chat.id,
        "challenger_id": challenger.id,
        "challenger_name": get_user_mention(challenger),
        "challenged_username": raw,
        "challenged_id": None,
        "challenged_name": f"@{raw}",
        "status": "pending",
        "moves": {},
        "message_id": None,
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"accept:{game_id}"),
            InlineKeyboardButton("❌ Отказаться", callback_data=f"decline:{game_id}"),
        ]
    ])

    challenger_mention = get_user_mention(challenger)
    msg = await update.message.reply_text(
        f"@{raw}! Тебе кинул вызов {challenger_mention}, принимаешь?",
        reply_markup=keyboard,
    )
    games[game_id]["message_id"] = msg.message_id


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    if data.startswith("accept:"):
        game_id = data.split(":", 1)[1]
        await handle_accept(query, user, game_id)

    elif data.startswith("decline:"):
        game_id = data.split(":", 1)[1]
        await handle_decline(query, user, game_id)

    elif data.startswith("move:"):
        _, game_id, move = data.split(":", 2)
        await handle_move(query, user, game_id, move)


async def handle_accept(query, user, game_id):
    game = games.get(game_id)
    if not game:
        await query.answer("Дуэль не найдена.", show_alert=True)
        return

    if game["status"] != "pending":
        await query.answer("Дуэль уже началась или завершена.", show_alert=True)
        return

    if user.id == game["challenger_id"]:
        await query.answer("Ты не можешь принять свой же вызов!", show_alert=True)
        return

    game["challenged_id"] = user.id
    game["challenged_name"] = get_user_mention(user)
    game["status"] = "waiting_moves"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🪨 Камень", callback_data=f"move:{game_id}:rock"),
            InlineKeyboardButton("✂️ Ножницы", callback_data=f"move:{game_id}:scissors"),
            InlineKeyboardButton("📄 Бумага", callback_data=f"move:{game_id}:paper"),
        ]
    ])

    await query.edit_message_text(
        f"⚔️ Дуэль началась!\n"
        f"{game['challenger_name']} vs {game['challenged_name']}\n\n"
        f"Оба игрока, выберите ход:",
        reply_markup=keyboard,
    )
    await query.answer("Вызов принят! Выбери ход.")


async def handle_decline(query, user, game_id):
    game = games.get(game_id)
    if not game:
        await query.answer("Дуэль не найдена.", show_alert=True)
        return

    if game["status"] != "pending":
        await query.answer("Дуэль уже началась или завершена.", show_alert=True)
        return

    if user.id == game["challenger_id"]:
        game["status"] = "cancelled"
        await query.edit_message_text(
            f"{game['challenger_name']} отменил вызов."
        )
        await query.answer("Вызов отменён.")
        return

    game["status"] = "declined"
    await query.edit_message_text(
        f"{get_user_mention(user)} отказался от дуэли с {game['challenger_name']}. 🐔"
    )
    await query.answer("Ты отказался от дуэли.")


async def handle_move(query, user, game_id, move):
    game = games.get(game_id)
    if not game:
        await query.answer("Дуэль не найдена.", show_alert=True)
        return

    if game["status"] != "waiting_moves":
        await query.answer("Дуэль не ждёт ходов.", show_alert=True)
        return

    if user.id not in (game["challenger_id"], game["challenged_id"]):
        await query.answer("Ты не участвуешь в этой дуэли!", show_alert=True)
        return

    if user.id in game["moves"]:
        await query.answer("Ты уже сделал ход!", show_alert=True)
        return

    game["moves"][user.id] = move
    await query.answer(f"Ход принят: {MOVES[move]}")

    if len(game["moves"]) < 2:
        other_name = (
            game["challenged_name"]
            if user.id == game["challenger_id"]
            else game["challenger_name"]
        )
        await query.edit_message_text(
            f"⚔️ Дуэль: {game['challenger_name']} vs {game['challenged_name']}\n\n"
            f"Ждём ход от {other_name}... ⏳",
            reply_markup=query.message.reply_markup,
        )
        return

    game["status"] = "finished"

    c_id = game["challenger_id"]
    d_id = game["challenged_id"]
    c_move = game["moves"][c_id]
    d_move = game["moves"][d_id]
    c_name = game["challenger_name"]
    d_name = game["challenged_name"]

    c_label = MOVES[c_move]
    d_label = MOVES[d_move]

    if c_move == d_move:
        result_line = "🤝 <b>Ничья!</b>"
        points_line = "Очки не изменились."
    elif WINS[c_move] == d_move:
        win_d, loss_d = db_record_result(
            c_id, c_name.lstrip("@"), d_id, d_name.lstrip("@")
        )
        result_line = f"🏆 Победил <b>{c_name}</b>!"
        points_line = f"{c_name}: <b>+{win_d}</b> очков\n{d_name}: <b>{loss_d}</b> очков"
    else:
        win_d, loss_d = db_record_result(
            d_id, d_name.lstrip("@"), c_id, c_name.lstrip("@")
        )
        result_line = f"🏆 Победил <b>{d_name}</b>!"
        points_line = f"{d_name}: <b>+{win_d}</b> очков\n{c_name}: <b>{loss_d}</b> очков"

    await query.edit_message_text(
        f"⚔️ <b>Итоги дуэли!</b>\n\n"
        f"{c_name}: {c_label}\n"
        f"{d_name}: {d_label}\n\n"
        f"{result_line}\n\n"
        f"💰 {points_line}",
        parse_mode="HTML",
    )

    games.pop(game_id, None)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан!")

    db_init()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("duel", duel))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
