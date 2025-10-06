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
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
DB_URL = os.getenv("DB_URL", "sqlite:///./db.sqlite")
MEDIA_DIR = os.getenv("MEDIA_DIR", "./media")
API_SERVER_URL = os.getenv("API_SERVER_URL", "http://127.0.0.1:8000")

Path(MEDIA_DIR).mkdir(parents=True, exist_ok=True)

# Setup DB
engine = create_engine(DB_URL, echo=False, future=True)
metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

async def save_media_and_record(message: Message, session_db):
    media_path = None
    if message.photo or message.document:
        # create filename: channel_messageid.ext
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
        filename = f"{CHANNEL_USERNAME}_{msg_id}.{ext}"
        out_path = Path(MEDIA_DIR) / filename
        # download with Telethon
        await client.download_media(message.media, file=str(out_path))
        media_path = str(out_path)

    # Save to DB
    # Use plain SQL to ensure compatibility
    with SessionLocal() as db:
        # Avoid duplicates by message_id + channel
        existing = db.execute(
            posts_table.select().where(
                (posts_table.c.channel == CHANNEL_USERNAME) &
                (posts_table.c.message_id == message.id)
            )
        ).first()
        if existing:
            print(f"Message {message.id} already exists in DB. Skipping.")
            return
        ins = posts_table.insert().values(
            channel=CHANNEL_USERNAME,
            message_id=message.id,
            text=message.text or (message.message if hasattr(message, "message") else None),
            media_path=media_path
        )
        db.execute(ins)
        db.commit()
        print(f"Saved message {message.id} (media: {bool(media_path)})")

        # Notify API server about the new post
        new_post_data = {
            "id": message.id, # This is not the DB ID, but the message_id from Telegram
            "channel": CHANNEL_USERNAME,
            "message_id": message.id,
            "text": message.text or (message.message if hasattr(message, "message") else None),
            "media_url": f"/media/{filename}" if media_path else None,
            "posted_at": message.date.isoformat() if message.date else None
        }
        async with httpx.AsyncClient() as client_http:
            try:
                await client_http.post(f"{API_SERVER_URL}/api/new_post/{CHANNEL_USERNAME}", json=new_post_data)
            except httpx.RequestError as e:
                print(f"Error notifying API server: {e}")

@client.on(events.NewMessage(chats=CHANNEL_USERNAME)) # Re-enable chats filter with resolved entity
async def handler(event):
    msg = event.message
    print(f"Received new message in {CHANNEL_USERNAME}: {msg.id}")
    try:
        await save_media_and_record(msg, None)
    except Exception as e:
        print("Error saving message:", e)

async def main():
    print("Starting Telegram listener...")
    print(f"Configured API_ID: {API_ID}")
    print(f"Configured CHANNEL_USERNAME: {CHANNEL_USERNAME}")
    await client.start()
    if await client.is_user_authorized():
        print("Client authorized successfully.")
    else:
        print("Client NOT authorized. Please ensure you have logged in with your Telegram account.")
        print("You might need to delete the .session file and restart listener.py to re-authenticate.")
    print("Client started. Listening for messages.")
    try:
        channel_entity = await client.get_entity(CHANNEL_USERNAME)
        print(f"Successfully resolved channel entity for {CHANNEL_USERNAME}: {channel_entity.id}")
    except Exception as e:
        print(f"Error resolving channel entity for {CHANNEL_USERNAME}: {e}")
        print("Please ensure the CHANNEL_USERNAME is correct and the client has access to it.")
        return # Exit if channel cannot be resolved

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
