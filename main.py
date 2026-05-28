import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.methods import DeleteWebhook
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from openai import OpenAI
from aiogram.types import FSInputFile
from aiogram import F, Router
from config import TOKEN, ADMIN_ID

admin_modes = {}

user_histories = {}  # Хранилище истории сообщений пользователей


logging.basicConfig(level=logging.INFO)
bot = Bot(TOKEN)
dp = Dispatcher()

# Настройки ограничений
MAX_REQUEST_LENGTH = 2000  # Максимальная длина текста запроса (в символах)
MAX_CHUNK_SIZE = 1000  # Максимальная длина одного сообщения, отправляемого API НЕ ТРОГАТЬ
REQUEST_COOLDOWN = 6 #кд между запросами

user_daily_requests = {}
last_request_time = {}

MODELS = {
    'gpt-4o': 'Модель gpt-4o. Улучшенная версия GPT-4.',
    'o1-mini': 'Модель o1-mini. Упрощённая версия o1.',
    'o3-mini': 'Модель o3-mini. Компактная и быстрая.',
}

user_models = {}
error_count = 0
error_models = set()
api_status = 'working'

def get_user_access(user_id):
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT access FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return user[0]
    return 0  # Если пользователя нет в БД, возвращаем доступ 0

def escape_markdown_v2(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def add_api_key(api_key):
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO api_keys (api_key) VALUES (?)', (api_key,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

    # ОБНОВЛЕНИЕ СПИСКА API КЛЮЧЕЙ
    global api_keys
    api_keys = get_api_keys_from_db()

def user_exists(user_id):
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user is not None  # Возвращает True, если пользователь существует

def format_seconds(seconds):
    if 11 <= seconds % 100 <= 19:
        return f"{seconds} секунд"
    last_digit = seconds % 10
    if last_digit == 1:
        return f"{seconds} секунда"
    elif 2 <= last_digit <= 4:
        return f"{seconds} секунды"
    else:
        return f"{seconds} секунд"

def update_user_vip(user_id, vip_status):
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    
    if user_exists(user_id):  # Проверяем, существует ли пользователь в базе
        cursor.execute('UPDATE users SET vip = ? WHERE user_id = ?', (vip_status, user_id))
    else:
        cursor.execute('INSERT INTO users (user_id, access, vip) VALUES (?, 1, ?)', (user_id, vip_status))
    
    conn.commit()
    conn.close()

def get_user_info(user_id):
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT access, vip FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def update_user_access(user_id, access):
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET access = ? WHERE user_id = ?', (access, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, access FROM users')
    users = cursor.fetchall()
    conn.close()
    return users

def split_text(text, max_length=MAX_CHUNK_SIZE):
    # Разбиваем текст на части, не разрывая слова
    words = text.split()
    chunks = []
    current_chunk = ""

    for word in words:
        if len(current_chunk) + len(word) + 1 <= max_length:
            current_chunk += " " + word if current_chunk else word
        else:
            chunks.append(current_chunk)
            current_chunk = word

    if current_chunk:
        chunks.append(current_chunk)

    return chunks

@dp.message(Command(commands=['start']))
async def start_command_handler(message: Message):
    user_id = message.from_user.id
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT access FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if not user:
        cursor.execute('INSERT INTO users (user_id, access) VALUES (?, 0)', (user_id,))
        conn.commit()
        conn.close()
        access = 0
    else:
        access = user[0]
        conn.close()

    if access == 1:
        photo = FSInputFile('menu.png')
        keyboard = get_model_keyboard()
        await message.answer_photo(photo, caption="""📌 Добро пожаловать! Выберите модель для работы:

🧠 GPT-4o - Улучшенная версия GPT-4. Отличается высокой точностью и глубиной ответов. Идеально подходит для сложных задач, генерации больших текстов и создания уникальных идей.

⚡ o1-mini - Упрощённая версия. Быстрая и эффективная. Хорошо справляется с задачами средней сложности, создаёт качественные ответы на стандартные запросы.

🚀 o3-mini - Компактная и быстрая модель. Оптимизирована для быстрого ответа на простые вопросы. Идеальный выбор для задач, где важна скорость отклика.

Бот находится в бета-версии, поэтому возможны небольшие баги""", reply_markup=keyboard)
    else:
        photo = FSInputFile('zapret.png')
        await message.answer_photo(photo, caption="🧙‍♂️У вас нет доступа к боту.")


