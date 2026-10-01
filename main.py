# -*- coding: utf-8 -*-
"""Sistemin merkezi: FastAPI app. Terminalda ishe salmaq ucun:
    uvicorn main:app --reload
"""

import os
import csv
import io
import urllib.parse
import secrets as secrets_mod
from html import escape as _esc
from typing import Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()
import uuid
import traceback
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import init_db, get_db, User, GenerationLog, Review, PageView, TokenPurchase
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


class PageViewRequest(BaseModel):
    utm_source: Optional[str] = None


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

    log = GenerationLog(
        user_id=user.id, movzu=project.movzu, universitet_adi=project.universitet_adi, status="pending",
    )
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


# ---------------- TRACKING ----------------

@app.post("/track/pageview")
def track_pageview(req: PageViewRequest, db: Session = Depends(get_db)):
    """Hec bir IP, hec bir sexsi melumat saxlanmir - yalniz utm_source (varsa)."""
    db.add(PageView(utm_source=req.utm_source or None))
    db.commit()
    return {"status": "ok"}


# ---------------- ADMIN ----------------

def _check_admin_key(admin_key: str):
    expected = os.environ.get("ADMIN_KEY")
    if not expected or not secrets_mod.compare_digest(admin_key, expected):
        raise HTTPException(status_code=403, detail="İcazə yoxdur")


@app.get("/admin/stats")
def admin_stats(admin_key: str, db: Session = Depends(get_db)):
    _check_admin_key(admin_key)

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


@app.get("/admin/users")
def admin_users(admin_key: str, db: Session = Depends(get_db)):
    _check_admin_key(admin_key)

    users = db.query(User).order_by(User.created_at.desc()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "created_at", "token_balance"])
    for u in users:
        writer.writerow([u.email, u.created_at, u.token_balance])

    return Response(content=buf.getvalue(), media_type="text/csv")


@app.post("/admin/reviews/{review_id}/approve")
def admin_approve_review(review_id: int, admin_key: str, db: Session = Depends(get_db)):
    _check_admin_key(admin_key)

    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Rəy tapılmadı")
    review.approved = True
    db.commit()
    return {"status": "ok"}


