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
    model = genai.GenerativeModel("gemini-2.5-pro")
    
    system_prompt = f"""
    Твоя задача: ОБЪЕДИНИТЬ старый конспект по теме "{topic_name}" с новой информацией.

    СТРОГИЕ ОГРАНИЧЕНИЯ:
    1. [ФОРМАТ] НИКАКОГО LaTeX. Запрещены символы $ и команды типа \\frac. Пиши формулы текстом: "a/b", "x^2", "lim(x->0)".
    2. [КОНТЕНТ] НЕ добавляй информацию "от себя", которой нет во входных данных. Не придумывай факты. Твоя задача — только структурировать и объединять то, что дали.
    3. [СТИЛЬ] Сохраняй стиль исходного конспекта. Если это сухие факты — пиши сухо.
    4. [OCR] Если на входе картинка — просто распознай текст и вставь его в нужное место. Не надо писать "На изображении мы видим...". Просто вставь текст.

    Входные данные могут содержать ошибки сканирования — исправь их (орфографию), но не меняй смысл.
    Верни ТОЛЬКО итоговый текст в Markdown.
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