@dp.message(Command(commands=['admin']))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить API ключ", callback_data="add_api"), InlineKeyboardButton(text="👥 Управление пользователями", callback_data="manage_users")],
        [InlineKeyboardButton(text="💎 Управление VIP", callback_data="manage_vip"), InlineKeyboardButton(text="📊 Показать статистику", callback_data="show_stats")],
        [InlineKeyboardButton(text="🔍 Найти API", callback_data="check_api"), InlineKeyboardButton(text="📈 Статус API ключей", callback_data="api_status")],
    ])

    await message.answer("🔐 Админ-панель. Выбери нужный пункт:", reply_markup=keyboard)



@dp.callback_query(lambda c: c.data == "api_status")
async def api_status_handler(callback_query: CallbackQuery):
    await callback_query.answer("⏳ Идёт проверка API ключей, это может занять некоторое время...")
    working_keys_count, total_keys_count = await check_api_status()
    await callback_query.message.answer(f"📊 Рабочих API ключей: {working_keys_count}/{total_keys_count}")


async def check_api_status():
    global api_keys
    working_keys_count = 0
    total_keys_count = len(api_keys)
    
    for api_key in api_keys:
        try:
            client = OpenAI(base_url="https://api.langdock.com/openai/eu/v1", api_key=api_key)
            completion = client.chat.completions.create(model="o3-mini", messages=[{"role": "user", "content": "Test message"}])
            if completion:
                working_keys_count += 1
        except:
            continue  # Если ключ не работает, переход к следующему
    
    return working_keys_count, total_keys_count


def get_users_statistics():
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE access = 1')
    access_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE access = 0')
    no_access_users = cursor.fetchone()[0]
    
    conn.close()
    
    return total_users, access_users, no_access_users

@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats_handler(callback_query: CallbackQuery):
    total_users, access_users, no_access_users = get_users_statistics()
    stats_message = (
        f"📊 Статистика пользователей:\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ С доступом (1): {access_users}\n"
        f"❌ Без доступа (0): {no_access_users}"
    )
    
    await callback_query.answer()
    await callback_query.message.answer(stats_message)

@dp.callback_query(lambda c: c.data == "check_api")
async def check_api_handler(callback_query: CallbackQuery):
    await check_all_api_keys()  # Запускаем проверку всех ключей
    await callback_query.answer("🔍 Начинаем проверку API ключей. Пожалуйста, подождите...")

async def check_all_api_keys():
    global current_api_index, api_status, api_keys

    api_keys = get_api_keys_from_db()

    for index, api_key in enumerate(api_keys):
        try:
            client = OpenAI(base_url="https://api.langdock.com/openai/eu/v1", api_key=api_key)
            completion = client.chat.completions.create(model="o3-mini", messages=[{"role": "user", "content": "Test message"}])
            if completion:
                current_api_index = index
                save_current_api_index(current_api_index)
                api_status = 'working'
                
                # Уведомление о найденном рабочем ключе
                await bot.send_message(ADMIN_ID, f"✅ Найден рабочий API ключ: {api_key} (Индекс: {index})")
                return
        except:
            continue

    # Если все ключи не работают
    api_status = 'error'
    await bot.send_message(ADMIN_ID, "❌ Все API ключи не работают. Требуются новые ключи для продолжения работы.")


@dp.callback_query(lambda c: c.data == "add_api")
async def add_api_callback(callback_query: CallbackQuery):
    global admin_modes
    admin_modes[callback_query.from_user.id] = 'add_api'
    await callback_query.message.answer("🔑 Отправьте новый API ключ для добавления в базу данных.")
    await callback_query.answer()

def get_total_users():
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    conn.close()
    return total_users


def get_active_users():
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users WHERE access = 1')
    active_users = cursor.fetchone()[0]
    conn.close()
    return active_users


@dp.callback_query(lambda c: c.data == "manage_users")
async def manage_users_callback(callback_query: CallbackQuery):
    global admin_modes
    admin_modes[callback_query.from_user.id] = 'manage_users'
    await callback_query.message.answer(
        "👥 Введите ID пользователя и новый статус доступа (0 - нет доступа, 1 - доступ есть).\nПример: `123456789 1`"
    )
    await callback_query.answer()



