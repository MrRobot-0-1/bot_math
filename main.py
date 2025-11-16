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
# ЗАГРУЗКА НАСТРОЕК
# =======================================

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден. Создайте .env файл с BOT_TOKEN=...")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")   # фикс для aiogram 3.7+
)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

DB_PATH = Path("math_questions.db")

# =======================================
# СОСТОЯНИЯ FSM
# =======================================

class TestStates(StatesGroup):
    choosing_type = State()
    choosing_grade = State()
    entering_name = State()
    answering = State()

# =======================================
# БАЗА ДАННЫХ
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

    # ------- загрузка JSON файла -------
    try:
        with open("questions.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки questions.json: {e}")
        conn.close()
        raise

    # ------- вставка вопросов -------
    for category, questions in data.items():
        for q in questions:
            cur.execute(
                "INSERT INTO questions (category, question, A, B, C, D, correct) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (category,
                 q["question"],
                 q["A"], q["B"], q["C"], q["D"],
                 q["correct"].upper()
                )
            )

    conn.commit()
    conn.close()
    logger.info("База успешно создана из questions.json")

def get_questions(category: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT question, A, B, C, D, correct FROM questions WHERE category = ?",
        (category,)
    )
    rows = cur.fetchall()
    conn.close()
    random.shuffle(rows)
    return rows[:20]


# =======================================
# КЛАВИАТУРЫ
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="A", callback_data="ans_A")],
        [InlineKeyboardButton(text="B", callback_data="ans_B")],
        [InlineKeyboardButton(text="C", callback_data="ans_C")],
        [InlineKeyboardButton(text="D", callback_data="ans_D")],
    ])

# =======================================
# ПОКАЗ ВОПРОСА
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
# ХЕНДЛЕРЫ
# =======================================

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("<b>Математический тест</b>\nВыберите категорию:", reply_markup=keyboard_start())
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

    cat = f"school_{grade}" if is_school else f"uni_{grade}"

    await state.update_data(grade=grade, category=cat)

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
    choice = cb.data.split("_")[1]  # A/B/C/D

    data = await state.get_data()
    q = data["questions"][data["index"]]
    correct = q[5]

    if choice == correct:
        await state.update_data(correct=data["correct"] + 1)

    next_index = data["index"] + 1
    await state.update_data(index=next_index)

    if next_index >= 20:
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

    if score >= 18: text += "Отлично!"
    elif score >= 15: text += "Очень хорошо!"
    elif score >= 12: text += "Хорошо!"
    else: text += "Нужно потренироваться!"

    await cb.message.edit_text(text)
    await state.clear()

# =======================================
# ЗАПУСК
# =======================================

async def main():
    init_db()
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
