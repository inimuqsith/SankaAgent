"""Satu MetaData untuk seluruh state jiwa; tipe portabel Postgres/SQLite."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

BigIntPK = BigInteger().with_variant(Integer, "sqlite")
Json = JSON().with_variant(JSONB, "postgresql")

metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("name", String(120), primary_key=True),
    Column("content", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

schema_meta = Table(
    "schema_meta",
    metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
)

messages = Table(
    "messages",
    metadata,
    Column("id", BigIntPK, primary_key=True, autoincrement=True),
    Column("chat_id", String(120), nullable=False),
    Column("msg_id", String(160), nullable=False),
    Column("ts", Float, nullable=False),
    Column("sender_id", String(80), nullable=False, default=""),
    Column("sender_name", String(160), nullable=False, default=""),
    Column("sender_address", String(160), nullable=False, default=""),
    Column("text", Text, nullable=False, default=""),
    Column("outgoing", Boolean, nullable=False, default=False),
    Column("author", String(20), nullable=False, default="unknown", server_default="unknown"),
    Column("visibility", Integer, nullable=False, default=2, server_default="2"),
    Column("deleted", Boolean, nullable=False, default=False, server_default=text("false")),
    Column("deleted_by", String(160), nullable=False, default="", server_default=""),
    Column("deleted_admin", Boolean, nullable=False, default=False, server_default=text("false")),
    Column("data", Json, nullable=False, default=dict),
    Column("media_ref", LargeBinary, nullable=True),
    Column("quote_media_ref", LargeBinary, nullable=True),
    UniqueConstraint("chat_id", "msg_id", name="uq_messages_chat_msg"),
)
Index("ix_messages_chat_ts", messages.c.chat_id, messages.c.ts, messages.c.id)
Index("ix_messages_sender", messages.c.sender_address)

chats = Table(
    "chats",
    metadata,
    Column("chat_id", String(120), primary_key=True),
    Column("name", String(200), nullable=False, default=""),
    Column("reset_ts", Float, nullable=False, default=0.0),
    Column("kind", String(20), nullable=False, default="dm"),
)

state = Table(
    "state",
    metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

memory_legacy = Table(
    "memory_legacy",
    metadata,
    Column("id", BigIntPK, primary_key=True, autoincrement=True),
    Column("scope", String(160), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("text", Text, nullable=False),
    Column("source", String(200), nullable=False, default=""),
    Column("valence", Float, nullable=True),
    Column("arousal", Float, nullable=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("superseded_by", BigIntPK, nullable=True),
    Column("migrated_at", Float, nullable=True),
    UniqueConstraint("scope", "kind", "text", name="uq_memory_legacy_scope_kind_text"),
)

actions = Table(
    "actions",
    metadata,
    Column("id", BigIntPK, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("source", String(120), nullable=False),
    Column("source_name", String(160), nullable=False, default=""),
    Column("target", String(160), nullable=False, default=""),
    Column("target_name", String(200), nullable=False, default=""),
    Column("kind", String(40), nullable=False, default="send"),
    Column("summary", Text, nullable=False, default=""),
    Column("request", Text, nullable=False, default=""),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", BigIntPK, primary_key=True, autoincrement=True),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("chat_id", String(120), nullable=False),
    Column("sender", String(160), nullable=False),
    Column("gateway", String(40), nullable=False, default=""),
    Column("kind", String(40), nullable=False),
    Column("tool", String(80), nullable=False, default=""),
    Column("args", Json, nullable=False, default=dict),
    Column("payload", Json, nullable=False, default=dict),
    Column("summary", Text, nullable=False, default=""),
    Column("diff", Text, nullable=False, default=""),
    Column("status", String(20), nullable=False, default="pending"),
    Column("decided_by", String(160), nullable=False, default=""),
    Column("decided_at", Float, nullable=True),
    Column("result", Text, nullable=False, default=""),
)
Index("ix_approvals_status", approvals.c.status, approvals.c.created_at)

people = Table(
    "people",
    metadata,
    Column("person_id", String(80), primary_key=True),
    Column("name", String(160), nullable=False, default=""),
    Column("aliases", Json, nullable=False, default=list),
    Column("role", String(40), nullable=False, default=""),
    Column("stage", String(40), nullable=False, default=""),
    Column("trust", Float, nullable=False, default=0.0),
    Column("affection", Float, nullable=False, default=0.0),
    Column("respect", Float, nullable=False, default=0.0),
    Column("annoyance", Float, nullable=False, default=0.0),
    Column("familiarity", Float, nullable=False, default=0.0),
    Column("interactions", Integer, nullable=False, default=0),
    Column("appease", Float, nullable=False, default=0.0),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("window_start", Float, nullable=False, default=0.0),
    Column("window_trust", Float, nullable=False, default=0.0),
    Column("window_affection", Float, nullable=False, default=0.0),
    Column("window_respect", Float, nullable=False, default=0.0),
    Column("window_annoyance", Float, nullable=False, default=0.0),
    Column("notes", Json, nullable=False, default=list),
)

topics = Table(
    "topics",
    metadata,
    Column("chat_id", String(120), primary_key=True),
    Column("summary", Text, nullable=False, default=""),
    Column("keywords", Json, nullable=False, default=list),
    Column("unresolved", Json, nullable=False, default=list),
    Column("pending", Integer, nullable=False, default=0),
    Column("last_message", Float, nullable=False, default=0.0),
    Column("last_digest", Float, nullable=False, default=0.0),
    Column("last_research", Float, nullable=False, default=0.0),
    Column("research_day", String(20), nullable=False, default=""),
    Column("research_count", Integer, nullable=False, default=0),
)

drives = Table(
    "drives",
    metadata,
    Column("chat_id", String(120), primary_key=True),
    Column("energy", Float, nullable=False, default=0.8),
    Column("boredom", Float, nullable=False, default=0.2),
    Column("loneliness", Float, nullable=False, default=0.2),
    Column("last_pulse", Float, nullable=False, default=0.0),
)

chat_people = Table(
    "chat_people",
    metadata,
    Column("chat_id", String(120), nullable=False),
    Column("person_id", String(80), nullable=False),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("interactions", Integer, nullable=False, default=0),
    PrimaryKeyConstraint("chat_id", "person_id", name="pk_chat_people"),
)

skills = Table(
    "skills",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("description", String(400), nullable=False, default=""),
    Column("body", Text, nullable=False, default=""),
    Column("archived", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

skill_files = Table(
    "skill_files",
    metadata,
    Column("skill", String(80), nullable=False),
    Column("path", String(300), nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("updated_at", Float, nullable=False),
    PrimaryKeyConstraint("skill", "path", name="pk_skill_files"),
)

skill_usage = Table(
    "skill_usage",
    metadata,
    Column("skill", String(80), primary_key=True),
    Column("uses", Integer, nullable=False, default=0),
    Column("last", Float, nullable=False, default=0.0),
)

recipes = Table(
    "recipes",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("body", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

config_changes = Table(
    "config_changes",
    metadata,
    Column("id", BigIntPK, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("actor", String(160), nullable=False, default=""),
    Column("file", String(80), nullable=False),
    Column("path", String(200), nullable=False, default=""),
    Column("old", Text, nullable=False, default=""),
    Column("new", Text, nullable=False, default=""),
)

engine_lease = Table(
    "engine_lease",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("instance_id", String(80), nullable=False, default=""),
    Column("host", String(200), nullable=False, default=""),
    Column("pid", Integer, nullable=False, default=0),
    Column("mode", String(20), nullable=False, default="primary"),
    Column("started_at", Float, nullable=False, default=0.0),
    Column("heartbeat_at", Float, nullable=False, default=0.0),
)

engine_control = Table(
    "engine_control",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("frozen", Boolean, nullable=False, default=False),
    Column("frozen_by", String(120), nullable=False, default=""),
    Column("frozen_at", Float, nullable=False, default=0.0),
)
