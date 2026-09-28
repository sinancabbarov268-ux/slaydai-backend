# -*- coding: utf-8 -*-
"""Sistemin merkezi: FastAPI app. Terminalda ishe salmaq ucun:
    uvicorn main:app --reload
"""

import os
import secrets as secrets_mod
from typing import Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
import uuid
import traceback
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import init_db, get_db, User, GenerationLog, Review
from auth import hash_password, verify_password, get_current_user
from pipeline import run_full_pipeline
import stripe_routes

app = FastAPI(title="Akademik Slayd Generasiya Sistemi")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # production-da bunu oz domeninle mehdudlashdir
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stripe_routes.router, prefix="/stripe", tags=["stripe"])

OUTPUT_DIR = "generated_files"
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=OUTPUT_DIR), name="files")


@app.on_event("startup")
def on_startup():
    init_db()


# ---------------- SXEMLƏR ----------------

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ReviewRequest(BaseModel):
    generation_id: int
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class FeedbackRequest(BaseModel):
    feedback: str  # "up" / "down"


class ProjectRequest(BaseModel):
    universitet_adi: str
    logo_path: Optional[str] = None
    fakulte: str
    kafedra: str
    ixtisas: str
    fenn: str
    movzu: str
    kurs: str
    qrup: str
    muellim: str
    telebe: str
    dil: str = "Azərbaycan dili"
    format: str = "16:9 (widescreen)"
    menbe_secimi: str = "ai_axtarsin"
    istifadeci_menbeleri: list[str] = []
    slayd_sayi_hedefi: int = Field(default=12, ge=5, le=15)


# ---------------- QEYDİYYAT / GİRİŞ ----------------

MAX_SIGNUPS_PER_IP_30D = 2


@app.post("/register")
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Bu e-poct artiq qeydiyyatdan kecib")

    client_ip = request.client.host if request.client else None
    recent_signups = 0
    if client_ip:
        cutoff = datetime.utcnow() - timedelta(days=30)
        recent_signups = db.query(User).filter(
            User.signup_ip == client_ip,
            User.created_at >= cutoff,
        ).count()

    initial_balance = 0 if recent_signups > MAX_SIGNUPS_PER_IP_30D else 1

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        token_balance=initial_balance,
        signup_ip=client_ip,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"api_token": user.api_token, "email": user.email, "token_balance": user.token_balance}


@app.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-poct ve ya sifre yanlishdir")
    return {"api_token": user.api_token, "token_balance": user.token_balance}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "token_balance": user.token_balance}


# ---------------- SLAYD GENERASİYASI ----------------

def _run_generation_job(log_id: int, project: dict):
    """Arxa planda ishleyir (BackgroundTasks) - istifadeciyi gozletmir."""
    from database import SessionLocal
    db = SessionLocal()
    log = db.query(GenerationLog).filter(GenerationLog.id == log_id).first()
    try:
        output_path = os.path.join(OUTPUT_DIR, f"{uuid.uuid4().hex}_slides.json")
        result = run_full_pipeline(project, output_path)
        log.status = "done"
        log.file_path = output_path
        log.result_url = result.get("result_url")
    except Exception as e:
        log.status = "error"
        log.error_message = f"{e}\n{traceback.format_exc()[:500]}"
        user = db.query(User).filter(User.id == log.user_id).first()
        if user:
            user.token_balance += 1  # ugursuz generasiya ucun token geri qaytarilir
    db.commit()
    db.close()


