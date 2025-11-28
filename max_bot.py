import asyncio
import logging
import json
import os
from typing import Dict, List
import uuid
import urllib.request

from maxapi import Bot, Dispatcher, Router
from maxapi.types import BotStarted
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.attachments.buttons.attachment_button import AttachmentButton
from maxapi.types.attachments.attachment import ButtonsPayload
from maxapi.types.updates.message_callback import MessageCallback
from ai_processing import generate_updated_note

logging.basicConfig(level=logging.INFO)

bot = Bot('f9LHodD0cOIs38MyWtobE8mdZEqKULiyRO3Ix7-faw7EaWM9AnWY2cBQepESyLyWuzf4m6RjPHcndOMbC-bU')
dp = Dispatcher()
router = Router()

# Константы для файлов и путей (всё относительно max_bot.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")  # <--- Папка для JSON

SUBJECTS_FILE = os.path.join(DATA_DIR, "subjects.json")
CONSPECTS_FILE = os.path.join(DATA_DIR, "conspects.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
TXT_CONSPECTS_DIR = os.path.join(BASE_DIR, "txt_conspects")

# Создаем все директории если их нет
os.makedirs(DATA_DIR, exist_ok=True)  # <--- Добавь эту строку
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(TXT_CONSPECTS_DIR, exist_ok=True)


# Храним состояние пользователей
user_states = {}

def load_json_data(filename: str, default: dict = None) -> dict:
    if default is None:
        default = {}
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            save_json_data(filename, default)
            return default
    except Exception as e:
        logging.error(f"Ошибка загрузки {filename}: {e}")
        return default

def save_json_data(filename: str, data: dict):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")

def get_subjects() -> Dict[str, List[str]]:
    return load_json_data(SUBJECTS_FILE)

def save_subjects(subjects: Dict[str, List[str]]):
    save_json_data(SUBJECTS_FILE, subjects)


def get_users() -> dict:
    return load_json_data(USERS_FILE, {})

def save_users(users: dict):
    save_json_data(USERS_FILE, users)

def get_user_course(user_id: int):
    """Возвращает сохраненный курс пользователя или None"""
    users = get_users()
    user_data = users.get(str(user_id))
    if user_data:
        return user_data.get('course')
    return None

def set_user_course(user_id: int, course: int):
    """Сохраняет курс пользователя навсегда"""
    users = get_users()
    users[str(user_id)] = {'course': course}
    save_users(users)


def get_conspects() -> Dict[str, List[Dict]]:
    return load_json_data(CONSPECTS_FILE, {})

def save_conspects(conspects: Dict[str, List[Dict]]):
    save_json_data(CONSPECTS_FILE, conspects)

def add_conspect_to_subject(course: int, subject: str, conspect_name: str, content: str = ""):
    conspects = get_conspects()
    subject_key = f"{course}_{subject}"
    if subject_key not in conspects:
        conspects[subject_key] = []
    
    conspect_id = str(uuid.uuid4())
    conspects[subject_key].append({
        'id': conspect_id,
        'name': conspect_name,
        'content': content
    })
    save_conspects(conspects)
    return conspect_id

def get_conspects_by_subject(course: int, subject: str) -> List[Dict]:
    conspects = get_conspects()
    subject_key = f"{course}_{subject}"
    subject_conspects = conspects.get(subject_key, [])
    return [c for c in subject_conspects if isinstance(c, dict)]

def update_conspect_content(course: int, subject: str, conspect_id: str, new_content: str):
    conspects = get_conspects()
    subject_key = f"{course}_{subject}"
    if subject_key in conspects:
        for conspect in conspects[subject_key]:
            if isinstance(conspect, dict) and conspect.get('id') == conspect_id:
                conspect['content'] = new_content
                break
        save_conspects(conspects)
        return True
    return False

def get_conspect_by_id(course: int, subject: str, conspect_id: str) -> Dict:
    conspects = get_conspects_by_subject(course, subject)
    for conspect in conspects:
        if isinstance(conspect, dict) and conspect.get('id') == conspect_id:
            return conspect
    return {}

def save_txt_file(conspect_id: str, content: str, version: str = "old"):
    filename = f"{conspect_id}_{version}.txt"
    filepath = os.path.join(TXT_CONSPECTS_DIR, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath
    except Exception as e:
        logging.error(f"Ошибка сохранения txt файла: {e}")
        return None

async def show_conspects_page(message, course: int, subject: str, page: int = 0):
    conspects = get_conspects_by_subject(course, subject)
    conspects_per_page = 3
    start_idx = page * conspects_per_page
    end_idx = start_idx + conspects_per_page
    page_conspects = conspects[start_idx:end_idx]

    buttons = []
    for conspect in page_conspects:
        buttons.append([CallbackButton(
            text=f"📝 {conspect['name']}",
            payload=f"edit_conspect_{course}_{subject}_{conspect['id']}"
        )])

    buttons.append([CallbackButton(
        text="➕ Добавить конспект",
        payload=f"add_new_conspect_{course}_{subject}"
    )])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(CallbackButton(
            text="⬅️ Предыдущая",
            payload=f"conspects_page_{course}_{subject}_{page-1}"
        ))
    if end_idx < len(conspects):
        nav_buttons.append(CallbackButton(
            text="Следующая ➡️",
            payload=f"conspects_page_{course}_{subject}_{page+1}"
        ))
    
    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([CallbackButton(
        text="⬅️ Назад к предмету",
        payload=f"back_to_subject_{course}_{subject}"
    )])

    keyboard = AttachmentButton(payload=ButtonsPayload(buttons=buttons))
    total_pages = (len(conspects) + conspects_per_page - 1) // conspects_per_page
    
    await message.answer(
        text=f"📚 Конспекты по предмету '{subject}' (Страница {page + 1}/{total_pages}):",
        attachments=[keyboard]
    )

async def process_conspect_data(message, state):
    """Обрабатывает данные конспекта - текст и изображения"""
    course = state['course']
    subject = state['subject']
    conspect_id = state['conspect_id']
    conspect_name = state['conspect_name']
    user_id = message.sender.user_id

    current_conspect = get_conspect_by_id(course, subject, conspect_id)
    current_content = current_conspect.get('content', '') if isinstance(current_conspect, dict) else ''

    user_states[user_id] = {
        'processing_data': True,
        'course': course,
        'subject': subject,
        'conspect_id': conspect_id,
        'conspect_name': conspect_name,
        'attachments': state.get('attachments', []),
        'text_data': state.get('text_data', ''),
        'old_content': current_content,
        'current_content': current_content
    }

    await message.answer(text="🔄 Начинаю обработку данных конспекта...")
    await process_next_item(message, user_id)

async def process_next_item(message, user_id):
    """Обрабатывает следующий элемент данных"""
    state = user_states.get(user_id, {})

    # Сначала обрабатываем текст, если есть
    if state.get('text_data') and state['text_data'] != state['old_content']:
        await message.answer(text="🔄 Обрабатываю текстовые данные...")
        try:
            new_content = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_updated_note(
                    state['current_content'],
                    state['conspect_name'],
                    "text",
                    state['text_data']
                )
            )
            state['current_content'] = new_content
            state['text_data'] = ''
            await message.answer(text="✅ Текстовые данные обработаны!")
        except Exception as e:
            logging.error(f"Ошибка обработки текста: {e}")
            await message.answer(text="❌ Ошибка при обработке текста")

    # Затем обрабатываем изображения
    elif state.get('attachments'):
        image_path = state['attachments'].pop(0)
        current_content = state.get('current_content', state['old_content'])
        try:
            if image_path and os.path.exists(image_path):
                await message.answer(text="🔄 Обрабатываю изображение с помощью AI...")
                new_content = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: generate_updated_note(
                        current_content,
                        state['conspect_name'],
                        "image",
                        image_path
                    )
                )
                state['current_content'] = new_content
                try:
                    os.remove(image_path)
                except:
                    pass
                await message.answer(text=f"✅ Изображение обработано! Осталось: {len(state['attachments'])}")
        except Exception as e:
            logging.error(f"Ошибка обработки изображения: {e}")
            await message.answer(text="❌ Ошибка при обработке изображения")

    # Проверяем, есть ли еще данные для обработки
    if state.get('text_data') or state.get('attachments'):
        await asyncio.sleep(1)
        await process_next_item(message, user_id)
    else:
        # Все данные обработаны
        final_content = state['current_content'] + "\n" + "-" * 40 + "\n\n\n" + "-" * 40 + "\n" + state['old_content']
        
        old_file = save_txt_file(state['conspect_id'], state['old_content'], "old")
        new_file = save_txt_file(state['conspect_id'], final_content, "new")

        if old_file and new_file:
            buttons = [
                [CallbackButton(text="✅ Оставить старую версию", payload=f"keep_old_{state['conspect_id']}")],
                [CallbackButton(text="🔄 Сохранить новую версию", payload=f"save_new_{state['conspect_id']}")]
            ]
            
            keyboard = AttachmentButton(payload=ButtonsPayload(buttons=buttons))
            await message.answer(
                text=f"📊 Обработка завершена!\n\n"
                     f"**Старая версия:**\n{state['old_content'][:200]}...\n\n"
                     f"**Новая версия:**\n{final_content[:200]}...\n\n"
                     f"Какую версию сохранить?",
                attachments=[keyboard]
            )

            user_states[user_id] = {
                'waiting_for_version_choice': True,
                'course': state['course'],
                'subject': state['subject'],
                'conspect_id': state['conspect_id'],
                'old_content': state['old_content'],
                'new_content': final_content
            }

