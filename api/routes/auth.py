import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from api.auth import verify_init_data
from api.config import get_settings
from api.database import get_db
from api.models import User, UserRole, UserStatus
from api.schemas import RegisterRequest, UserOut, ValidateResponse
from bot.notifications import notify_admins_new_registration

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)
TELEGRAM_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")


def normalize_telegram_username(value: str) -> str:
    username = value.strip()
    if not TELEGRAM_USERNAME_RE.fullmatch(username):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid Telegram handle, e.g. @username")
    return username if username.startswith("@") else f"@{username}"


def _to_user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.committees = [m.committee for m in user.committee_memberships]
    return out


@router.post("/validate", response_model=ValidateResponse)
def validate(
    x_telegram_init_data: str = Header(..., alias="X-Telegram-Init-Data"),
    db: Session = Depends(get_db),
) -> ValidateResponse:
    identity = verify_init_data(x_telegram_init_data)
    user = db.query(User).filter(User.telegram_id == identity.telegram_id).first()
    if not user:
        return ValidateResponse(registered=False, user=None)
    return ValidateResponse(registered=True, user=_to_user_out(user))


@router.post("/register", response_model=UserOut)
async def register(
    req: RegisterRequest,
    x_telegram_init_data: str = Header(..., alias="X-Telegram-Init-Data"),
    db: Session = Depends(get_db),
) -> UserOut:
    identity = verify_init_data(x_telegram_init_data)

    existing = db.query(User).filter(User.telegram_id == identity.telegram_id).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already registered")

    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")

    settings = get_settings()
    is_admin = identity.telegram_id in settings.all_admin_telegram_id_set

    display_name = req.display_name or " ".join(
        part for part in [identity.first_name, identity.last_name] if part
    ) or identity.username

    user = User(
        telegram_id=identity.telegram_id,
        telegram_username=normalize_telegram_username(req.telegram_username) if req.telegram_username else None,
        email=req.email,
        display_name=display_name,
        role=UserRole.admin if is_admin else UserRole.user,
        status=UserStatus.approved if is_admin else UserStatus.pending,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if not is_admin:
        await notify_admins_new_registration(display_name, req.email)

    return _to_user_out(user)
