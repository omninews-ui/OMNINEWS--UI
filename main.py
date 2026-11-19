import asyncio
import base64
import json
import os
import random
import re
import string
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="LAIO_APP: Live AI Orchestra, SGES & CGES Platform", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENVIRONMENT CONFIGURATION ---
MPESA_MODE = os.getenv("MPESA_MODE", "mock").lower()  # "mock", "sandbox", or "production"
PLATFORM_FEE_PERCENT = float(os.getenv("PLATFORM_FEE_PERCENT", "10.0"))

DARAJA_CONSUMER_KEY = os.getenv("DARAJA_CONSUMER_KEY", "")
DARAJA_CONSUMER_SECRET = os.getenv("DARAJA_CONSUMER_SECRET", "")
DARAJA_SHORTCODE = os.getenv("DARAJA_SHORTCODE", "174379")
DARAJA_PASSKEY = os.getenv("DARAJA_PASSKEY", "")
DARAJA_B2C_SHORTCODE = os.getenv("DARAJA_B2C_SHORTCODE", "600988")
DARAJA_INITIATOR_NAME = os.getenv("DARAJA_INITIATOR_NAME", "testapi")
DARAJA_SECURITY_CREDENTIAL = os.getenv("DARAJA_SECURITY_CREDENTIAL", "")
DARAJA_CALLBACK_URL = os.getenv("DARAJA_CALLBACK_URL", "https://onrender.com")

DARAJA_BASE_URL = (
    "https://safaricom.co.ke"
    if MPESA_MODE == "production"
    else "https://safaricom.co.ke"
)

# --- LOCALIZED 5-TIER STIPEND CONFIGURATION ---
LOCALE_CONFIGS = {
    "KE": {
        "currency": "KES",
        "symbol": "Ksh.",
        "tiers":,
        "tier_labels": ["Applause", "Warmth", "Encore", "Maestro", "Patron"],
        "gateway": "MPESA",
        "prompt": "Ikiwa umeguswa, tafadhali msaidie mbunifu huyu:",
        "prompt_en": "If you feel inspired, kindly support this creator:",
        "placeholder": "Enter M-Pesa No. (e.g. 0712345678 or 254712345678)",
    },
    "US": {
        "currency": "USD",
        "symbol": "$",
        "tiers":,
        "tier_labels": ["Applause", "Warmth", "Encore", "Maestro", "Patron"],
        "gateway": "STRIPE_US",
        "prompt": "If you feel inspired, kindly support this creator:",
        "prompt_en": "If you feel inspired, kindly support this creator:",
        "placeholder": "Enter Email or Phone for Receipt",
    },
    "BR": {
        "currency": "BRL",
        "symbol": "R$",
        "tiers":,
        "tier_labels": ["Aplauso", "Carinho", "Bis", "Maestro", "Patrono"],
        "gateway": "PIX_STRIPE_BR",
        "prompt": "Se você se sentiu inspirado, apoie este criador:",
        "prompt_en": "If you feel inspired, kindly support this creator:",
        "placeholder": "Chave PIX ou E-mail para recibo",
    },
}

# --- IN-MEMORY DATABASES ---
CREATORS_DB = {
    "STORY_001": {
        "creator_id": "CR_101",
        "name": "Mzee Juma & Ensemble",
        "role": "Senior Percussionist",
        "mpesa_phone": "254708374149",
    },
    "STORY_002": {
        "creator_id": "CR_102",
        "name": "Amina Ochieng",
        "role": "Vocalist & Spoken Word",
        "mpesa_phone": "254712345678",
    },
    "PILOT": {
        "creator_id": "CR_001",
        "name": "PILOT",
        "role": "LAIO Global Architect",
        "mpesa_phone": "254712345678",
    },
}

TRANSACTIONS_DB: Dict[str, dict] = {}
SGES_LEDGER: List[dict] = []

# --- FILE RESOLUTION HELPER ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIRS = [BASE_DIR, BASE_DIR / "laio_app", BASE_DIR.parent / "laio_app"]


def find_html_file(filename: str) -> Optional[Path]:
    for folder in STATIC_DIRS:
        candidate = folder / filename
        if candidate.exists():
            return candidate
    return None


# --- STATIC & HTML ROUTES ---
@app.get("/")
async def serve_hub():
    hub_path = find_html_file("index.html")
    if hub_path:
        return FileResponse(hub_path)
    widget_path = find_html_file("TipWidget.html")
    if widget_path:
        return FileResponse(widget_path)
    return {"status": "LAIO_APP Online", "endpoints": ["/tip-widget", "/api/locale-config", "/api/economy/status"]}


@app.get("/tip-widget")
@app.get("/TipWidget.html")
async def serve_tip_widget():
    widget_path = find_html_file("TipWidget.html")
    if widget_path:
        return FileResponse(widget_path)
    raise HTTPException(status_code=404, detail="TipWidget.html not found.")


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "mode": MPESA_MODE}