@dp.bot_started()
async def bot_started(event: BotStarted):
    await event.bot.send_message(
        chat_id=event.chat_id,
        text='Привет! Отправь мне /start'
    )

@router.message_created()
async def handle_message(event: MessageCreated):
    message = event.message
    user_id = message.sender.user_id

    # Обработка команды /start
    if message.body.text and message.body.text.lower() in ['старт', 'привет', '/start', 'start']:
        saved_course = get_user_course(user_id)
        
        if saved_course:
            # Если курс уже сохранен - идем сразу к предметам
            await show_subjects_for_course(message, saved_course)
        else:
            # Если новый пользователь - показываем выбор курса
            if user_id in user_states:
                del user_states[user_id]
            await show_courses_menu(message)
        return

    # Обработка команды /change_course
    elif message.body.text and message.body.text.lower() in ['/change_course', 'change_course', 'сменить курс']:
        await show_courses_menu(message)
        return

    # Обработка ввода названия предмета
    elif (user_id in user_states and
          user_states[user_id].get('waiting_for_subject_name') and
          message.body.text):
        state = user_states[user_id]
        course = state['course']
        subject_name = message.body.text.strip()

        subjects_data = get_subjects()
        course_key = str(course)
        if course_key not in subjects_data:
            subjects_data[course_key] = []

        if subject_name and subject_name not in subjects_data[course_key]:
            subjects_data[course_key].append(subject_name)
            save_subjects(subjects_data)
            await message.answer(text=f"✅ Предмет '{subject_name}' добавлен в {course} курс!")
        else:
            await message.answer(text=f"ℹ️ Предмет '{subject_name}' уже существует в {course} курсе.")

        del user_states[user_id]
        await show_subjects_for_course(message, course)

    # Обработка ввода названия нового конспекта
    elif (user_id in user_states and
          user_states[user_id].get('waiting_for_conspect_name') and
          message.body.text):
        state = user_states[user_id]
        course = state['course']
        subject = state['subject']
        conspect_name = message.body.text.strip()

        if conspect_name:
            conspect_id = add_conspect_to_subject(course, subject, conspect_name)
            user_states[user_id] = {
                'waiting_for_conspect_data': True,
                'course': course,
                'subject': subject,
                'conspect_id': conspect_id,
                'conspect_name': conspect_name,
                'attachments': [],
                'text_data': ''
            }
            await message.answer(text="📝 Теперь отправьте текст конспекта или изображения. После загрузки напишите 'готово'")

    # Обработка данных конспекта (текст и изображения)
    elif (user_id in user_states and
          user_states[user_id].get('waiting_for_conspect_data')):
        state = user_states[user_id]

        # Обработка текста
        if message.body.text and message.body.text.strip():
            if message.body.text.lower() not in ['готово', 'done', 'закончил']:
                state['text_data'] = message.body.text.strip()
                await message.answer(text="✅ Текст конспекта получен! Можете отправить изображения или написать 'готово'")

        # Обработка изображений
        if message.body.attachments:
            for image in message.body.attachments:
                parse_url_image = str(image).split()
                url_from_img = [elem for elem in parse_url_image if elem.startswith("url")][0]
                url_from_img = url_from_img[5:-2]

                try:
                    image_filename = f"image_{uuid.uuid4().hex}.jpg"
                    image_path = os.path.join(IMAGES_DIR, image_filename)
                    urllib.request.urlretrieve(url_from_img, image_path)
                    state['attachments'].append(image_path)
                    await message.answer(text=f"✅ Изображение {len(state['attachments'])} получено и сохранено! Отправьте еще или напишите 'готово'")
                except Exception as e:
                    logging.error(f"Ошибка сохранения изображения: {e}")
                    await message.answer(text="❌ Ошибка при сохранении изображения")

        # Обработка команды "готово"
        if message.body.text and message.body.text.lower() in ['готово', 'done', 'закончил']:
            if state['text_data'] or state['attachments']:
                await process_conspect_data(message, state)
            else:
                await message.answer(text="❌ Не получено ни текста, ни изображений.")