def get_api_keys_from_db():
    conn = sqlite3.connect('api.db')
    cursor = conn.cursor()
    cursor.execute("SELECT api_key FROM api_keys")
    results = cursor.fetchall()
    conn.close()
    return [row[0] for row in results]


@dp.message(Command(commands=['admin']))
async def handle_admin_messages(message: Message):
    global admin_modes

    if message.from_user.id == ADMIN_ID:
        mode = admin_modes.get(message.from_user.id)
        
        if mode == 'add_api':
            api_key = message.text.strip()
            add_api_key(api_key)
            await message.answer("✅ API ключ успешно добавлен.")
            del admin_modes[message.from_user.id]

        elif mode == 'manage_users':
            try:
                user_id, access = message.text.split()
                user_id = int(user_id)
                access = int(access)
                if access in (0, 1):
                    update_user_access(user_id, access)
                    await message.answer(f"✅ Доступ для пользователя {user_id} успешно изменён.")
                else:
                    await message.answer("❌ Неверный формат доступа. Используйте 0 или 1.")
            except:
                await message.answer("❌ Неверный формат. Используйте: `<user_id> <доступ>` (0 или 1)")

            del admin_modes[message.from_user.id]

        elif mode == 'manage_vip':
            try:
                user_id, vip_status = message.text.split()
                user_id = int(user_id)
                vip_status = int(vip_status)
                
                if vip_status in (0, 1):
                    update_user_vip(user_id, vip_status)
                    if vip_status == 1:
                        await message.answer(f"✅ Пользователю {user_id} выдан статус VIP.")
                    else:
                        await message.answer(f"✅ У пользователя {user_id} отобран статус VIP.")
                    
                    del admin_modes[message.from_user.id]
                else:
                    await message.answer("❌ Неверный формат VIP статуса. Используйте 0 или 1.")
            except:
                await message.answer("❌ Неверный формат. Используйте: `<user_id> <vip_status>` (0 или 1)")





def save_current_api_index(index):
    with open("current_api_index.txt", "w") as file:
        file.write(str(index))


def load_current_api_index():
    try:
        with open("current_api_index.txt", "r") as file:
            return int(file.read())
    except:
        return 0


def get_model_keyboard():
    global api_status
    warning_text = ' ⚠️ Возможны неполадки, проблема уже известна и решается' if api_status == 'error' else ''
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    buttons = [InlineKeyboardButton(text=model_name, callback_data=f"model_{model_name}") for model_name in MODELS.keys()]

    row = []
    for i, button in enumerate(buttons):
        row.append(button)
        if len(row) == 2 or i == len(buttons) - 1:
            keyboard.inline_keyboard.append(row)
            row = []

    if api_status == 'error':
        keyboard.inline_keyboard.append([InlineKeyboardButton(text=warning_text, callback_data='error_info')])

    return keyboard


async def check_all_api_keys():
    global current_api_index, api_status, api_keys

    for index, api_key in enumerate(api_keys):
        try:
            client = OpenAI(base_url="https://api.langdock.com/openai/eu/v1", api_key=api_key)
            completion = client.chat.completions.create(model="o3-mini", messages=[{"role": "user", "content": "Test message"}])
            if completion:
                current_api_index = index
                save_current_api_index(current_api_index)
                api_status = 'working'
                await bot.send_message(ADMIN_ID, f"✅ Найден рабочий API ключ: {api_key} (Индекс: {index})")
                return  # Найден рабочий API ключ
        except:
            continue  # Пробуем следующий ключ

    # Если все ключи не работают
    api_status = 'error'
    await bot.send_message(ADMIN_ID, "❌ Все API ключи не работают. Требуются новые ключи для продолжения работы.")


api_keys = get_api_keys_from_db()
current_api_index = load_current_api_index()


@dp.callback_query(lambda c: c.data.startswith("model_"))
async def model_selection_handler(callback_query: CallbackQuery):
    model_name = callback_query.data.replace('model_', '')
    user_models[callback_query.from_user.id] = model_name
    await callback_query.message.answer(f"Вы выбрали модель: {model_name}")
    await callback_query.answer()


@dp.callback_query(lambda c: c.data == "check_api")
async def check_api_handler(callback_query: CallbackQuery):
    await check_all_api_keys()
    await callback_query.answer()