# --- HELPERS: VALIDATION, SPLITS & DARAJA ---
def normalize_kenyan_phone(raw_phone: str) -> str:
    digits = re.sub(r"\D", "", raw_phone.strip())
    if digits.startswith("0") and len(digits) == 10 and digits[1] in ("7", "1"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in ("7", "1"):
        digits = "254" + digits

    if not re.match(r"^254(7|1)\d{8}$", digits):
        raise HTTPException(
            status_code=400,
            detail="Invalid Kenyan M-Pesa number. Use 07XXXXXXXX, 01XXXXXXXX, or 2547XXXXXXXX.",
        )
    return digits


def calculate_stipend_split(gross_amount: float) -> dict:
    fee = round(gross_amount * (PLATFORM_FEE_PERCENT / 100.0), 2)
    net_payout = round(gross_amount - fee, 2)
    return {
        "gross_amount": gross_amount,
        "platform_fee_percent": PLATFORM_FEE_PERCENT,
        "platform_fee": fee,
        "creator_net_payout": net_payout,
    }


def generate_mock_mpesa_receipt() -> str:
    chars = string.ascii_uppercase + string.digits
    return "TK" + "".join(random.choices(chars, k=8))


async def get_daraja_access_token() -> str:
    url = f"{DARAJA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, auth=(DARAJA_CONSUMER_KEY, DARAJA_CONSUMER_SECRET))
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to authenticate with Safaricom Daraja.")
        return resp.json()["access_token"]


async def trigger_mpesa_stk_push(phone_number: str, amount: int, account_ref: str) -> dict:
    token = await get_daraja_access_token()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password_str = f"{DARAJA_SHORTCODE}{DARAJA_PASSKEY}{timestamp}"
    password = base64.b64encode(password_str.encode()).decode("utf-8")

    payload = {
        "BusinessShortCode": DARAJA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone_number,
        "PartyB": DARAJA_SHORTCODE,
        "PhoneNumber": phone_number,
        "CallBackURL": DARAJA_CALLBACK_URL,
        "AccountReference": account_ref[:12],
        "TransactionDesc": "LAIO Creator Stipend",
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{DARAJA_BASE_URL}/mpesa/stkpush/v1/processrequest"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        return resp.json()


async def simulate_mock_mpesa_lifecycle(checkout_id: str):
    await asyncio.sleep(3.0)
    tx = TRANSACTIONS_DB.get(checkout_id)
    if not tx:
        return

    stk_receipt = generate_mock_mpesa_receipt()
    b2c_receipt = generate_mock_mpesa_receipt()

    tx["status"] = "COMPLETED"
    tx["stk_receipt"] = stk_receipt
    tx["b2c_payout_status"] = "DISBURSED_TO_CREATOR"
    tx["b2c_receipt"] = b2c_receipt
    tx["completed_at"] = datetime.utcnow().isoformat()


# --- ENDPOINT 1: LOCALE & CREATOR CONFIG ---
@app.get("/api/locale-config")
async def get_locale_config(
    request: Request,
    country_code: Optional[str] = None,
    submission_id: str = "PILOT",
):
    detected_country = (
        country_code
        or request.headers.get("CF-IPCountry")
        or request.headers.get("X-Vercel-IP-Country")
        or "KE"
    ).upper()

    config = LOCALE_CONFIGS.get(detected_country, LOCALE_CONFIGS["KE"])
    creator = CREATORS_DB.get(submission_id, CREATORS_DB["PILOT"])

    return {
        "mode": MPESA_MODE,
        "country": detected_country,
        "creator": {"name": creator["name"], "role": creator["role"]},
        **config,
    }


# --- ENDPOINT 2: INITIATE & POLL STIPENDS ---
class TipRequest(BaseModel):
    submission_id: str = "PILOT"
    country_code: str = "KE"
    supporter_contact: str
    amount: float


@app.post("/api/tips/initiate")
async def initiate_creator_tip(payload: TipRequest, background_tasks: BackgroundTasks):
    country = payload.country_code.upper()
    config = LOCALE_CONFIGS.get(country, LOCALE_CONFIGS["KE"])

    if payload.amount not in config["tiers"]:
        raise HTTPException(status_code=400, detail="Selected amount is not a valid tier for this region.")

    creator = CREATORS_DB.get(payload.submission_id, CREATORS_DB["PILOT"])
    split = calculate_stipend_split(payload.amount)
    checkout_id = f"ws_CO_{uuid.uuid4().hex[:12].upper()}"

    if config["gateway"] == "MPESA":
        clean_phone = normalize_kenyan_phone(payload.supporter_contact)
        if MPESA_MODE == "mock":
            gateway_res = {
                "MerchantRequestID": f"MOCK-MR-{uuid.uuid4().hex[:6].upper()}",
                "CheckoutRequestID": checkout_id,
                "ResponseCode": "0",
                "CustomerMessage": "Success. Request accepted for processing (MOCK)",
            }
            background_tasks.add_task(simulate_mock_mpesa_lifecycle, checkout_id)
        else:
