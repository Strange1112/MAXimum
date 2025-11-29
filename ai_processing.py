import os
import google.generativeai as genai
import trafilatura
from dotenv import load_dotenv
from PIL import Image

"""
подготовка к работе с файлом, смотри.
тебе нужно будет установить библиотеки, введи эту хуйню:
pip install google-generativeai trafilatura pillow dotenv python-dotenv 
последняя на всякий случай хуй знает будет у тебя без неё работать или нет
и создай файл ".env" в папке где лежит этот файл, там напиши вот это:
GENAI_API_KEY=AIzaSyASbEQZz-9DJnNJj-fzO12E4sS_OVlk-fo
всё, файл из одной строки будет
"""
load_dotenv()
API_KEY = os.environ["GENAI_API_KEY"]

def fetch_url_content(url):
    """Вытаскивает текст из ссылки"""
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        return trafilatura.extract(downloaded)
    return "Не удалось извлечь текст по ссылке."

def generate_updated_note(current_note, topic_name, new_data_type, new_data_content):
    """
    Функция для Gemini API.
    new_data_content для картинки должен быть !ПУТЕМ! к файлу на диске (str)
    """
    genai.configure(api_key=API_KEY)
    
    # Хз чё тут выбрать, можно поменять на "gemini-2.5-pro", она умнее, но чуть медленнее
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    system_prompt = f"""
    ROLE: Ты редактор академических конспектов.

    TASK: Объедини существующий конспект с новой информацией по теме "{topic_name}".

    INPUT CONTEXT:
    - Старый конспект: может быть пустым или содержать структурированную информацию
    - Новые данные: текст, распознанный текст с изображения или извлеченный контент из URL
    - Тема: {topic_name}

    PROCESSING RULES:
    1. Формат формул: Используй простой текст — a/b, x^2, sqrt(x), lim(x->0), integral(f(x)dx)
    2. Точность: Работай только с предоставленной информацией. Не добавляй факты извне
    3. Стиль: Сохраняй тональность исходного конспекта (формальный/неформальный)
    4. OCR обработка: Интегрируй распознанный текст напрямую без метакомментариев
    5. Орфография: Исправляй ошибки сканирования, сохраняя исходный смысл
    6. Релевантность: Если новые данные не относятся к теме — верни исходный конспект
    7. Пустой конспект: Если старый конспект пуст — создай структурированный новый
    8. Шум: Пропускай пометки на полях, подчеркивания и нерелевантные заметки

    OUTPUT FORMAT:
    - Только итоговый Markdown-текст конспекта
    - Без вступлений, объяснений или метакомментариев
    - Структура: заголовки (##), списки, code blocks для формул

    EXAMPLES:

    Формулы:
    ❌ WRONG: $\\frac{{a}}{{b}}$, $$x^2$$
    ✅ CORRECT: a/b, x^2, (a+b)/c

    OCR интеграция:
    ❌ WRONG: "На изображении показано: формула x^2 + y^2 = r^2"
    ✅ CORRECT: "## Уравнение окружности\nx^2 + y^2 = r^2"

    Начни обработку.
    """

    
    # Тут сам запрос делается
    prompt_parts = [system_prompt]
    
    if new_data_type == "text":
        prompt_parts.append(f"Старый конспект:\n{current_note}")
        prompt_parts.append(f"Новая информация:\n{new_data_content}")
    
    elif new_data_type == "url":
        scraped_text = fetch_url_content(new_data_content)
        prompt_parts.append(f"Старый конспект:\n{current_note}")
        prompt_parts.append(
            f"Информация из ссылки:\n{scraped_text[:30000]}"
        )  # Лимит символов на всякий случай
    
    elif new_data_type == "image":
        try:
            img = Image.open(new_data_content)
            prompt_parts.append(f"Старый конспект:\n{current_note}")
            prompt_parts.append(
                "Ниже изображение с новой информацией (распознай рукописный или печатный текст):"
            )
            prompt_parts.append(img)
        except Exception as e:
            return f"Ошибка открытия изображения: {e}"
    
    # Генерация
    try:
        response = model.generate_content(prompt_parts)
        return response.text
    except Exception as e:
        return f"Ошибка Gemini API: {e}"