_ADMIN_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="az">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin Dashboard — SlaydAI</title>
<meta name="robots" content="noindex, nofollow">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#05070d; --panel:#0e1320; --panel-2:#121829; --border:rgba(255,255,255,.08);
    --border-soft:rgba(255,255,255,.05); --ink:#eef0f5; --sub:#8891a7; --sub-2:#5c6478;
    --gold:#d9ab5c; --gold-soft:#e9c98a; --green:#3ecf8e; --red:#f2685c; --amber:#f0b458;
    --blue:#5b8def; --radius:14px;
  }
  *{box-sizing:border-box;}
  body{
    margin:0; font-family:'Inter',-apple-system,Arial,sans-serif; background:var(--bg);
    color:var(--ink); font-variant-numeric:tabular-nums;
    background-image:radial-gradient(700px 400px at 100% -10%, rgba(217,171,92,.08), transparent 60%),
                      radial-gradient(600px 400px at 0% 0%, rgba(91,141,239,.06), transparent 60%);
    background-attachment:fixed;
  }
  .wrap{max-width:1180px; margin:0 auto; padding:36px 24px 80px;}
  header.top{display:flex; align-items:center; justify-content:space-between; margin-bottom:32px; flex-wrap:wrap; gap:14px;}
  .brand{font-size:15px; font-weight:700; letter-spacing:.2px; display:flex; align-items:center; gap:9px; color:var(--ink);}
  .brand .dot{width:8px; height:8px; border-radius:50%; background:var(--gold); box-shadow:0 0 10px var(--gold);}
  .brand .sep{color:var(--sub-2); font-weight:400;}
  .brand .tag{color:var(--sub); font-weight:500;}
  .btn-outline{
    font-size:12.5px; font-weight:600; color:var(--ink); background:var(--panel-2);
    border:1px solid var(--border); border-radius:9px; padding:9px 15px; text-decoration:none;
    display:inline-flex; align-items:center; gap:6px; transition:border-color .2s, background .2s;
  }
  .btn-outline:hover{border-color:rgba(217,171,92,.4); background:#161d31;}

  section{margin-bottom:38px;}
  .section-title{font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:.6px; color:var(--sub); margin:0 0 16px; display:flex; align-items:center; gap:8px;}
  .section-title .n{color:var(--sub-2); font-weight:500;}

  .panel{
    background:linear-gradient(180deg, var(--panel), var(--panel-2));
    border:1px solid var(--border); border-radius:var(--radius); padding:26px;
  }

  /* stat cards */
  .stats-grid{display:grid; grid-template-columns:repeat(5,1fr); gap:14px;}
  .stat-card{
    background:linear-gradient(180deg, var(--panel), var(--panel-2)); border:1px solid var(--border);
    border-radius:var(--radius); padding:20px 20px 18px; transition:transform .2s, border-color .2s;
  }
  .stat-card:hover{transform:translateY(-3px); border-color:rgba(217,171,92,.35);}
  .stat-card.accent{border-color:rgba(217,171,92,.4); background:linear-gradient(180deg, rgba(217,171,92,.1), var(--panel-2));}
  .stat-label{font-size:12px; color:var(--sub); font-weight:600; margin-bottom:10px;}
  .stat-value{font-size:30px; font-weight:800; letter-spacing:-.5px; color:var(--ink);}
  .stat-card.accent .stat-value{color:var(--gold-soft);}

  /* funnel */
  .funnel-wrap{max-width:560px; margin:0 auto;}
  .funnel-step{width:100%;}
  .funnel-bar{
    width:var(--w); min-width:150px; margin:0 auto; text-align:center;
    background:linear-gradient(135deg, var(--gold-soft), var(--gold)); color:#20140a;
    border-radius:10px; padding:16px 12px; transform-origin:center;
    animation:growIn .5s cubic-bezier(.2,.8,.2,1) both; animation-delay:calc(var(--i) * .09s);
  }
  @keyframes growIn{from{transform:scaleX(.3); opacity:0;} to{transform:scaleX(1); opacity:1;}}
  .funnel-count{font-size:21px; font-weight:800;}
  .funnel-label{text-align:center; font-size:12.5px; color:var(--sub); margin:9px 0 4px; font-weight:600;}
  .funnel-conv{text-align:center; font-size:12px; color:var(--sub-2); margin:2px 0 10px;}
  .funnel-conv b{color:var(--green);}

  /* tables */
  .table-scroll{overflow-x:auto;}
  table{width:100%; border-collapse:collapse; font-size:13.5px;}
  thead th{
    text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--sub-2);
    font-weight:700; padding:0 14px 10px; border-bottom:1px solid var(--border);
  }
  tbody td{padding:12px 14px; border-bottom:1px solid var(--border-soft); color:var(--ink);}
  tbody tr:nth-child(even){background:rgba(255,255,255,.015);}
  tbody tr:hover{background:rgba(217,171,92,.05);}
  td.empty{color:var(--sub-2); text-align:center; padding:24px; font-style:italic;}
  td.stars{color:var(--gold-soft); letter-spacing:1px;}

  .mini-bar{width:80px; height:6px; border-radius:4px; background:rgba(255,255,255,.06); overflow:hidden;}
  .mini-bar-fill{height:100%; background:var(--blue); border-radius:4px;}
  .mini-bar-fill.gold{background:var(--gold);}

  .badge{font-size:11px; font-weight:700; padding:4px 10px; border-radius:999px; display:inline-block;}
  .badge.ok{background:rgba(62,207,142,.15); color:var(--green);}
  .badge.pending{background:rgba(240,180,88,.15); color:var(--amber);}
  .btn-approve{
    font-size:11.5px; font-weight:700; color:#0e1320; background:var(--green); border:none;
    border-radius:7px; padding:6px 12px; cursor:pointer; margin-left:8px; transition:opacity .2s;
  }
  .btn-approve:hover{opacity:.85;}
  .btn-approve:disabled{opacity:.5; cursor:default;}

  .grid-2{display:grid; grid-template-columns:1fr 1fr; gap:20px;}

  @media (max-width:980px){
    .stats-grid{grid-template-columns:repeat(2,1fr);}
    .grid-2{grid-template-columns:1fr;}
  }
  @media (max-width:600px){
    .stats-grid{grid-template-columns:1fr;}
    .wrap{padding:24px 14px 60px;}
    .funnel-bar{min-width:0;}
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="top">
    <div class="brand"><span class="dot"></span> SlaydAI <span class="sep">/</span> <span class="tag">Admin Dashboard</span></div>
    <a class="btn-outline" href="/admin/users?admin_key=__ADMIN_KEY_URL__" target="_blank" rel="noopener">⬇ İstifadəçiləri CSV kimi yüklə</a>
  </header>

  <section>
    <div class="stats-grid">
      __STAT_CARDS__
    </div>
  </section>

  <section>
    <div class="section-title">Huni (Funnel)</div>
    <div class="panel funnel-wrap">
      __FUNNEL__
    </div>
  </section>

  <section class="grid-2">
    <div>
      <div class="section-title">Mənbə (UTM)</div>
      <div class="panel table-scroll">
        <table>
          <thead><tr><th>Mənbə</th><th>Sayı</th><th></th></tr></thead>
          <tbody>__UTM_ROWS__</tbody>
        </table>
      </div>
    </div>
    <div>
      <div class="section-title">Ən çox alınan token miqdarı</div>
      <div class="panel table-scroll">
        <table>
          <thead><tr><th>Miqdar</th><th>Sifariş sayı</th><th></th></tr></thead>
          <tbody>__QTY_ROWS__</tbody>
        </table>
      </div>
    </div>
  </section>

  <section>
    <div class="section-title">Ən çox universitetlər <span class="n">TOP 10</span></div>
    <div class="panel table-scroll">
      <table>
        <thead><tr><th>#</th><th>Universitet</th><th>Generasiya sayı</th></tr></thead>
        <tbody>__UNI_ROWS__</tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-title">İstifadəçilər <span class="n">son 50</span></div>
    <div class="panel table-scroll">
      <table>
        <thead><tr><th>Email</th><th>Qeydiyyat tarixi</th><th>Token balansı</th><th>Generasiya sayı</th></tr></thead>
        <tbody>__USERS_ROWS__</tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-title">Son generasiyalar <span class="n">son 20</span></div>
    <div class="panel table-scroll">
      <table>
        <thead><tr><th>Email</th><th>Mövzu</th><th>Tarix</th><th></th></tr></thead>
        <tbody>__RECENT_GEN_ROWS__</tbody>
      </table>
    </div>
  </section>

  <section>
    <div class="section-title">Rəylər</div>
    <div class="panel table-scroll">
      <table>
        <thead><tr><th>İstifadəçi</th><th>Reytinq</th><th>Şərh</th><th>Tarix</th><th>Status</th></tr></thead>
        <tbody>__REVIEWS_ROWS__</tbody>
      </table>
    </div>
  </section>

</div>

<script>
const ADMIN_KEY = '__ADMIN_KEY_JS__';
async function approveReview(id, btn) {
  btn.disabled = true;
  btn.innerText = "...";
  try {
    const res = await fetch(`/admin/reviews/${id}/approve?admin_key=${encodeURIComponent(ADMIN_KEY)}`, { method: "POST" });
    if (!res.ok) throw new Error("request failed");
    const row = document.getElementById(`review-row-${id}`);
    if (row) row.children[4].innerHTML = '<span class="badge ok">Təsdiqlənib</span>';
  } catch (e) {
    btn.disabled = false;
    btn.innerText = "Təsdiqlə";
    alert("Xəta baş verdi, yenidən cəhd et.");
  }
}
</script>
</body>
</html>
"""


@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(admin_key: str, db: Session = Depends(get_db)):
    _check_admin_key(admin_key)

    # ---------- 1) HUNI (FUNNEL) ----------
    total_page_views = db.query(PageView).count()
    total_users = db.query(User).count()
    users_with_generation = db.query(func.count(func.distinct(GenerationLog.user_id))).scalar() or 0
    total_payments = db.query(TokenPurchase).count()

    funnel_stages = [
        ("Səhifə baxışı", total_page_views),
        ("Qeydiyyat", total_users),
        ("Generasiya sifarişi", users_with_generation),
        ("Uğurlu ödəniş", total_payments),
    ]
    max_stage = max((c for _, c in funnel_stages), default=0) or 1
    funnel_parts = []
    for i, (label, count) in enumerate(funnel_stages):
        width_pct = max(10, round(count / max_stage * 100)) if count > 0 else 3
        if i == 0:
            funnel_parts.append(f'<div class="funnel-step" style="--w:{width_pct}%; --i:{i}">'
                                 f'<div class="funnel-bar"><span class="funnel-count">{count:,}</span></div>'
                                 f'<div class="funnel-label">{_esc(label)}</div></div>')
        else:
            prev_count = funnel_stages[i - 1][1]
            conv_pct = round(count / prev_count * 100, 1) if prev_count else 0.0
            funnel_parts.append(
                f'<div class="funnel-conv">↓ <b>{conv_pct}%</b> keçid</div>'
                f'<div class="funnel-step" style="--w:{width_pct}%; --i:{i}">'
                f'<div class="funnel-bar"><span class="funnel-count">{count:,}</span></div>'
                f'<div class="funnel-label">{_esc(label)}</div></div>'
            )
    funnel_html = "".join(funnel_parts)

    # ---------- 2) UTM MENBE ----------
    utm_raw = db.query(PageView.utm_source, func.count(PageView.id)).group_by(PageView.utm_source).all()
    utm_counts = {}
    for src, cnt in utm_raw:
        key = src if src else "direct"
        utm_counts[key] = utm_counts.get(key, 0) + cnt
    utm_sorted = sorted(utm_counts.items(), key=lambda x: -x[1])
    utm_total = sum(c for _, c in utm_sorted) or 1
    utm_html = "".join(
        f'<tr><td>{_esc(src)}</td><td>{cnt:,}</td>'
        f'<td><div class="mini-bar"><div class="mini-bar-fill" style="width:{round(cnt/utm_total*100)}%"></div></div></td></tr>'
        for src, cnt in utm_sorted
    ) or '<tr><td colspan="3" class="empty">Hələ məlumat yoxdur</td></tr>'

    # ---------- 3) EN COX UNIVERSITETLER ----------
    uni_rows = (
        db.query(GenerationLog.universitet_adi, func.count(GenerationLog.id).label("c"))
        .filter(GenerationLog.universitet_adi.isnot(None), GenerationLog.universitet_adi != "")
        .group_by(GenerationLog.universitet_adi)
        .order_by(func.count(GenerationLog.id).desc())
        .limit(10)
        .all()
    )
    uni_html = "".join(
        f"<tr><td>{i+1}</td><td>{_esc(name)}</td><td>{cnt:,}</td></tr>"
        for i, (name, cnt) in enumerate(uni_rows)
    ) or '<tr><td colspan="3" class="empty">Hələ məlumat yoxdur (köhnə qeydlərdə universitet adı saxlanılmayıb)</td></tr>'

    # ---------- 4) ISTIFADECILER (max 50) ----------
    users = db.query(User).order_by(User.created_at.desc()).limit(50).all()
    gen_counts = dict(
        db.query(GenerationLog.user_id, func.count(GenerationLog.id)).group_by(GenerationLog.user_id).all()
    )
    users_html = "".join(
        f'<tr><td>{_esc(u.email)}</td>'
        f'<td>{u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "-"}</td>'
        f'<td>{u.token_balance}</td>'
        f'<td>{gen_counts.get(u.id, 0)}</td></tr>'
        for u in users
    ) or '<tr><td colspan="4" class="empty">İstifadəçi yoxdur</td></tr>'

    # ---------- 4b) SON GENERASIYALAR (son 20, status=done) ----------
    recent_gens = (
        db.query(GenerationLog, User.email)
        .join(User, GenerationLog.user_id == User.id)
        .filter(GenerationLog.status == "done")
        .order_by(GenerationLog.created_at.desc())
        .limit(20)
        .all()
    )
    recent_gen_parts = []
    for log, email in recent_gens:
        if log.result_url:
            link_html = f'<a class="btn-outline" href="{_esc(log.result_url)}" target="_blank" rel="noopener">Gamma-da aç</a>'
        else:
            link_html = ""
        recent_gen_parts.append(
            f'<tr><td>{_esc(email)}</td><td>{_esc(log.movzu or "-")}</td>'
            f'<td>{log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else "-"}</td>'
            f'<td>{link_html}</td></tr>'
        )
    recent_gen_html = "".join(recent_gen_parts) or '<tr><td colspan="4" class="empty">Hələ tamamlanmış generasiya yoxdur</td></tr>'

    # ---------- 5) EN COX ALINAN TOKEN MIQDARI ----------
    qty_rows = (
        db.query(TokenPurchase.quantity, func.count(TokenPurchase.id).label("c"))
        .group_by(TokenPurchase.quantity)
        .order_by(func.count(TokenPurchase.id).desc())
        .all()
    )
    qty_total = sum(c for _, c in qty_rows) or 1
    qty_html = "".join(
        f'<tr><td>{q} token</td><td>{cnt:,}</td>'
        f'<td><div class="mini-bar"><div class="mini-bar-fill gold" style="width:{round(cnt/qty_total*100)}%"></div></div></td></tr>'
        for q, cnt in qty_rows
    ) or '<tr><td colspan="3" class="empty">Hələ ödəniş yoxdur</td></tr>'

    # ---------- 6) REYLER (hamisi) ----------
    review_rows = (
        db.query(Review, User.email)
        .join(User, Review.user_id == User.id)
        .order_by(Review.created_at.desc())
        .all()
    )
    reviews_parts = []
    for r, email in review_rows:
        stars = "★" * r.rating + "☆" * (5 - r.rating)
        if r.approved:
            status_html = '<span class="badge ok">Təsdiqlənib</span>'
        else:
            status_html = (
                '<span class="badge pending">Gözləyir</span> '
                f'<button class="btn-approve" onclick="approveReview({r.id}, this)">Təsdiqlə</button>'
            )
        reviews_parts.append(
            f'<tr id="review-row-{r.id}"><td>{_esc(email)}</td><td class="stars">{stars}</td>'
            f'<td>{_esc(r.comment or "—")}</td>'
            f'<td>{r.created_at.strftime("%Y-%m-%d") if r.created_at else "-"}</td>'
            f'<td>{status_html}</td></tr>'
        )
    reviews_html = "".join(reviews_parts) or '<tr><td colspan="5" class="empty">Rəy yoxdur</td></tr>'

    # ---------- 7) UMUMI STATISTIKA KARTLARI ----------
    total_generations = db.query(GenerationLog).count()
    done_generations = db.query(GenerationLog).filter(GenerationLog.status == "done").count()
    success_rate = round(done_generations / total_generations * 100, 1) if total_generations else 0.0

    total_revenue_cents = 0
    for (qty,) in db.query(TokenPurchase.quantity).all():
        try:
            total_revenue_cents += stripe_routes.calculate_price(qty)
        except ValueError:
            pass
    total_revenue = total_revenue_cents / 100

    stat_cards_html = f"""
      <div class="stat-card"><div class="stat-label">İstifadəçilər</div><div class="stat-value">{total_users:,}</div></div>
      <div class="stat-card"><div class="stat-label">Generasiyalar</div><div class="stat-value">{total_generations:,}</div></div>
      <div class="stat-card"><div class="stat-label">Uğur faizi</div><div class="stat-value">{success_rate}%</div></div>
      <div class="stat-card"><div class="stat-label">Ödənişlər</div><div class="stat-value">{total_payments:,}</div></div>
      <div class="stat-card accent"><div class="stat-label">Cəmi gəlir</div><div class="stat-value">${total_revenue:,.2f}</div></div>
    """

    admin_key_url = urllib.parse.quote(admin_key, safe="")
    admin_key_js = admin_key.replace("\\", "\\\\").replace("'", "\\'")

    page = _ADMIN_DASHBOARD_TEMPLATE
    page = page.replace("__STAT_CARDS__", stat_cards_html)
    page = page.replace("__FUNNEL__", funnel_html)
    page = page.replace("__UTM_ROWS__", utm_html)
    page = page.replace("__UNI_ROWS__", uni_html)
    page = page.replace("__USERS_ROWS__", users_html)
    page = page.replace("__RECENT_GEN_ROWS__", recent_gen_html)
    page = page.replace("__QTY_ROWS__", qty_html)
    page = page.replace("__REVIEWS_ROWS__", reviews_html)
    page = page.replace("__ADMIN_KEY_URL__", admin_key_url)
    page = page.replace("__ADMIN_KEY_JS__", admin_key_js)
    return page


# ---------------- STRIPE YONLENDIRME SEHIFELERI ----------------

@app.get("/payment-success", response_class=HTMLResponse)
def payment_success(amount: float = 0.0):
    # amount /stripe/buy-de hesablanmis heqiqi mebleg kimi Stripe-in success_url-ine
    # query param olaraq gelir (stripe_routes.py-e bax) - FastAPI onu avtomatik
    # float-a cevirir, ona gore burda hec bir elave sanitizasiya lazim deyil.
    purchase_value = f"{amount:.2f}"
    return """
    <html><head><meta charset="utf-8">
    <meta http-equiv="refresh" content="3;url=https://slaydyarat.pro">
    <style>
      body{font-family:-apple-system,sans-serif;background:#081729;color:#f3f0ea;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
      .box{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);
        border-radius:18px;padding:48px 36px;max-width:420px}
      .check{font-size:48px;margin-bottom:12px}
      h2{margin:0 0 12px}
      p{color:rgba(243,240,234,.7);line-height:1.6}
      a{display:inline-block;margin-top:20px;padding:12px 28px;border-radius:12px;
        background:linear-gradient(135deg,#e9c98a,#d9ab5c);color:#20140a;
        font-weight:700;text-decoration:none}
    </style></head>
    <body><div class="box">
      <div class="check">✓</div>
      <h2>Ödəniş uğurla tamamlandı!</h2>
      <p>Tokenləriniz hesabınıza əlavə olundu. 3 saniyə sonra avtomatik
      yönləndiriləcəksiniz.</p>
      <a href="https://slaydyarat.pro">İndi qayıt</a>
    </div>
    <script>
    !function(f,b,e,v,n,t,s)
    {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
    n.callMethod.apply(n,arguments):n.queue.push(arguments)};
    if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
    n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];
    s.parentNode.insertBefore(t,s)}(window, document,'script',
    'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', '1898222571587194');
    fbq('track', 'Purchase', {currency: 'USD', value: """ + purchase_value + """});
    </script>
    </body></html>
    """


@app.get("/payment-cancelled", response_class=HTMLResponse)
def payment_cancelled():
    return """
    <html><head><meta charset="utf-8">
    <meta http-equiv="refresh" content="3;url=https://slaydyarat.pro">
    <style>
      body{font-family:-apple-system,sans-serif;background:#081729;color:#f3f0ea;
        display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}
      .box{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);
        border-radius:18px;padding:48px 36px;max-width:420px}
      h2{margin:0 0 12px}
      p{color:rgba(243,240,234,.7);line-height:1.6}
      a{display:inline-block;margin-top:20px;padding:12px 28px;border-radius:12px;
        background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.14);
        color:#f3f0ea;font-weight:600;text-decoration:none}
    </style></head>
    <body><div class="box">
      <h2>Ödəniş ləğv edildi</h2>
      <p>İstədiyiniz zaman yenidən cəhd edə bilərsiniz. 3 saniyə sonra
      avtomatik yönləndiriləcəksiniz.</p>
      <a href="https://slaydyarat.pro">Sayta qayıt</a>
    </div></body></html>
    """