async def show_courses_menu(message):
    button_1 = CallbackButton(text="1 курс", payload="first")
    button_2 = CallbackButton(text="2 курс", payload="second")
    button_3 = CallbackButton(text="3 курс", payload="third")
    button_4 = CallbackButton(text="4 курс", payload="fourth")

    keyboard = AttachmentButton(
        payload=ButtonsPayload(buttons=[[button_1, button_2], [button_3, button_4]])
    )

    await message.answer(
        text="Выберите свой курс:",
        attachments=[keyboard]
    )

async def show_subjects_for_course(message, course: int):
    subjects_data = get_subjects()
    course_key = str(course)
    subjects = subjects_data.get(course_key, [])

    buttons = []
    buttons.append([CallbackButton(text="➕ Добавить предмет", payload=f"add_subject_{course}")])
    
    for subject in subjects:
        buttons.append([CallbackButton(text=subject, payload=f"subject_{course}_{subject}")])
    
    buttons.append([CallbackButton(text="🔄 Сменить курс", payload="change_course_button")])

    keyboard = AttachmentButton(payload=ButtonsPayload(buttons=buttons))
    
    subject_count = len(subjects)
    await message.answer(
        text=f"📚 Предметы {course} курса ({subject_count} предметов):",
        attachments=[keyboard]
    )

