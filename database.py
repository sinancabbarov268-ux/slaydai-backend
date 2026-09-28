# -*- coding: utf-8 -*-
"""Verilenler bazasi: istifadeci ve token balansi."""

import os
import secrets
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, func, text
from sqlalchemy.orm import declarative_base, sessionmaker

# Render (ve ya baska hostinq) DATABASE_URL mühit dəyişəni ilə Postgres verir -
# varsa onu istifadə edirik, yoxdursa (lokal test üçün) sqlite-a geri düşürük.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./app.db")
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy 1.4+ "postgres://" schema-sini artiq qebul etmir + mütləq psycopg2
    # dialektini (psycopg v3 yox) istifadə etmək üçün "+psycopg2" elave edirik.
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# check_same_thread YALNIZ SQLite-a aiddir - psycopg2 (Postgres) bunu tanimir.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    api_token = Column(String, unique=True, index=True, default=lambda: secrets.token_hex(24))
    token_balance = Column(Integer, default=0)  # neçe slayd generasiyasi haqqi qalib
    signup_ip = Column(String, nullable=True)  # ilk pulsuz generasiya qorumasi ucun
    created_at = Column(DateTime, server_default=func.now())


class GenerationLog(Base):
    __tablename__ = "generation_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    movzu = Column(String)
    status = Column(String, default="pending")  # pending / done / error
    file_path = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    downloaded = Column(Boolean, default=False)
    download_clicked_at = Column(DateTime, nullable=True)
    feedback = Column(String, nullable=True)  # "up" / "down" / None
    result_url = Column(String, nullable=True)  # Gamma-nin exportUrl-i (pptx linki)
    created_at = Column(DateTime, server_default=func.now())


class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    generation_id = Column(Integer, index=True, nullable=False)
    rating = Column(Integer, nullable=False)  # 1-5
    comment = Column(String, nullable=True)
    approved = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


# Yeni sutunlarin (yuxaridaki modellere elave olunanlarin) siyahisi - SQLite-da
# Base.metadata.create_all() movcud cedvellere YENI SUTUN elave ETMIR (yalniz
# movcud olmayan cedvelleri yaradir), ona gore movcud app.db-de bu sutunlari
# ALTER TABLE ile elave etmek lazimdir ki, kohne melumat itmesin.
_NEW_COLUMNS = {
    "users": [("signup_ip", "VARCHAR")],
    "generation_logs": [
        ("downloaded", "BOOLEAN DEFAULT 0"),
        ("download_clicked_at", "DATETIME"),
        ("feedback", "VARCHAR"),
        ("result_url", "VARCHAR"),
    ],
}


def _migrate_missing_columns():
    """NEZERE AL: "PRAGMA table_info" YALNIZ SQLite-a aiddir. Postgres-de (Render)
    verilenler bazasi onsuz da hemise BOS/TEZE olacaq (create_all() butun sutunlari
    ozu yaradir), ona gore bu funksiya YALNIZ SQLite altinda cagirilir (asagida)."""
    with engine.connect() as conn:
        for table, columns in _NEW_COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col_name, col_def in columns:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
        conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        _migrate_missing_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
