from fastapi import FastAPI, Query, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import Message
import os
import re
from dotenv import load_dotenv
from fastapi import Request

# Загрузка переменных окружения
load_dotenv()

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
session_name = os.getenv("SESSION_NAME", "anon")

# Инициализация FastAPI
app = FastAPI()

# Папка для скачанных медиа
download_folder = "downloads"
os.makedirs(download_folder, exist_ok=True)

# Правильный URL для доступа к медиа
BASE_URL = "https://fastapi-production-100d.up.railway.app"

# Подключение папки со статикой
app.mount("/media", StaticFiles(directory=download_folder), name="media")

# Инициализация шаблонов
templates = Jinja2Templates(directory="templates")

# Словарь для хранения временных данных, таких как phone_code_hash
auth_data = {}

# Функция извлечения username из ссылки или имени канала
def extract_username(channel: str) -> str:
    channel = re.sub(r"https?://t\.me/", "", channel)
    channel = channel.lstrip("@")
    match = re.match(r"[\w\d_]+", channel)
    if match:
        return match.group(0)
    raise HTTPException(status_code=400, detail="Неверный формат имени канала")

@app.get("/", response_class=HTMLResponse)
async def get_form(request: Request):
    return templates.TemplateResponse("login_form.html", {"request": request, "step": "phone"})

@app.post("/authenticate")
async def authenticate(request: Request, phone: str = Form(None), code: str = Form(None), password: str = Form(None)):
    global client
    # Если клиент еще не инициализирован, то инициализируем его
    if client is None:
        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()

    # Шаг 1: Ввод номера телефона
    if phone and not code and not password:
        try:
            # Отправляем код на номер телефона и сохраняем phone_code_hash
            result = await client.send_code_request(phone)
            auth_data['phone_code_hash'] = result.phone_code_hash  # Сохраняем phone_code_hash
            return templates.TemplateResponse("login_form.html", {"request": request, "step": "code", "phone": phone})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка при отправке кода: {str(e)}")
    
    # Шаг 2: Ввод кода
    if code and not password:
        try:
            # Используем phone_code_hash для авторизации
            await client.sign_in(phone, code, phone_code_hash=auth_data['phone_code_hash'])
        except SessionPasswordNeededError:
            return templates.TemplateResponse("login_form.html", {"request": request, "step": "password", "phone": phone})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка при вводе кода: {str(e)}")
    
    # Шаг 3: Ввод пароля
    if password:
        try:
            await client.sign_in(password=password)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка при вводе пароля: {str(e)}")

    # Завершение аутентификации
    return {"status": "success", "message": "Аутентификация успешна!"}

@app.get("/get_post_media")
async def get_post_media(
    channel: str = Query(..., description="Имя канала или ссылка на него"),
    post_id: int = Query(..., description="ID сообщения в канале")
):
    username = extract_username(channel)
    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()

    try:
        msg = await client.get_messages(username, ids=post_id)
        if not msg:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")

        result = {
            "id": msg.id,
            "date": str(msg.date),
            "text": "",
            "url": f"https://t.me/{username}/{msg.id}",
            "media": {}
        }

        if msg.grouped_id:
            range_ids = list(range(msg.id - 10, msg.id + 10))
            nearby_msgs = await client.get_messages(username, ids=range_ids)
            grouped = [m for m in nearby_msgs if m and m.grouped_id == msg.grouped_id]
            grouped = sorted(grouped, key=lambda m: m.id)

            for m in grouped:
                if m.message:
                    result["text"] = m.message
                    break

            index = 1
            for m in grouped:
                media_info = await process_media(m, media_index=index)
                if media_info:
                    key = f"media_{index}"
                    result["media"][key] = media_info
                    index += 1
        else:
            result["text"] = msg.message or ""
            if msg.media:
                media_info = await process_media(msg, media_index=1)
                if media_info:
                    result["media"]["media_1"] = media_info

    finally:
        await client.disconnect()

    return {"status": "ok", "post": result}

async def process_media(msg: Message, media_index: int = 0):
    if not msg.media:
        return None

    media = msg.media
    media_info = {}

    file_name_base = f"{msg.id}"
    file_name_ext = "media"

    if hasattr(media, "document") and media.document:
        attrs = media.document.attributes
        file_name_attr = None
        for attr in attrs:
            if hasattr(attr, "file_name"):
                file_name_attr = attr.file_name
                break
        if file_name_attr:
            file_name_ext = file_name_attr
        else:
            mime = getattr(media.document, "mime_type", None)
            if mime:
                ext = mime.split('/')[-1]
                file_name_ext = f"{file_name_base}.{ext}"
            else:
                file_name_ext = f"{file_name_base}.media"
    elif hasattr(media, "photo") and media.photo:
        file_name_ext = f"{file_name_base}.jpg"
    else:
        file_name_ext = f"{file_name_base}.media"

    name_part, ext_part = os.path.splitext(file_name_ext)

    if media_index > 0:
        if name_part.startswith(file_name_base):
            suffix = name_part[len(file_name_base):]
            file_name = f"{file_name_base}_{media_index}{suffix}{ext_part}"
        else:
            file_name = f"{file_name_base}_{media_index}_{name_part}{ext_part}"
    else:
        file_name = file_name_ext

    file_path = os.path.join(download_folder, file_name)

    if not os.path.exists(file_path):
        await msg.client.download_media(msg, file=file_path)

    media_info["type"] = type(media).__name__
    media_info["file_name"] = file_name
    media_info["url"] = f"{BASE_URL}/media/{file_name}"

    return media_info