@router.message_callback()
async def handle_callback(event: MessageCallback):
    callback = event.callback
    message = event.message
    user_id = callback.user.user_id

    if callback.payload in ["first", "second", "third", "fourth"]:
        course_map = {"first": 1, "second": 2, "third": 3, "fourth": 4}
        course = course_map[callback.payload]
        
        set_user_course(user_id, course)
        
        await message.answer(text=f"✅ Выбран {course} курс")
        await show_subjects_for_course(message, course)

    elif callback.payload == "change_course_button":
        await show_courses_menu(message)

    elif callback.payload.startswith("add_subject_"):
        course = int(callback.payload.split("_")[2])
        user_states[user_id] = {'waiting_for_subject_name': True, 'course': course}
        await message.answer(text="✏️ Введите название предмета:")

    elif callback.payload.startswith("subject_"):
        parts = callback.payload.split("_")
        course = int(parts[1])
        subject_name = "_".join(parts[2:])
        await show_conspects_page(message, course, subject_name, 0)

    elif callback.payload.startswith("conspects_page_"):
        parts = callback.payload.split("_")
        course = int(parts[2])
        subject_name = "_".join(parts[3:-1])
        page = int(parts[-1])
        await show_conspects_page(message, course, subject_name, page)

    elif callback.payload.startswith("add_new_conspect_"):
        parts = callback.payload.split("_")
        course = int(parts[3])
        subject_name = "_".join(parts[4:])
        user_states[user_id] = {
            'waiting_for_conspect_name': True,
            'course': course,
            'subject': subject_name
        }
        await message.answer(text="✏️ Введите название нового конспекта:")

    elif callback.payload.startswith("edit_conspect_"):
        parts = callback.payload.split("_")
        course = int(parts[2])
        subject_name = "_".join(parts[3:-1])
        conspect_id = parts[-1]

        conspect_data = get_conspect_by_id(course, subject_name, conspect_id)
        conspect_name = conspect_data.get('name', 'Конспект') if isinstance(conspect_data, dict) else 'Конспект'
        current_content = conspect_data.get('content', '')

        await message.answer(text=f"📄 Текущий конспект '{conspect_name}':\n\n{current_content}")

        buttons = [
            [CallbackButton(text="➕ Дополнить конспект", payload=f"add_to_conspect_{course}_{subject_name}_{conspect_id}")],
            [CallbackButton(text="⬅️ Назад к конспектам", payload=f"back_to_conspects_{course}_{subject_name}")]
        ]

        keyboard = AttachmentButton(payload=ButtonsPayload(buttons=buttons))
        await message.answer(
            text=f"📚 Конспект: {conspect_name}\nВыберите действие:",
            attachments=[keyboard]
        )

        user_states[user_id] = {
            'waiting_for_conspect_data': True,
            'course': course,
            'subject': subject_name,
            'conspect_id': conspect_id,
            'conspect_name': conspect_name,
            'attachments': [],
            'text_data': ''
        }

    elif callback.payload.startswith("keep_old_") or callback.payload.startswith("save_new_"):
        conspect_id = callback.payload.split("_")[2]
        state = user_states.get(user_id, {})

        if state.get('waiting_for_version_choice') and state['conspect_id'] == conspect_id:
            course = state['course']
            subject = state['subject']
            subject_name = "_".join([subject])

            conspect_data = get_conspect_by_id(course, subject_name, conspect_id)
            conspect_name = conspect_data.get('name', 'Конспект') if isinstance(conspect_data, dict) else 'Конспект'

            if callback.payload.startswith("save_new_"):
                update_conspect_content(course, subject, conspect_id, state['new_content'])
                await message.answer(text="✅ Новая версия конспекта сохранена!")
                await message.answer(text=f"📄 Обновленный конспект '{conspect_name}':\n\n{state['new_content']}")
            else:
                await message.answer(text=f"📄 Сохранена старая версия конспекта '{conspect_name}':\n\n{state['old_content']}")

            buttons = [
                [CallbackButton(text="➕ Дополнить конспект", payload=f"add_to_conspect_{course}_{subject}_{conspect_id}")],
                [CallbackButton(text="⬅️ Назад к конспектам", payload=f"back_to_conspects_{course}_{subject}")]
            ]

            keyboard = AttachmentButton(payload=ButtonsPayload(buttons=buttons))
            await message.answer(
                text="Выберите дальнейшее действие:",
                attachments=[keyboard]
            )

            del user_states[user_id]

    elif callback.payload.startswith("add_to_conspect_"):
        parts = callback.payload.split("_")
        course = int(parts[3])
        subject_name = "_".join(parts[4:-1])
        conspect_id = parts[-1]

        conspect_data = get_conspect_by_id(course, subject_name, conspect_id)
        conspect_name = conspect_data.get('name', 'Конспект') if isinstance(conspect_data, dict) else 'Конспект'

        user_states[user_id] = {
            'waiting_for_conspect_data': True,
            'course': course,
            'subject': subject_name,
            'conspect_id': conspect_id,
            'conspect_name': conspect_name,
            'attachments': [],
            'text_data': ''
        }

        await message.answer(text="📝 Отправьте текст, изображения или ссылку для дополнения конспекта. После загрузки напишите 'готово'")

    elif callback.payload.startswith("back_to_conspects_"):
        parts = callback.payload.split("_")
        course = int(parts[3])
        subject_name = "_".join(parts[4:])
        await show_conspects_page(message, course, subject_name, 0)

    elif callback.payload.startswith("back_to_subject_"):
        parts = callback.payload.split("_")
        course = int(parts[3])
        subject_name = "_".join(parts[4:])
        await show_subjects_for_course(message, course)

    elif callback.payload == "back_to_courses":
        await show_courses_menu(message)

dp.include_routers(router)

async def main():
    get_subjects()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