@app.post("/generate")
def generate_slides(
    project: ProjectRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.token_balance <= 0:
        raise HTTPException(status_code=402, detail="Token balansiniz kifayet etmir. Elave token alin.")

    # token-i evvelceden azaldiriq ki, ayni anda ikici sorgu ile "pulsuz" generasiya olunmasin
    user.token_balance -= 1
    db.commit()

    log = GenerationLog(user_id=user.id, movzu=project.movzu, status="pending")
    db.add(log)
    db.commit()
    db.refresh(log)

    background_tasks.add_task(_run_generation_job, log.id, project.model_dump())

    return {"generation_id": log.id, "status": "pending", "message": "Generasiya basladi, biraz sonra yoxla."}


@app.get("/generate/{generation_id}")
def check_generation(generation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log = db.query(GenerationLog).filter(
        GenerationLog.id == generation_id, GenerationLog.user_id == user.id
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Tapilmadi")

    result = {"status": log.status, "movzu": log.movzu}
    if log.status == "done":
        filename = os.path.basename(log.file_path)
        result["download_url"] = f"/files/{filename}"  # xam slides_markdown.json (arxiv/debug ucun)
        result["result_url"] = log.result_url  # Gamma-nin hazir .pptx linki (esas yukleme menbeyi)
    elif log.status == "error":
        result["error"] = log.error_message
    return result


@app.post("/generate/{generation_id}/downloaded")
def mark_downloaded(generation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log = db.query(GenerationLog).filter(
        GenerationLog.id == generation_id, GenerationLog.user_id == user.id
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Tapilmadi")
    log.downloaded = True
    log.download_clicked_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


@app.post("/generate/{generation_id}/feedback")
def submit_feedback(
    generation_id: int,
    req: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if req.feedback not in ("up", "down"):
        raise HTTPException(status_code=400, detail="feedback 'up' ve ya 'down' olmalidir")
    log = db.query(GenerationLog).filter(
        GenerationLog.id == generation_id, GenerationLog.user_id == user.id
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Tapilmadi")
    log.feedback = req.feedback
    db.commit()
    return {"status": "ok"}


# ---------------- RƏYLƏR ----------------

@app.post("/reviews")
def create_review(req: ReviewRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log = db.query(GenerationLog).filter(
        GenerationLog.id == req.generation_id, GenerationLog.user_id == user.id
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Bu generasiya sizin hesabınıza aid deyil və ya tapılmadı")

    existing = db.query(Review).filter(Review.generation_id == req.generation_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Bu generasiya üçün artıq rəy yazılıb")

    review = Review(
        user_id=user.id,
        generation_id=req.generation_id,
        rating=req.rating,
        comment=req.comment,
        approved=False,
    )
    db.add(review)
    user.token_balance += 1
    db.commit()
    db.refresh(review)

    return {
        "id": review.id,
        "message": "Rəyiniz üçün təşəkkürlər! 1 pulsuz token hesabınıza əlavə olundu.",
        "token_balance": user.token_balance,
    }


@app.get("/reviews")
def list_reviews(db: Session = Depends(get_db)):
    reviews = (
        db.query(Review)
        .filter(Review.approved == True)  # noqa: E712
        .order_by(Review.created_at.desc())
        .all()
    )
    return [
        {"id": r.id, "rating": r.rating, "comment": r.comment, "created_at": r.created_at}
        for r in reviews
    ]


# ---------------- ADMIN ----------------

@app.get("/admin/stats")
def admin_stats(admin_key: str, db: Session = Depends(get_db)):
    expected = os.environ.get("ADMIN_KEY")
    if not expected or not secrets_mod.compare_digest(admin_key, expected):
        raise HTTPException(status_code=403, detail="İcazə yoxdur")

    total = db.query(GenerationLog).count()
    done = db.query(GenerationLog).filter(GenerationLog.status == "done").count()
    error = db.query(GenerationLog).filter(GenerationLog.status == "error").count()
    downloaded = db.query(GenerationLog).filter(GenerationLog.downloaded == True).count()  # noqa: E712
    feedback_up = db.query(GenerationLog).filter(GenerationLog.feedback == "up").count()
    feedback_down = db.query(GenerationLog).filter(GenerationLog.feedback == "down").count()

    return {
        "total_generations": total,
        "success_rate": round(done / total, 4) if total else 0,
        "error_rate": round(error / total, 4) if total else 0,
        "download_rate": round(downloaded / total, 4) if total else 0,
        "feedback_up": feedback_up,
        "feedback_down": feedback_down,
    }
