# listener.py
import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Message
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import posts_table, Post, metadata
import aiofiles
from pathlib import Path
import httpx
import json

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "collector_session")
# CHANNEL_USERNAME is now dynamic, but keep for initial client.get_entity if needed
CHANNEL_USERNAME_DEFAULT = os.getenv("CHANNEL_USERNAME", "freelance_ethio")
DB_URL = os.getenv("DB_URL", "sqlite:///./db.sqlite")
MEDIA_DIR = os.getenv("MEDIA_DIR", "./media")
API_SERVER_URL = os.getenv("API_SERVER_URL", "http://127.0.0.1:8000")
CHANNELS_HISTORY_FILE = "channels_for_history.json"
PROCESSED_CHANNELS_FILE = "processed_channels_history.json"

Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)

# Setup DB
engine = create_engine(DB_URL, echo=False, future=True)
metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

async def get_channel_identifier(event):
    if event.chat:
        if event.chat.username:
            return event.chat.username
        elif event.chat.title:
            # Sanitize title to be used as an identifier
            return "".join(c for c in event.chat.title if c.isalnum()).lower()
    return "unknown_channel" # Fallback

async def save_media_and_record(message: Message, channel_identifier: str):
    media_path = None
    if message.photo or message.document:
        msg_id = message.id
        ext = "jpg"
        if message.photo:
            ext = "jpg"
        elif message.document and getattr(message.document, "mime_type", None):
            mime = message.document.mime_type
            if mime.startswith("image/"):
                ext = mime.split("/")[-1]
            else:
                ext = getattr(message.document, "file_name", "file").split(".")[-1]
        filename = f"{channel_identifier}_{msg_id}.{ext}"
        out_path = Path(MEDIA_DIR) / filename
        await client.download_media(message.media, file=str(out_path))
        media_path = str(out_path)

    with SessionLocal() as db:
        existing = db.execute(
            posts_table.select().where(
                (posts_table.c.channel == channel_identifier) &
                (posts_table.c.message_id == message.id)
            )
        ).first()
        if existing:
            print(f"Message {message.id} already exists in DB for channel {channel_identifier}. Skipping.")
            return
        ins = posts_table.insert().values(
            channel=channel_identifier,
            message_id=message.id,
            text=message.text or (message.message if hasattr(message, "message") else None),
            media_path=media_path
        )
        db.execute(ins)
        db.commit()
        print(f"Saved message {message.id} (media: {bool(media_path)}) for channel {channel_identifier}")

        new_post_data = {
            "id": message.id,
            "channel": channel_identifier,
            "message_id": message.id,
            "text": message.text or (message.message if hasattr(message, "message") else None),
            "media_url": f"/media/{filename}" if media_path else None,
            "posted_at": message.date.isoformat() if message.date else None
        }
        async with httpx.AsyncClient() as client_http:
            try:
                await client_http.post(f"{API_SERVER_URL}/api/new_post/{channel_identifier}", json=new_post_data)
            except httpx.RequestError as e:
                print(f"Error notifying API server for channel {channel_identifier}: {e}")

@client.on(events.NewMessage()) # Listen to all new messages
async def handler(event):
    msg = event.message
    channel_identifier = await get_channel_identifier(event)
    print(f"Received new message in {channel_identifier}: {msg.id}")
    try:
        await save_media_and_record(msg, channel_identifier)
    except Exception as e:
        print(f"Error saving message for channel {channel_identifier}:", e)

async def main():
    print("Starting Telegram listener...")
    print(f"Configured API_ID: {API_ID}")
    print(f"Default CHANNEL_USERNAME for initial entity resolution: {CHANNEL_USERNAME_DEFAULT}")
    await client.start()
    if await client.is_user_authorized():
        print("Client authorized successfully.")
    else:
        print("Client NOT authorized. Please ensure you have logged in with your Telegram account.")
        print("You might need to delete the .session file and restart listener.py to re-authenticate.")
    print("Client started. Listening for messages.")
    try:
        # Use the default channel username for initial entity resolution, but listener will be dynamic
        channel_entity = await client.get_entity(CHANNEL_USERNAME_DEFAULT)
        print(f"Successfully resolved default channel entity for {CHANNEL_USERNAME_DEFAULT}: {channel_entity.id}")
    except Exception as e:
        print(f"Error resolving default channel entity for {CHANNEL_USERNAME_DEFAULT}: {e}")
        print("Please ensure the CHANNEL_USERNAME_DEFAULT is correct and the client has access to it.")
        # Do not exit, as the listener can still listen to other channels it has access to
        # return

    # Load channels to fetch history for
    channels_to_process = []
    if os.path.exists(CHANNELS_HISTORY_FILE):
        with open(CHANNELS_HISTORY_FILE, "r") as f:
            data = json.load(f)
            channels_to_process = data.get("channels", [])

    # Load already processed channels
    processed_channels = set()
    if os.path.exists(PROCESSED_CHANNELS_FILE):
        with open(PROCESSED_CHANNELS_FILE, "r") as f:
            data = json.load(f)
            processed_channels = set(data.get("channels", []))

    for channel_name in channels_to_process:
        if channel_name not in processed_channels:
            print(f"Fetching up to 1000 historical messages from {channel_name}...")
            try:
                async for message in client.iter_messages(channel_name, limit=1000):
                    await save_media_and_record(message, channel_name)
                print(f"Finished fetching historical messages for {channel_name}.")
                processed_channels.add(channel_name)
            except Exception as e:
                print(f"Error fetching historical messages for {channel_name}: {e}")
        else:
            print(f"Historical messages for {channel_name} already processed. Skipping.")

    # Save updated processed channels
    with open(PROCESSED_CHANNELS_FILE, "w") as f:
        json.dump({"channels": list(processed_channels)}, f, indent=4)

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
