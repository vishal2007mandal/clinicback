"""
============================================================
PUJA CLINIC — Single File FastAPI Backend
============================================================
"""

import logging
import os
from datetime import date, datetime
from typing import Literal, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from supabase import Client, create_client

# ============================================================
# LOAD ENV
# ============================================================
load_dotenv()

APP_NAME = os.getenv("APP_NAME", "Puja Clinic API")
APP_ENV = os.getenv("APP_ENV", "development")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5500,"
        "http://127.0.0.1:5500,http://localhost:8080",
    ).split(",")
    if o.strip()
]

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe@123")

# ============================================================
# VALIDATION
# ============================================================
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env"
    )

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("puja-clinic")

# ============================================================
# SUPABASE CLIENT
# ============================================================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(
    title=APP_NAME,
    version="1.0.0",
    description="Backend API for Puja Clinic website & admin dashboard.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# SCHEMAS
# ============================================================
AppointmentStatus = Literal["pending", "completed", "cancelled"]


class AppointmentCreate(BaseModel):
    patient_name: str = Field(..., min_length=2, max_length=100)
    patient_phone: str = Field(..., min_length=10, max_length=15)
    service: str = Field(..., min_length=2, max_length=100)
    visit_date: date
    message: Optional[str] = Field(None, max_length=1000)

    @field_validator("patient_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 10:
            raise ValueError("Phone must have at least 10 digits")
        return digits

    @field_validator("visit_date")
    @classmethod
    def validate_date(cls, v: date) -> date:
        if v < date.today():
            raise ValueError("Visit date cannot be in the past")
        return v


class AppointmentResponse(BaseModel):
    id: int
    patient_name: str
    patient_phone: str
    service: str
    visit_date: date
    message: Optional[str] = None
    status: AppointmentStatus
    created_at: datetime
    updated_at: datetime


class AppointmentStatusUpdate(BaseModel):
    status: AppointmentStatus


class DashboardStats(BaseModel):
    total: int
    pending: int
    completed: int
    cancelled: int
    today: int


# ============================================================
# SIMPLE ADMIN AUTH
# ============================================================
def verify_admin(
    x_admin_username: str = Header(..., alias="X-Admin-Username"),
    x_admin_password: str = Header(..., alias="X-Admin-Password"),
) -> str:
    if (
        x_admin_username != ADMIN_USERNAME
        or x_admin_password != ADMIN_PASSWORD
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )
    return x_admin_username


# ============================================================
# TELEGRAM
# ============================================================
STATUS_EMOJI = {"pending": "🟡", "completed": "🟢", "cancelled": "🔴"}


def _format_appointment_message(appt: dict) -> str:
    created = appt.get("created_at")
    if isinstance(created, str):
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            created_str = created_dt.strftime("%d %b %Y, %I:%M %p")
        except Exception:
            created_str = created
    else:
        created_str = str(created)

    visit_date = appt.get("visit_date", "—")
    if hasattr(visit_date, "strftime"):
        visit_date = visit_date.strftime("%d %b %Y")

    status_val = appt.get("status", "pending")
    status_emoji = STATUS_EMOJI.get(status_val, "⚪")
    message = appt.get("message") or "—"
    phone = appt.get("patient_phone", "—")

    return (
        "🏥 <b>NEW APPOINTMENT REQUEST</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>ID:</b> #{appt.get('id', 'N/A')}\n"
        f"👤 <b>Patient:</b> {appt.get('patient_name', '—')}\n"
        f"📞 <b>Phone:</b> <a href='tel:{phone}'>{phone}</a>\n"
        f"🩺 <b>Service:</b> {appt.get('service', '—')}\n"
        f"📅 <b>Preferred Date:</b> {visit_date}\n"
        f"💬 <b>Message:</b> {message}\n"
        f"📊 <b>Status:</b> {status_emoji} {status_val.upper()}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <i>Received: {created_str}</i>\n"
        "📍 <i>Puja Clinic, Block Chowk, Ranchi</i>"
    )


async def send_telegram_notification(appt: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials missing, skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": _format_appointment_message(appt),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                logger.error(
                    "Telegram send failed [%s]: %s",
                    response.status_code,
                    response.text,
                )
                return False
            logger.info(
                "Telegram notification sent for appointment #%s",
                appt.get("id"),
            )
            return True
    except Exception as exc:
        logger.exception("Telegram notification error: %s", exc)
        return False


# ============================================================
# HEALTH
# ============================================================
@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "app": APP_NAME, "env": APP_ENV, "docs": "/docs"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}


