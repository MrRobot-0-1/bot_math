import asyncio
import logging
import os
import random
import sqlite3
import json
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# =======================================
# IMPORT OPENROUTER / DEEPSEEK
# =======================================
from openai import OpenAI


# =======================================
# LOAD ENV
# =======================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
REFERER = os.getenv("REFERER")
SITE_NAME = os.getenv("SITE_NAME")

if not BOT_TOKEN:
    raise ValueError("Ошибка: BOT_TOKEN отсутствует в .env")

if not OPENROUTER_API_KEY:
    raise ValueError("Ошибка: OPENROUTER_API_KEY отсутствует в .env")

# OpenRouter client
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

DB_PATH = Path("math_questions.db")


# =======================================
# STATES
# =======================================
class TestStates(StatesGroup):
    choosing_type = State()
    choosing_grade = State()
    entering_name = State()
    answering = State()


class HelpStates(StatesGroup):
    waiting_question = State()


# =======================================
# DATABASE
# =======================================
def init_db():
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            question TEXT,
            A TEXT,
            B TEXT,
            C TEXT,
            D TEXT,
            correct TEXT
        )
    """)

    with open("questions.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    for category, questions in data.items():
        for q in questions:
            cur.execute("""
                INSERT INTO questions (category, question, A, B, C, D, correct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                category,
                q["question"],
                q["A"], q["B"], q["C"], q["D"],
                q["correct"].upper()
            ))

    conn.commit()
    conn.close()
    logger.info("База вопросов загружена успешно.")


