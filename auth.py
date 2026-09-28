# -*- coding: utf-8 -*-
"""Sade autentifikasiya: qeydiyyat, giris, token yoxlanmasi."""

from fastapi import Depends, HTTPException, Header
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """Basliqda 'Authorization: Bearer <api_token>' gozlenilir."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Giris teleb olunur (Authorization basligi yoxdur)")
    token = authorization.replace("Bearer ", "").strip()
    user = db.query(User).filter(User.api_token == token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Etibarsiz token")
    return user