# ============================================================
# PUBLIC — CREATE APPOINTMENT
# ============================================================
@app.post(
    "/api/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Appointments"],
)
async def create_appointment(payload: AppointmentCreate):
    data = {
        "patient_name": payload.patient_name.strip(),
        "patient_phone": payload.patient_phone,
        "service": payload.service,
        "visit_date": payload.visit_date.isoformat(),
        "message": (payload.message or "").strip() or None,
        "status": "pending",
    }

    try:
        result = supabase.table("appointments").insert(data).execute()
    except Exception as exc:
        logger.exception("Supabase insert failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Could not save appointment. Please try again.",
        )

    if not result.data:
        raise HTTPException(status_code=500, detail="Appointment was not saved.")

    appt = result.data[0]

    try:
        await send_telegram_notification(appt)
    except Exception as exc:
        logger.exception("Telegram notify error: %s", exc)

    return appt


# ============================================================
# ADMIN — LOGIN
# ============================================================
@app.post("/api/admin/login", tags=["Admin"])
async def admin_login(
    x_admin_username: str = Header(..., alias="X-Admin-Username"),
    x_admin_password: str = Header(..., alias="X-Admin-Password"),
):
    if (
        x_admin_username != ADMIN_USERNAME
        or x_admin_password != ADMIN_PASSWORD
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    return {"ok": True, "username": x_admin_username}


# ============================================================
# ADMIN — STATS
# ============================================================
@app.get("/api/admin/stats", response_model=DashboardStats, tags=["Admin"])
async def get_stats(_: str = Depends(verify_admin)):
    try:
        resp = supabase.table("appointments").select("status, visit_date").execute()
    except Exception as exc:
        logger.exception("Stats query failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load stats")

    rows = resp.data or []
    total = len(rows)
    pending = sum(1 for r in rows if r["status"] == "pending")
    completed = sum(1 for r in rows if r["status"] == "completed")
    cancelled = sum(1 for r in rows if r["status"] == "cancelled")
    today_str = date.today().isoformat()
    today = sum(1 for r in rows if r.get("visit_date") == today_str)

    return DashboardStats(
        total=total,
        pending=pending,
        completed=completed,
        cancelled=cancelled,
        today=today,
    )


# ============================================================
# ADMIN — LIST
# ============================================================
@app.get(
    "/api/admin/appointments",
    response_model=list[AppointmentResponse],
    tags=["Admin"],
)
async def list_appointments(
    _: str = Depends(verify_admin),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    try:
        query = supabase.table("appointments").select("*")
        if status_filter in ("pending", "completed", "cancelled"):
            query = query.eq("status", status_filter)
        if search:
            q = search.strip().replace(",", "")
            if q:
                query = query.or_(
                    f"patient_name.ilike.%{q}%,patient_phone.ilike.%{q}%"
                )
        query = query.order("created_at", desc=True).limit(limit)
        resp = query.execute()
    except Exception as exc:
        logger.exception("List appointments failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to load appointments"
        )

    return resp.data or []


# ============================================================
# ADMIN — UPDATE STATUS
# ============================================================
@app.patch(
    "/api/admin/appointments/{appointment_id}/status",
    response_model=AppointmentResponse,
    tags=["Admin"],
)
async def update_status(
    appointment_id: int,
    payload: AppointmentStatusUpdate,
    _: str = Depends(verify_admin),
):
    try:
        resp = (
            supabase.table("appointments")
            .update({"status": payload.status})
            .eq("id", appointment_id)
            .execute()
        )
    except Exception as exc:
        logger.exception("Status update failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update status")

    if not resp.data:
        raise HTTPException(status_code=404, detail="Appointment not found")

    return resp.data[0]


# ============================================================
# ADMIN — DELETE
# ============================================================
@app.delete(
    "/api/admin/appointments/{appointment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Admin"],
)
async def delete_appointment(
    appointment_id: int,
    _: str = Depends(verify_admin),
):
    try:
        resp = (
            supabase.table("appointments")
            .delete()
            .eq("id", appointment_id)
            .execute()
        )
    except Exception as exc:
        logger.exception("Delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete")

    if not resp.data:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return None


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=APP_ENV == "development",
    )



    print("✅ main.py loaded, app =", app)
print("✅ Routes:", [r.path for r in app.routes])