def get_questions(category: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT question, A, B, C, D, correct
        FROM questions
        WHERE category = ?
    """, (category,))

    rows = cur.fetchall()
    conn.close()

    random.shuffle(rows)
    return rows[:20]


# =======================================
# KEYBOARDS
# =======================================
def keyboard_start():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Школьники (1–11 класс)", callback_data="type_school")],
        [InlineKeyboardButton(text="Студенты (1–4 курс)", callback_data="type_uni")]
    ])


def keyboard_grades(is_school: bool):
    grades = range(1, 12) if is_school else range(1, 5)
    rows, row = [], []

    for i, g in enumerate(grades, 1):
        row.append(
            InlineKeyboardButton(
                text=f"{g}-й класс" if is_school else f"{g}-й курс",
                callback_data=f"grade_{g}"
            )
        )
        if i % 3 == 0:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def keyboard_answers():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="A", callback_data="ans_A"),
             InlineKeyboardButton(text="B", callback_data="ans_B")],
            [InlineKeyboardButton(text="C", callback_data="ans_C"),
             InlineKeyboardButton(text="D", callback_data="ans_D")],
            [InlineKeyboardButton(text="🧠 Подсказка", callback_data="hint")]
        ]
    )


# =======================================
# SHOW QUESTION
# =======================================
async def show_question(target, state: FSMContext):
    data = await state.get_data()
    i = data["index"]
    q = data["questions"][i]

    text = (
        f"Вопрос <b>{i+1}</b>/20\n\n"
        f"<b>{q[0]}</b>\n\n"
        f"A. {q[1]}\n"
        f"B. {q[2]}\n"
        f"C. {q[3]}\n"
        f"D. {q[4]}\n"
    )

    if isinstance(target, Message):
        await target.answer(text, reply_markup=keyboard_answers())
    else:
        await target.message.edit_text(text, reply_markup=keyboard_answers())


# =======================================
# OPENROUTER / DEEPSEEK REQUEST
# =======================================
async def send_to_ai(text: str) -> str:
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model="deepseek/deepseek-chat-v3",
                extra_headers={
                    "HTTP-Referer": REFERER,
                    "X-Title": SITE_NAME,
                },
                extra_body={},
                messages=[{"role": "user", "content": text}]
            )
            return resp.choices[0].message.content

        except Exception as e:
            if "429" in str(e):
                await asyncio.sleep(1.5)  # пауза и повтор
                continue
            return f"❗ Ошибка ИИ: {e}"

    return "❗ Сервер перегружен. Попробуй через пару секунд."



# =======================================
# HANDLERS
# =======================================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "<b>Математический тест</b>\nВыберите категорию:",
        reply_markup=keyboard_start()
    )
    await state.set_state(TestStates.choosing_type)


@router.callback_query(F.data.startswith("type_"))
async def choose_type(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    is_school = cb.data == "type_school"

    await state.update_data(is_school=is_school)
    await cb.message.edit_text("Выберите класс/курс:", reply_markup=keyboard_grades(is_school))
    await state.set_state(TestStates.choosing_grade)


@router.callback_query(F.data.startswith("grade_"))
async def choose_grade(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    grade = int(cb.data.split("_")[1])

    is_school = (await state.get_data())["is_school"]
    category = f"school_{grade}" if is_school else f"uni_{grade}"

    await state.update_data(category=category, grade=grade)
    await cb.message.edit_text("Введите <b>ФИО полностью</b>:")
    await state.set_state(TestStates.entering_name)


@router.message(TestStates.entering_name)
async def enter_name(message: Message, state: FSMContext):
    name = message.text.strip()

    if len(name.split()) < 2:
        await message.answer("Введите ФИО полностью.")
        return

    await state.update_data(name=name)

    data = await state.get_data()
    questions = get_questions(data["category"])

    await state.update_data(questions=questions, index=0, correct=0)
    await show_question(message, state)
    await state.set_state(TestStates.answering)


@router.callback_query(F.data.startswith("ans_"), TestStates.answering)
async def process_answer(cb: CallbackQuery, state: FSMContext):
    await cb.answer()

    choice = cb.data.split("_")[1]
    data = await state.get_data()
    q = data["questions"][data["index"]]

    if choice == q[5]:
        await state.update_data(correct=data["correct"] + 1)

    i = data["index"] + 1
    await state.update_data(index=i)

    if i >= 20:
        await show_results(cb, state)
    else:
        await show_question(cb, state)


async def show_results(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    score = d["correct"]

    text = (
        "<b>Тест завершён!</b>\n\n"
        f"ФИО: <b>{d['name']}</b>\n"
        f"Класс/курс: <b>{d['grade']}</b>\n"
        f"Правильных ответов: <b>{score}/20</b>\n\n"
    )

    if score >= 18:
        text += "Отлично!"
    elif score >= 15:
        text += "Очень хорошо!"
    elif score >= 12:
        text += "Хорошо!"
    else:
        text += "Нужно потренироваться!"

    await cb.message.edit_text(text)
    await state.clear()


# =======================================
# HINT
# =======================================
@router.callback_query(F.data == "hint", TestStates.answering)
async def send_hint_func(cb: CallbackQuery, state: FSMContext):
    await cb.answer()

    data = await state.get_data()
    q = data["questions"][data["index"]]
    question_text = q[0]

    # ⏳ отправляем оповещение
    wait_msg = await cb.message.answer("⏳ Нейросеть думает... Подожди пару секунд 🤖")

    hint = await send_to_ai(
        f"Дай короткую подсказку для решения задачи: {question_text}. НЕ говори ответ."
    )

    # удаляем оповещение
    await wait_msg.delete()

    await cb.message.answer(f"🧠 <b>Подсказка:</b>\n{hint}")



# =======================================
# /helpme
# =======================================
@router.message(Command("helpme"))
async def helpme_cmd(message: Message, state: FSMContext):
    await message.answer("Напиши задачу, с которой нужна помощь.")
    await state.set_state(HelpStates.waiting_question)


@router.message(HelpStates.waiting_question)
async def helpme_answer(message: Message, state: FSMContext):
    user_question = message.text

    # ⏳ Уведомление о генерации
    wait_msg = await message.answer("⏳ Нейросеть думает… Подожди пару секунд 🤖")

    # Генерация ответа ИИ
    ai_answer = await send_to_ai(
        f"Объясни решение задачи максимально понятно и простыми словами: {user_question}"
    )

    # Удаляем уведомление
    await wait_msg.delete()

    # Отправляем результат
    await message.answer(ai_answer)

    await state.clear()



# =======================================
# START BOT
# =======================================
async def main():
    init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
