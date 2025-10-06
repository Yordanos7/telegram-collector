# api_server.py
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from models import posts_table, metadata, Post
from pydantic import BaseModel
from typing import List, Optional
import json

load_dotenv()

DB_URL = os.getenv("DB_URL", "sqlite:///./db.sqlite")
MEDIA_DIR = os.getenv("MEDIA_DIR", "./media")

engine = create_engine(DB_URL, future=True)
metadata.create_all(engine)

app = FastAPI(title="Telegram Collector API")

# Serve the 'web' directory statically
app.mount("/web", StaticFiles(directory="web"), name="web")

# Serve media directory statically at /media
if not os.path.exists(MEDIA_DIR):
    os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str):
        await websocket.accept()
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        self.active_connections[channel].append(websocket)

    def disconnect(self, websocket: WebSocket, channel: str):
        self.active_connections[channel].remove(websocket)
        if not self.active_connections[channel]:
            del self.active_connections[channel]

    async def broadcast(self, message: str, channel: str):
        if channel in self.active_connections:
            for connection in self.active_connections[channel]:
                await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str):
    await manager.connect(websocket, channel)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive, or handle client messages
    except WebSocketDisconnect:
        manager.disconnect(websocket, channel)
        print(f"WebSocket disconnected for channel: {channel}")

class PostOut(BaseModel):
    id: int
    channel: str
    message_id: int
    text: Optional[str]
    media_url: Optional[str]
    posted_at: Optional[str]

@app.get("/api/posts/{channel}", response_model=List[PostOut])
def get_posts(channel: str, limit: int = 50, offset: int = 0):
    with Session(engine) as session:
        stmt = select(posts_table).where(posts_table.c.channel == channel).order_by(posts_table.c.posted_at.desc()).offset(offset).limit(limit)
        rows = session.execute(stmt).all()
        result = []
        for row in rows:
            media_url = None
            if row.media_path:
                # media_path is local path; map to /media/filename
                filename = os.path.basename(row.media_path)
                media_url = f"/media/{filename}"
            result.append(PostOut(
                id=row.id,
                channel=row.channel,
                message_id=row.message_id,
                text=row.text,
                media_url=media_url,
                posted_at=row.posted_at.isoformat() if row.posted_at else None
            ))
        return result

@app.get("/api/post/{channel}/{message_id}", response_model=PostOut)
def get_one(channel: str, message_id: int):
    with Session(engine) as session:
        stmt = select(posts_table).where((posts_table.c.channel==channel) & (posts_table.c.message_id==message_id))
        row = session.execute(stmt).first()
        if not row:
            raise HTTPException(status_code=404, detail="Post not found")
        media_url = None
        if row.media_path:
            filename = os.path.basename(row.media_path)
            media_url = f"/media/{filename}"
        return {
            "id": row.id,
            "channel": row.channel,
            "message_id": row.message_id,
            "text": row.text,
            "media_url": media_url,
            "posted_at": row.posted_at.isoformat() if row.posted_at else None
        }

@app.get("/api/posts_table")
def get_posts_table():
    with Session(engine) as session:
        stmt = select(posts_table).order_by(posts_table.c.posted_at.desc())
        rows = session.execute(stmt).all()
        result = []
        for row in rows:
            media_url = None
            if row.media_path:
                filename = os.path.basename(row.media_path)
                media_url = f"/media/{filename}"
            result.append({
                "id": row.id,
                "channel": row.channel,
                "message_id": row.message_id,
                "text": row.text,
                "media_url": media_url,
                "posted_at": row.posted_at.isoformat() if row.posted_at else None
            })
        return result

@app.post("/api/new_post/{channel}")
async def new_post_notification(channel: str, post: PostOut):
    await manager.broadcast(json.dumps(post.dict()), channel)
    return {"message": "Post broadcasted"}
