import asyncio
import logging
import json
import os
from typing import Dict, List, Optional
import uuid
import urllib.request
import re
from enum import Enum
from functools import partial

from maxapi import Bot, Dispatcher, Router
from maxapi.types import BotStarted
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.types.attachments.buttons.attachment_button import AttachmentButton
from maxapi.types.attachments.attachment import ButtonsPayload
from maxapi.types.updates.message_callback import MessageCallback

from dotenv import load_dotenv
from ai_processing import generate_updated_note
from ai_diff import generate_diff_summary

# Загрузка переменных окружения
load_dotenv()

logging.basicConfig(level=logging.INFO)

# ===== КОНФИГУРАЦИЯ =====
BOT_TOKEN = os.getenv('MAX_BOT_TOKEN')

if not BOT_TOKEN:
    raise ValueError("❌ MAX_BOT_TOKEN не найден в .env файле!")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()

# ===== КОНСТАНТЫ =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SUBJECTS_FILE = os.path.join(DATA_DIR, "subjects.json")
CONSPECTS_FILE = os.path.join(DATA_DIR, "conspects.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
TXT_CONSPECTS_DIR = os.path.join(DATA_DIR, "txt_conspects")

CONSPECTS_PER_PAGE = 3

# Создаём директории
for directory in [DATA_DIR, IMAGES_DIR, TXT_CONSPECTS_DIR]:
    os.makedirs(directory, exist_ok=True)

# ===== ENUM ДЛЯ СОСТОЯНИЙ =====
class UserState(Enum):
    WAITING_FOR_SUBJECT_NAME = "waiting_for_subject_name"
    WAITING_FOR_CONSPECT_NAME = "waiting_for_conspect_name"
    WAITING_FOR_CONSPECT_DATA = "waiting_for_conspect_data"
    WAITING_FOR_VERSION_CHOICE = "waiting_for_version_choice"
    PROCESSING_DATA = "processing_data"

# ===== ТЕКСТОВЫЕ КОНСТАНТЫ =====
class Messages:
    START = "Выберите свой курс:"
    COURSE_SELECTED = "✅ Выбран {} курс"
    ENTER_SUBJECT_NAME = "✏️ Введите название предмета:"
    SUBJECT_ADDED = "✅ Предмет '{}' добавлен в {} курс!"
    SUBJECT_EXISTS = "ℹ️ Предмет '{}' уже существует в {} курсе."
    ENTER_CONSPECT_NAME = "✏️ Введите название нового конспекта:"
    SEND_DATA = "📝 Теперь отправьте текст конспекта, изображения или ссылки. После загрузки напишите 'Готово'"
    SEND_MORE_DATA = "📝 Отправьте текст, изображения или ссылки для дополнения конспекта. После загрузки напишите 'Готово'"
    CANCELLED = "❌ {}"
    PROCESSING_START = "🔄 Начинаю обработку данных конспекта..."
    NO_DATA = "❌ Не получено ни текста, ни изображений, ни ссылок."
    UNSUPPORTED_FILE = (
        "❌ Неподдерживаемый тип файла!\n\n"
        "Я принимаю только:\n"
        "📝 Текст\n"
        "🖼 Изображения (JPG, PNG, GIF, WebP)\n"
        "🔗 Ссылки (URL)\n\n"
        "Попробуйте снова или напишите 'Готово'"
    )
    UNKNOWN_DATA_TYPE = (
        "⚠️ Не распознан тип данных\n\n"
        "Отправьте:\n"
        "• Текст конспекта\n"
        "• Изображения лекций\n"
        "• Ссылки на материалы\n"
        "• Или напишите 'Готово'"
    )

# Храним состояние пользователей
user_states: Dict[int, dict] = {}

# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def load_json_data(filename: str, default: Optional[dict] = None) -> dict:
    """Загрузка данных из JSON файла"""
    if default is None:
        default = {}
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        save_json_data(filename, default)
        return default
    except Exception as e:
        logging.error(f"Ошибка загрузки {filename}: {e}")
        return default

def save_json_data(filename: str, data: dict) -> None:
    """Сохранение данных в JSON файл"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")

# ===== ФУНКЦИИ РАБОТЫ С URL =====
def is_valid_url(text: str) -> bool:
    """Проверяет, является ли текст валидным URL"""
    if not text:
        return False
    
    text = text.strip()
    
    # Паттерны для определения URL
    url_patterns = [
        r'^https?://',  # http:// или https://
        r'^ftp://',     # ftp://
        r'^www\.',      # www.example.com
    ]
    
    # Проверяем по паттернам
    for pattern in url_patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    
    # Дополнительная проверка: содержит домен с точкой (минимум example.com)
    if '.' in text and ' ' not in text and len(text) > 3:
        parts = text.split('/')
        domain = parts[0]
        # Проверяем что есть точка и доменная зона (минимум 2 символа)
        if '.' in domain:
            domain_parts = domain.split('.')
            if len(domain_parts) >= 2 and len(domain_parts[-1]) >= 2:
                return True
    
    return False

def normalize_url(url: str) -> str:
    """Нормализует URL, добавляя протокол если его нет"""
    url = url.strip()
    
    # Если уже есть протокол
    if re.match(r'^[a-zA-Z]+://', url):
        return url
    
    # Если начинается с www. или просто домен
    return f'https://{url}'

# ===== ФУНКЦИИ РАБОТЫ С ПРЕДМЕТАМИ =====
def get_subjects() -> Dict[str, List[str]]:
    return load_json_data(SUBJECTS_FILE)

def save_subjects(subjects: Dict[str, List[str]]) -> None:
    save_json_data(SUBJECTS_FILE, subjects)

def add_subject(course: int, subject_name: str) -> bool:
    """Добавляет предмет в курс. Возвращает True если добавлен, False если уже существует"""
    subjects_data = get_subjects()
    course_key = str(course)
    
    if course_key not in subjects_data:
        subjects_data[course_key] = []
    
    if subject_name in subjects_data[course_key]:
        return False
    
    subjects_data[course_key].append(subject_name)
    save_subjects(subjects_data)
    return True

# ===== ФУНКЦИИ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ =====
def get_users() -> dict:
    return load_json_data(USERS_FILE, {})

def save_users(users: dict) -> None:
    save_json_data(USERS_FILE, users)

def get_user_course(user_id: int) -> Optional[int]:
    """Получает курс пользователя"""
    users = get_users()
    user_data = users.get(str(user_id))
    return user_data.get('course') if user_data else None

def set_user_course(user_id: int, course: int) -> None:
    """Устанавливает курс пользователя"""
    users = get_users()
    users[str(user_id)] = {'course': course}
    save_users(users)

# ===== ФУНКЦИИ РАБОТЫ С КОНСПЕКТАМИ =====
def get_conspects() -> Dict[str, List[Dict]]:
    return load_json_data(CONSPECTS_FILE, {})

def save_conspects(conspects: Dict[str, List[Dict]]) -> None:
    save_json_data(CONSPECTS_FILE, conspects)

def get_subject_key(course: int, subject: str) -> str:
    """Формирует ключ для предмета"""
    return f"{course}_{subject}"

def add_conspect_to_subject(course: int, subject: str, conspect_name: str, content: str = "") -> str:
    """Добавляет конспект к предмету. Возвращает ID конспекта"""
    conspects = get_conspects()
    subject_key = get_subject_key(course, subject)
    
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
    """Получает все конспекты предмета"""
    conspects = get_conspects()
    subject_key = get_subject_key(course, subject)
    return [c for c in conspects.get(subject_key, []) if isinstance(c, dict)]

def update_conspect_content(course: int, subject: str, conspect_id: str, new_content: str) -> bool:
    """Обновляет содержимое конспекта"""
    conspects = get_conspects()
    subject_key = get_subject_key(course, subject)
    
    if subject_key in conspects:
        for conspect in conspects[subject_key]:
            if isinstance(conspect, dict) and conspect.get('id') == conspect_id:
                conspect['content'] = new_content
                save_conspects(conspects)
                return True
    return False

def get_conspect_by_id(course: int, subject: str, conspect_id: str) -> Optional[Dict]:
    """Получает конспект по ID"""
    conspects = get_conspects_by_subject(course, subject)
    for conspect in conspects:
        if isinstance(conspect, dict) and conspect.get('id') == conspect_id:
            return conspect
    return None

def save_txt_file(conspect_id: str, content: str, version: str = "old") -> Optional[str]:
    """Сохраняет текстовый файл конспекта"""
    filename = f"{conspect_id}_{version}.txt"
    filepath = os.path.join(TXT_CONSPECTS_DIR, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath
    except Exception as e:
        logging.error(f"Ошибка сохранения txt файла: {e}")
        return None

def cleanup_temp_files(conspect_id: str) -> None:
    """Удаляет временные файлы конспекта"""
    for version in ['old', 'new', 'diff']:
        filepath = os.path.join(TXT_CONSPECTS_DIR, f"{conspect_id}_{version}.txt")
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            logging.error(f"Ошибка удаления файла {filepath}: {e}")

# ===== ФУНКЦИИ РАБОТЫ С ИЗОБРАЖЕНИЯМИ =====
def extract_image_url(attachment_str: str) -> Optional[str]:
    """Извлекает URL из строки attachment"""
    try:
        parts = str(attachment_str).split()
        url_parts = [part for part in parts if part.startswith("url")]
        if url_parts:
            return url_parts[0][5:-2]  # Удаляем 'url=' и кавычки
    except Exception as e:
        logging.error(f"Ошибка парсинга URL изображения: {e}")
    return None

async def download_image(url: str) -> Optional[str]:
    """Скачивает изображение по URL и возвращает путь к файлу"""
    try:
        image_filename = f"image_{uuid.uuid4().hex}.jpg"
        image_path = os.path.join(IMAGES_DIR, image_filename)
        urllib.request.urlretrieve(url, image_path)
        return image_path
    except Exception as e:
        logging.error(f"Ошибка скачивания изображения: {e}")
        return None

def is_image_attachment(attachment_str: str) -> bool:
    """Проверяет, является ли вложение изображением"""
    attachment_str = attachment_str.lower()
    image_indicators = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', 'image']
    return 'url' in attachment_str and any(ext in attachment_str for ext in image_indicators)

# ===== СОЗДАНИЕ КНОПОК =====
def create_cancel_button(payload: str) -> AttachmentButton:
    """Создаёт клавиатуру с кнопкой отмены"""
    button = CallbackButton(text="❌ Отмена", payload=payload)
    return AttachmentButton(payload=ButtonsPayload(buttons=[[button]]))

def create_keyboard(buttons: List[List[CallbackButton]]) -> AttachmentButton:
    """Создаёт клавиатуру из кнопок"""
    return AttachmentButton(payload=ButtonsPayload(buttons=buttons))

# ===== ПАРСИНГ CALLBACK PAYLOAD =====
class CallbackData:
    """Класс для хранения распарсенных данных callback"""
    def __init__(self, action: str, course: Optional[int] = None, 
                 subject: Optional[str] = None, conspect_id: Optional[str] = None, 
                 page: Optional[int] = None):
        self.action = action
        self.course = course
        self.subject = subject
        self.conspect_id = conspect_id
        self.page = page

def parse_callback_payload(payload: str) -> CallbackData:
    """Парсит payload и возвращает структурированные данные"""
    parts = payload.split("_")
    
    # Обработка простых действий
    if payload in ["first", "second", "third", "fourth"]:
        course_map = {"first": 1, "second": 2, "third": 3, "fourth": 4}
        return CallbackData(action="select_course", course=course_map[payload])
    
    if payload == "change_course_button":
        return CallbackData(action="change_course")
    
    # Действия с предметами
    if payload.startswith("add_subject_"):
        return CallbackData(action="add_subject", course=int(parts[2]))
    
    if payload.startswith("cancel_add_subject_"):
        return CallbackData(action="cancel_add_subject", course=int(parts[3]))
    
    if payload.startswith("subject_"):
        return CallbackData(
            action="show_subject",
            course=int(parts[1]),
            subject="_".join(parts[2:])
        )
    
    # Действия с конспектами
    if payload.startswith("conspects_page_"):
        return CallbackData(
            action="conspects_page",
            course=int(parts[2]),
            subject="_".join(parts[3:-1]),
            page=int(parts[-1])
        )
    
    if payload.startswith("add_new_conspect_"):
        return CallbackData(
            action="add_new_conspect",
            course=int(parts[3]),
            subject="_".join(parts[4:])
        )
    
    if payload.startswith("cancel_add_conspect_"):
        return CallbackData(
            action="cancel_add_conspect",
            course=int(parts[3]),
            subject="_".join(parts[4:])
        )
    
    if payload.startswith("edit_conspect_"):
        return CallbackData(
            action="edit_conspect",
            course=int(parts[2]),
            subject="_".join(parts[3:-1]),
            conspect_id=parts[-1]
        )
    
    if payload.startswith("add_to_conspect_"):
        return CallbackData(
            action="add_to_conspect",
            course=int(parts[3]),
            subject="_".join(parts[4:-1]),
            conspect_id=parts[-1]
        )
    
    if payload.startswith("cancel_upload_data_"):
        return CallbackData(
            action="cancel_upload_data",
            course=int(parts[3]),
            subject="_".join(parts[4:-1]),
            conspect_id=parts[-1]
        )
    
    if payload.startswith("back_to_conspects_"):
        return CallbackData(
            action="back_to_conspects",
            course=int(parts[3]),
            subject="_".join(parts[4:])
        )
    
    if payload.startswith("back_to_subject_"):
        return CallbackData(
            action="back_to_subject",
            course=int(parts[3]),
            subject="_".join(parts[4:])
        )
    
    if payload.startswith("keep_old_") or payload.startswith("save_new_"):
        action = "keep_old" if payload.startswith("keep_old_") else "save_new"
        return CallbackData(action=action, conspect_id=parts[2])
    
    if payload.startswith("show_full_diff_"):
        return CallbackData(action="show_full_diff", conspect_id=parts[3])
    
    return CallbackData(action="unknown")

# ===== ИНТЕРФЕЙС =====
async def show_courses_menu(message) -> None:
    """Показывает меню выбора курса"""
    buttons = [
        [
            CallbackButton(text="1 курс", payload="first"),
            CallbackButton(text="2 курс", payload="second")
        ],
        [
            CallbackButton(text="3 курс", payload="third"),
            CallbackButton(text="4 курс", payload="fourth")
        ]
    ]
    await message.answer(text=Messages.START, attachments=[create_keyboard(buttons)])

async def show_subjects_for_course(message, course: int) -> None:
    """Показывает предметы для выбранного курса"""
    subjects_data = get_subjects()
    subjects = subjects_data.get(str(course), [])
    
    buttons = [[CallbackButton(text="➕ Добавить предмет", payload=f"add_subject_{course}")]]
    buttons.extend([[CallbackButton(text=subject, payload=f"subject_{course}_{subject}")] for subject in subjects])
    buttons.append([CallbackButton(text="🔄 Сменить курс", payload="change_course_button")])
    
    await message.answer(
        text=f"📚 Предметы {course} курса ({len(subjects)} предметов):",
        attachments=[create_keyboard(buttons)]
    )

async def show_conspects_page(message, course: int, subject: str, page: int = 0) -> None:
    """Показывает страницу с конспектами предмета"""
    conspects = get_conspects_by_subject(course, subject)
    start_idx = page * CONSPECTS_PER_PAGE
    end_idx = start_idx + CONSPECTS_PER_PAGE
    page_conspects = conspects[start_idx:end_idx]
    
    buttons = []
    
    # Кнопки конспектов
    for conspect in page_conspects:
        buttons.append([CallbackButton(
            text=f"📝 {conspect['name']}",
            payload=f"edit_conspect_{course}_{subject}_{conspect['id']}"
        )])
    
    # Кнопка добавления
    buttons.append([CallbackButton(
        text="➕ Добавить конспект",
        payload=f"add_new_conspect_{course}_{subject}"
    )])
    
    # Навигация
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
    
    # Кнопка назад
    buttons.append([CallbackButton(
        text="⬅️ Назад к предмету",
        payload=f"back_to_subject_{course}_{subject}"
    )])
    
    total_pages = max(1, (len(conspects) + CONSPECTS_PER_PAGE - 1) // CONSPECTS_PER_PAGE)
    await message.answer(
        text=f"📚 Конспекты по предмету '{subject}' (Страница {page + 1}/{total_pages}):",
        attachments=[create_keyboard(buttons)]
    )

async def show_conspect_view(message, course: int, subject: str, conspect_id: str) -> None:
    """Показывает просмотр конспекта с кнопками действий"""
    conspect_data = get_conspect_by_id(course, subject, conspect_id)
    
    if not conspect_data:
        await message.answer(text="❌ Конспект не найден")
        return
    
    conspect_name = conspect_data.get('name', 'Конспект')
    current_content = conspect_data.get('content', '')
    
    # Отправляем контент
    await message.answer(text=f"📄 Текущий конспект '{conspect_name}':\n\n{current_content}")
    
    # Отправляем кнопки
    buttons = [
        [CallbackButton(text="➕ Дополнить конспект", payload=f"add_to_conspect_{course}_{subject}_{conspect_id}")],
        [CallbackButton(text="⬅️ Назад к конспектам", payload=f"back_to_conspects_{course}_{subject}")]
    ]
    
    await message.answer(
        text=f"📚 Конспект: {conspect_name}\nВыберите действие:",
        attachments=[create_keyboard(buttons)]
    )

# ===== ОБРАБОТКА ДАННЫХ КОНСПЕКТА =====
async def process_conspect_data(message, state: dict) -> None:
    """Обрабатывает данные конспекта - текст, ссылки и изображения"""
    user_id = message.sender.user_id
    course = state['course']
    subject = state['subject']
    conspect_id = state['conspect_id']
    
    current_conspect = get_conspect_by_id(course, subject, conspect_id)
    current_content = current_conspect.get('content', '') if current_conspect else ''
    
    user_states[user_id] = {
        'state': UserState.PROCESSING_DATA.value,
        'course': course,
        'subject': subject,
        'conspect_id': conspect_id,
        'conspect_name': state['conspect_name'],
        'attachments': state.get('attachments', []),
        'urls': state.get('urls', []),
        'text_data': state.get('text_data', ''),
        'old_content': current_content,
        'current_content': current_content
    }
    
    await message.answer(text=Messages.PROCESSING_START)
    await process_next_item(message, user_id)

async def process_next_item(message, user_id: int) -> None:
    """Обрабатывает следующий элемент данных"""
    state = user_states.get(user_id, {})
    
    # 1. Обрабатываем текст
    if state.get('text_data') and state['text_data'] != state.get('old_content', ''):
        await process_text_data(message, user_id, state)
    
    # 2. Обрабатываем URLs
    elif state.get('urls'):
        await process_url_data(message, user_id, state)
    
    # 3. Обрабатываем изображения
    elif state.get('attachments'):
        await process_image_data(message, user_id, state)
    
    # Проверяем, есть ли ещё данные
    if state.get('text_data') or state.get('urls') or state.get('attachments'):
        await asyncio.sleep(1)
        await process_next_item(message, user_id)
    else:
        await finalize_processing(message, user_id, state)

async def process_text_data(message, user_id: int, state: dict) -> None:
    """Обрабатывает текстовые данные"""
    await message.answer(text="🔄 Обрабатываю текстовые данные...")
    try:
        def process_text():
            return generate_updated_note(
                state['current_content'],
                state['conspect_name'],
                "text",
                state['text_data']
            )
        
        new_content = await asyncio.get_event_loop().run_in_executor(None, process_text)
        state['current_content'] = new_content
        state['text_data'] = ''
        await message.answer(text="✅ Текстовые данные обработаны!")
    except Exception as e:
        logging.error(f"Ошибка обработки текста: {e}")
        await message.answer(text="❌ Ошибка при обработке текста")

async def process_url_data(message, user_id: int, state: dict) -> None:
    """Обрабатывает URL данные"""
    url = state['urls'].pop(0)
    await message.answer(text=f"🔄 Обрабатываю ссылку: {url[:50]}...")
    try:
        def process_url():
            return generate_updated_note(
                state['current_content'],
                state['conspect_name'],
                "url",
                url
            )
        
        new_content = await asyncio.get_event_loop().run_in_executor(None, process_url)
        state['current_content'] = new_content
        await message.answer(text=f"✅ Ссылка обработана! Осталось ссылок: {len(state['urls'])}")
    except Exception as e:
        logging.error(f"Ошибка обработки URL: {e}")
        await message.answer(text="❌ Ошибка при обработке ссылки")

async def process_image_data(message, user_id: int, state: dict) -> None:
    """Обрабатывает изображения"""
    image_path = state['attachments'].pop(0)
    try:
        if image_path and os.path.exists(image_path):
            await message.answer(text="🔄 Обрабатываю изображение с помощью AI...")
            
            def process_image():
                return generate_updated_note(
                    state['current_content'],
                    state['conspect_name'],
                    "image",
                    image_path
                )
            
            new_content = await asyncio.get_event_loop().run_in_executor(None, process_image)
            state['current_content'] = new_content
            
            # Удаляем временный файл
            try:
                os.remove(image_path)
            except:
                pass
            
            await message.answer(text=f"✅ Изображение обработано! Осталось: {len(state['attachments'])}")
    except Exception as e:
        logging.error(f"Ошибка обработки изображения: {e}")
        await message.answer(text="❌ Ошибка при обработке изображения")

async def finalize_processing(message, user_id: int, state: dict) -> None:
    """Завершает обработку и предлагает выбрать версию"""
    old_content = state['old_content']
    new_content = state['current_content']
    topic_name = state['conspect_name']
    conspect_id = state['conspect_id']
    
    # Сохраняем файлы
    old_file = save_txt_file(conspect_id, old_content, "old")
    new_file = save_txt_file(conspect_id, new_content, "new")
    
    if old_file and new_file:
        # Генерируем саммари изменений
        await message.answer(text="🔍 Анализирую изменения...")
        
        def generate_diff():
            return generate_diff_summary(old_content, new_content, topic_name)
        
        try:
            diff_summary = await asyncio.get_event_loop().run_in_executor(None, generate_diff)
            
            # Сохраняем diff в файл
            diff_file_path = os.path.join(TXT_CONSPECTS_DIR, f"{conspect_id}_diff.txt")
            try:
                with open(diff_file_path, 'w', encoding='utf-8') as f:
                    f.write(f"# Изменения в конспекте '{topic_name}'\n\n")
                    f.write(diff_summary)
                    f.write(f"\n\n---\n\n## СТАРАЯ ВЕРСИЯ:\n\n{old_content}")
                    f.write(f"\n\n---\n\n## НОВАЯ ВЕРСИЯ:\n\n{new_content}")
            except Exception as e:
                logging.error(f"Ошибка сохранения diff файла: {e}")
            
            # Показываем саммари
            await message.answer(text=diff_summary)
            
        except Exception as e:
            logging.error(f"Ошибка генерации diff: {e}")
            await message.answer(text="⚠️ Не удалось сгенерировать анализ изменений")
        
        # Кнопки выбора версии
        buttons = [
            [CallbackButton(text="✅ Оставить старую версию", payload=f"keep_old_{conspect_id}")],
            [CallbackButton(text="🔄 Сохранить новую версию", payload=f"save_new_{conspect_id}")],
            [CallbackButton(text="📄 Показать тексты", payload=f"show_full_diff_{conspect_id}")]
        ]
        
        await message.answer(
            text="💾 Какую версию сохранить?",
            attachments=[create_keyboard(buttons)]
        )
        
        user_states[user_id] = {
            'state': UserState.WAITING_FOR_VERSION_CHOICE.value,
            'course': state['course'],
            'subject': state['subject'],
            'conspect_id': conspect_id,
            'old_content': old_content,
            'new_content': new_content
        }

# ===== ОБРАБОТЧИКИ СОБЫТИЙ =====
@dp.bot_started()
async def bot_started(event: BotStarted) -> None:
    await event.bot.send_message(
        chat_id=event.chat_id,
        text='Привет! Отправь мне /start'
    )

@router.message_created()
async def handle_message(event: MessageCreated) -> None:
    message = event.message
    user_id = message.sender.user_id
    text = message.body.text.strip() if message.body.text else ""
    
    # Обработка команды "отмена"
    if text.lower() in ['отмена', 'cancel', 'отменить']:
        await handle_cancel(message, user_id)
        return
    
    # Обработка команд
    if text.lower() in ['старт', 'привет', '/start', 'start']:
        await handle_start(message, user_id)
        return
    
    if text.lower() in ['/change_course', 'change_course', 'сменить курс']:
        await show_courses_menu(message)
        return
    
    # Обработка состояний
    state = user_states.get(user_id, {})
    
    if state.get('state') == UserState.WAITING_FOR_SUBJECT_NAME.value:
        await handle_subject_name_input(message, user_id, text, state)
    
    elif state.get('state') == UserState.WAITING_FOR_CONSPECT_NAME.value:
        await handle_conspect_name_input(message, user_id, text, state)
    
    elif state.get('state') == UserState.WAITING_FOR_CONSPECT_DATA.value:
        await handle_conspect_data_input(message, user_id, text, state)

async def handle_cancel(message, user_id: int) -> None:
    """Обрабатывает команду отмены"""
    state = user_states.get(user_id, {})
    
    if state.get('state') == UserState.WAITING_FOR_SUBJECT_NAME.value:
        course = state.get('course')
        del user_states[user_id]
        await message.answer(text=Messages.CANCELLED.format("Добавление предмета отменено"))
        await show_subjects_for_course(message, course)
    
    elif state.get('state') == UserState.WAITING_FOR_CONSPECT_NAME.value:
        course = state.get('course')
        subject = state.get('subject')
        del user_states[user_id]
        await message.answer(text=Messages.CANCELLED.format("Создание конспекта отменено"))
        await show_conspects_page(message, course, subject, 0)
    
    elif state.get('state') == UserState.WAITING_FOR_CONSPECT_DATA.value:
        course = state.get('course')
        subject = state.get('subject')
        conspect_id = state.get('conspect_id')
        del user_states[user_id]
        await message.answer(text=Messages.CANCELLED.format("Загрузка данных отменена"))
        await show_conspect_view(message, course, subject, conspect_id)

async def handle_start(message, user_id: int) -> None:
    """Обрабатывает команду /start"""
    saved_course = get_user_course(user_id)
    if saved_course:
        await show_subjects_for_course(message, saved_course)
    else:
        if user_id in user_states:
            del user_states[user_id]
        await show_courses_menu(message)

async def handle_subject_name_input(message, user_id: int, subject_name: str, state: dict) -> None:
    """Обрабатывает ввод названия предмета"""
    course = state['course']
    
    if add_subject(course, subject_name):
        await message.answer(text=Messages.SUBJECT_ADDED.format(subject_name, course))
    else:
        await message.answer(text=Messages.SUBJECT_EXISTS.format(subject_name, course))
    
    del user_states[user_id]
    await show_subjects_for_course(message, course)

async def handle_conspect_name_input(message, user_id: int, conspect_name: str, state: dict) -> None:
    """Обрабатывает ввод названия конспекта"""
    if not conspect_name:
        return
    
    course = state['course']
    subject = state['subject']
    conspect_id = add_conspect_to_subject(course, subject, conspect_name)
    
    user_states[user_id] = {
        'state': UserState.WAITING_FOR_CONSPECT_DATA.value,
        'course': course,
        'subject': subject,
        'conspect_id': conspect_id,
        'conspect_name': conspect_name,
        'attachments': [],
        'urls': [],
        'text_data': ''
    }
    
    cancel_button = create_cancel_button(f"cancel_upload_data_{course}_{subject}_{conspect_id}")
    await message.answer(text=Messages.SEND_DATA, attachments=[cancel_button])

async def handle_conspect_data_input(message, user_id: int, text: str, state: dict) -> None:
    """Обрабатывает ввод данных конспекта"""
    # Проверка на команду "готово"
    if text.lower() in ['готово', 'done', 'закончил']:
        if state.get('text_data') or state.get('attachments') or state.get('urls'):
            await process_conspect_data(message, state)
        else:
            await message.answer(text=Messages.NO_DATA)
        return
    
    # Флаг валидности данных
    valid_data_received = False
    
    # Обработка текста
    if text:
        # Проверяем, является ли это URL
        if is_valid_url(text):
            if 'urls' not in state:
                state['urls'] = []
            
            # Нормализуем URL (добавляем протокол если нужно)
            normalized_url = normalize_url(text)
            state['urls'].append(normalized_url)
            
            # Показываем пользователю какой URL сохранили
            display_url = normalized_url if normalized_url != text else text
            await message.answer(
                text=f"✅ Ссылка получена: {display_url}\n"
                     f"Можете отправить ещё данные или написать 'Готово'"
            )
            valid_data_received = True
        else:
            # Обычный текст
            state['text_data'] = text
            await message.answer(text="✅ Текст конспекта получен! Можете отправить изображения или написать 'Готово'")
            valid_data_received = True
    
    # Обработка вложений
    if message.body.attachments:
        has_valid_attachment = False
        
        for attachment in message.body.attachments:
            attachment_str = str(attachment)
            
            # Проверяем, что это изображение
            if is_image_attachment(attachment_str):
                image_url = extract_image_url(attachment_str)
                if image_url:
                    image_path = await download_image(image_url)
                    if image_path:
                        state['attachments'].append(image_path)
                        await message.answer(
                            text=f"✅ Изображение {len(state['attachments'])} получено! Отправьте еще или напишите 'Готово'"
                        )
                        has_valid_attachment = True
                        valid_data_received = True
                    else:
                        await message.answer(text="❌ Ошибка при сохранении изображения")
            else:
                # Неподдерживаемый тип вложения
                await message.answer(text=Messages.UNSUPPORTED_FILE)
    
    # Если не было валидных данных
    if not valid_data_received and text and not text.lower() in ['готово', 'done', 'закончил']:
        await message.answer(text=Messages.UNKNOWN_DATA_TYPE)

@router.message_callback()
async def handle_callback(event: MessageCallback) -> None:
    """Обрабатывает callback кнопки"""
    callback = event.callback
    message = event.message
    user_id = callback.user.user_id
    
    data = parse_callback_payload(callback.payload)
    
    # Обработка действий
    if data.action == "select_course":
        set_user_course(user_id, data.course)
        await message.answer(text=Messages.COURSE_SELECTED.format(data.course))
        await show_subjects_for_course(message, data.course)
    
    elif data.action == "change_course":
        await show_courses_menu(message)
    
    elif data.action == "add_subject":
        user_states[user_id] = {
            'state': UserState.WAITING_FOR_SUBJECT_NAME.value,
            'course': data.course
        }
        cancel_button = create_cancel_button(f"cancel_add_subject_{data.course}")
        await message.answer(text=Messages.ENTER_SUBJECT_NAME, attachments=[cancel_button])
    
    elif data.action == "show_subject":
        await show_conspects_page(message, data.course, data.subject, 0)
    
    elif data.action == "conspects_page":
        await show_conspects_page(message, data.course, data.subject, data.page)
    
    elif data.action == "add_new_conspect":
        user_states[user_id] = {
            'state': UserState.WAITING_FOR_CONSPECT_NAME.value,
            'course': data.course,
            'subject': data.subject
        }
        cancel_button = create_cancel_button(f"cancel_add_conspect_{data.course}_{data.subject}")
        await message.answer(text=Messages.ENTER_CONSPECT_NAME, attachments=[cancel_button])
    
    elif data.action == "edit_conspect":
        await show_conspect_view(message, data.course, data.subject, data.conspect_id)
    
    elif data.action == "add_to_conspect":
        conspect_data = get_conspect_by_id(data.course, data.subject, data.conspect_id)
        if conspect_data:
            user_states[user_id] = {
                'state': UserState.WAITING_FOR_CONSPECT_DATA.value,
                'course': data.course,
                'subject': data.subject,
                'conspect_id': data.conspect_id,
                'conspect_name': conspect_data.get('name', 'Конспект'),
                'attachments': [],
                'urls': [],
                'text_data': ''
            }
            cancel_button = create_cancel_button(f"cancel_upload_data_{data.course}_{data.subject}_{data.conspect_id}")
            await message.answer(text=Messages.SEND_MORE_DATA, attachments=[cancel_button])
    
    elif data.action == "show_full_diff":
        state = user_states.get(user_id, {})
        
        if state.get('state') == UserState.WAITING_FOR_VERSION_CHOICE.value and state.get('conspect_id') == data.conspect_id:
            old_content = state['old_content']
            new_content = state['new_content']
            
            old_preview = old_content[:1000] + "..." if len(old_content) > 1000 else old_content
            new_preview = new_content[:1000] + "..." if len(new_content) > 1000 else new_content
            
            await message.answer(text=f"📄 **СТАРАЯ ВЕРСИЯ:**\n\n{old_preview}")
            await message.answer(text=f"📄 **НОВАЯ ВЕРСИЯ:**\n\n{new_preview}")
            
            # Возвращаем кнопки выбора
            buttons = [
                [CallbackButton(text="✅ Оставить старую", payload=f"keep_old_{data.conspect_id}")],
                [CallbackButton(text="🔄 Сохранить новую", payload=f"save_new_{data.conspect_id}")]
            ]
            await message.answer(
                text="💾 Выберите версию:",
                attachments=[create_keyboard(buttons)]
            )
    
    elif data.action in ["keep_old", "save_new"]:
        await handle_version_choice(message, user_id, data)
    
    elif data.action == "back_to_conspects":
        await show_conspects_page(message, data.course, data.subject, 0)
    
    elif data.action == "back_to_subject":
        await show_subjects_for_course(message, data.course)
    
    elif data.action.startswith("cancel_"):
        await handle_callback_cancel(message, user_id, data)

async def handle_version_choice(message, user_id: int, data: CallbackData) -> None:
    """Обрабатывает выбор версии конспекта"""
    state = user_states.get(user_id, {})
    
    if state.get('state') != UserState.WAITING_FOR_VERSION_CHOICE.value:
        return
    
    if state['conspect_id'] != data.conspect_id:
        return
    
    course = state['course']
    subject = state['subject']
    conspect_id = state['conspect_id']
    
    conspect_data = get_conspect_by_id(course, subject, conspect_id)
    conspect_name = conspect_data.get('name', 'Конспект') if conspect_data else 'Конспект'
    
    if data.action == "save_new":
        update_conspect_content(course, subject, conspect_id, state['new_content'])
        await message.answer(text="✅ Новая версия конспекта сохранена!")
        await message.answer(text=f"📄 Обновленный конспект '{conspect_name}':\n\n{state['new_content']}")
    else:
        await message.answer(text=f"📄 Сохранена старая версия конспекта '{conspect_name}':\n\n{state['old_content']}")
    
    buttons = [
        [CallbackButton(text="➕ Дополнить конспект", payload=f"add_to_conspect_{course}_{subject}_{conspect_id}")],
        [CallbackButton(text="⬅️ Назад к конспектам", payload=f"back_to_conspects_{course}_{subject}")]
    ]
    
    await message.answer(text="Выберите дальнейшее действие:", attachments=[create_keyboard(buttons)])
    
    # Очистка временных файлов
    cleanup_temp_files(conspect_id)
    del user_states[user_id]

async def handle_callback_cancel(message, user_id: int, data: CallbackData) -> None:
    """Обрабатывает callback кнопки отмены"""
    if user_id in user_states:
        del user_states[user_id]
    
    if data.action == "cancel_add_subject":
        await message.answer(text=Messages.CANCELLED.format("Добавление предмета отменено"))
        await show_subjects_for_course(message, data.course)
    
    elif data.action == "cancel_add_conspect":
        await message.answer(text=Messages.CANCELLED.format("Создание конспекта отменено"))
        await show_conspects_page(message, data.course, data.subject, 0)
    
    elif data.action == "cancel_upload_data":
        await message.answer(text=Messages.CANCELLED.format("Загрузка данных отменена"))
        await show_conspect_view(message, data.course, data.subject, data.conspect_id)

# ===== ЗАПУСК БОТА =====
dp.include_routers(router)

async def main() -> None:
    get_subjects()
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
