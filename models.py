# models.py
from sqlalchemy import (
    MetaData, Table, Column, Integer, String, Text, DateTime, create_engine
)
from sqlalchemy.sql import func
from sqlalchemy.orm import registry

mapper_registry = registry()
metadata = MetaData()

posts_table = Table(
    "posts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("channel", String(255), nullable=False, index=True),
    Column("message_id", Integer, nullable=False, index=True),
    Column("text", Text, nullable=True),
    Column("media_path", String(1000), nullable=True),
    Column("posted_at", DateTime(timezone=True), server_default=func.now()),
)

# ORM model (optional convenience)
class Post:
    def __init__(self, channel, message_id, text=None, media_path=None, posted_at=None):
        self.channel = channel
        self.message_id = message_id
        self.text = text
        self.media_path = media_path
        self.posted_at = posted_at

mapper_registry.map_imperatively(Post, posts_table)