@dp.callback_query(lambda c: c.data == "manage_vip")
async def manage_vip_callback(callback_query: CallbackQuery):
    global admin_modes
    admin_modes[callback_query.from_user.id] = 'manage_vip'
    await callback_query.message.answer(
        "💎 Введите ID пользователя и новый VIP статус (0 - убрать VIP, 1 - дать VIP).\nПример: `123456789 1`"
    )
    await callback_query.answer()


@dp.message()
async def filter_messages(message: Message):
    asyncio.create_task(process_user_request(message))


async def process_user_request(message: types.Message):
    global error_count, current_api_index, error_models, api_status, admin_modes, last_request_time, user_histories

    user_id = message.from_user.id

    # Если админ в режиме редактирования
    if user_id in admin_modes:
        await handle_admin_messages(message)
        return

    user_info = get_user_info(user_id)
    if not user_info:
        await message.answer("❌ Вы не зарегистрированы в системе.")
        return

    access, vip_status = user_info
    if access == 0:
        return

    # Проверка типа сообщения
    if message.content_type != 'text':
        await message.answer("⚠️ Бот принимает только текстовые сообщения.")
        return

    if len(message.text) > MAX_REQUEST_LENGTH:
        await message.answer(f"❌ Ваш запрос слишком длинный! Максимум — {MAX_REQUEST_LENGTH} символов.")
        return

    # Кд
    current_time = datetime.now()
    if vip_status != 1:
        if user_id in last_request_time:
            time_passed = (current_time - last_request_time[user_id]).total_seconds()
            if time_passed < REQUEST_COOLDOWN:
                wait_time = format_seconds(int(REQUEST_COOLDOWN - time_passed))
                await message.answer(f"⏳ Подождите {wait_time} перед следующим запросом.")
                return
        last_request_time[user_id] = current_time

    # Получаем модель и ключ
    model_name = user_models.get(user_id, 'o3-mini').split()[-1]
    api_key = api_keys[current_api_index]
    client = OpenAI(base_url="https://api.langdock.com/openai/eu/v1", api_key=api_key)

    # Инициализируем историю пользователя
    if user_id not in user_histories:
        user_histories[user_id] = []

    # Добавляем текущее сообщение в историю
    user_histories[user_id].append({"role": "user", "content": message.text})
    user_histories[user_id] = user_histories[user_id][-10:]  # Ограничиваем последние 10 сообщений

    user_histories[user_id] = [
        msg for msg in user_histories[user_id]
        if isinstance(msg, dict) and msg.get("role") and msg.get("content")
    ]

    try:
        # Отправляем историю в модель (асинхронно)
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=model_name,
            messages=user_histories[user_id]
        )

        response_text = completion.choices[0].message.content

        # Сохраняем ответ в историю
        user_histories[user_id].append({"role": "assistant", "content": response_text})
        user_histories[user_id] = user_histories[user_id][-10:]

        # Разбиваем длинный ответ
        def split_response(response, max_length=4096):
            return [response[i:i + max_length] for i in range(0, len(response), max_length)]

        for part in split_response(response_text):
            await message.answer(part, parse_mode="Markdown")

        error_count = 0
        error_models.clear()

    except Exception as e:
        import traceback
        import httpx

        error_count += 1
        error_models.add(model_name)

        # Попытка извлечь подробности, если это ошибка httpx
        error_text = traceback.format_exc()
        error_message = f"❌ Ошибка при запросе к модели `{model_name}`:\n\n"
        error_message += f"Пользователь: {user_id}\n"

        if isinstance(e, httpx.HTTPStatusError):
            response = e.response
            error_message += f"HTTP {response.status_code} {response.reason_phrase}\n"
            try:
                error_json = response.json()
                error_message += f"Ответ API:\n```json\n{error_json}```"
            except:
                error_message += f"Тело ответа:\n```{response.text}```"
        else:
            error_message += f"Трассировка:\n```{error_text}```"

        # Отправка админу
        try:
            await bot.send_message(ADMIN_ID, error_message[:4096])
        except Exception as send_error:
            print("⚠️ Ошибка при отправке сообщения админу:", send_error)




        if error_count >= 3:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ Модель {model_name} выдала 3 ошибки подряд. Переключаю API ключ.\n"
                f"Ошибочные модели: {', '.join(error_models)}\nТекущий API ключ: {api_key}"
            )
            await check_all_api_keys()
            error_count = 0
            error_models.clear()

        await message.answer("❌ Произошла ошибка при обработке текста.")






async def main():
    await bot(DeleteWebhook(drop_pending_updates=True))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
