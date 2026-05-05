import uuid
from datetime import datetime, timedelta, timezone
import bcrypt as _bcrypt
from jose import JWTError, jwt
from coreAdmin_api.settings import settings


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def _create_token(data: dict, expires_delta: timedelta, token_type: str) -> str:
    payload = data.copy()
    payload.update(
        {
            "exp": datetime.now(timezone.utc) + expires_delta,
            "jti": str(uuid.uuid4()),
            "type": token_type,
        }
    )
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: str) -> str:
    return _create_token(
        {"sub": user_id},
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        {"sub": user_id},
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
    )


def create_impersonation_token(target_user_id: str, superadmin_id: str) -> tuple[str, str]:
    """Create a short-lived, non-renewable impersonation access token.

    Returns (token, jti) so the JTI can be stored in ImpersonationLog.
    """
    jti = str(uuid.uuid4())
    payload = {
        "sub": target_user_id,
        "impersonated_by": superadmin_id,
        "renewable": False,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "jti": jti,
        "type": "access",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, jti


def create_access_token_with_iat(user_id: str) -> str:
    """Access token that embeds iat explicitly for step-up age checks."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": str(uuid.uuid4()),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str, expected_type: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload
    except JWTError:
        return None
