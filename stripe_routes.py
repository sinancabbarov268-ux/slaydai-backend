# -*- coding: utf-8 -*-
"""Stripe: token paketi satisi ve webhook ile balansin artirilmasi."""

import os
import stripe
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from sqlalchemy.orm import Session

from database import get_db, User
from auth import get_current_user

router = APIRouter()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
DOMAIN = os.environ.get("PUBLIC_DOMAIN", "http://localhost:8000")

# Vahid qiymet (sent ile) miqdara gore pilləli endirim - sabit paketler evezine
# elastik miqdar sistemi: istifadeci 1-10 arasi istediyi miqdari sece biler.
MAX_QUANTITY = 10


def calculate_price(quantity: int) -> int:
    """Verilen miqdar ucun CƏMİ qiymeti (sent ile) qaytarir. quantity 1-10
    araliginda olmalidir, eks halda ValueError."""
    if quantity < 1 or quantity > MAX_QUANTITY:
        raise ValueError(f"quantity 1-{MAX_QUANTITY} araliginda olmalidir (verilen: {quantity})")

    if quantity == 1:
        unit = 399
    elif quantity <= 4:
        unit = 349
    elif quantity <= 7:
        unit = 299
    else:
        unit = 249
    return unit * quantity


@router.post("/buy")
def create_checkout(
    quantity: int = Query(..., ge=1, le=MAX_QUANTITY),
    user: User = Depends(get_current_user),
):
    try:
        total_cents = calculate_price(quantity)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"{quantity} sorğu paketi"},
                "unit_amount": total_cents,
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=f"{DOMAIN}/payment-success",
        cancel_url=f"{DOMAIN}/payment-cancelled",
        metadata={"user_email": user.email, "tokens": quantity},
    )
    return {"checkout_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Etibarsiz webhook imzasi")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session["metadata"]["user_email"]
        tokens = int(session["metadata"]["tokens"])

        user = db.query(User).filter(User.email == email).first()
        if user:
            user.token_balance += tokens
            db.commit()

    return {"status": "ok"}
