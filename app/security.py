"""Password hashing, opaque sessions, CSRF validation, and persistent throttling."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
import time
from fastapi import HTTPException, Request, Response

from .database import database
from .settings import settings

PBKDF2_ROUNDS = 310_000


def _digest(value: str) -> str:
    key=settings.session_secret.encode("utf-8") if settings.session_secret else b"linguaai-development-session-pepper"
    return hmac.new(key,value.encode("utf-8"),hashlib.sha256).hexdigest()


def hash_session_token(value: str) -> str:
    return _digest(value)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def create_session(response: Response, user_id: int | None) -> tuple[str, str]:
    raw = secrets.token_urlsafe(36)
    csrf = secrets.token_urlsafe(24)
    expiry = (datetime.now(timezone.utc) + timedelta(days=settings.session_days)).isoformat()
    with database() as conn:
        conn.execute("INSERT INTO sessions(token_hash,csrf_token,user_id,expires_at) VALUES(?,?,?,?)", (_digest(raw), csrf, user_id, expiry))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now(timezone.utc).isoformat(),))
    response.set_cookie(settings.cookie_name, raw, max_age=settings.session_days * 86400, httponly=True,
                        secure=settings.production, samesite="strict", path="/")
    return csrf, raw


def clear_session(response: Response, raw: str | None) -> None:
    if raw:
        with database() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_digest(raw),))
    response.delete_cookie(settings.cookie_name, path="/", httponly=True, secure=settings.production, samesite="strict")


def get_session(request: Request):
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        return None
    with database() as conn:
        return conn.execute("SELECT s.*,u.email,u.display_name,u.preferred_language FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (_digest(raw), datetime.now(timezone.utc).isoformat())).fetchone()


def require_csrf(request: Request) -> None:
    session = get_session(request)
    submitted = request.headers.get("x-csrf-token", "")
    if not session or not submitted or not hmac.compare_digest(session["csrf_token"], submitted):
        raise HTTPException(403, "Session expired. Refresh the page and try again.")


def require_user(request: Request):
    session = get_session(request)
    if not session or session["user_id"] is None:
        raise HTTPException(401, "Sign in to access your saved learning data.")
    return session


def limit_request(request: Request, bucket: str, limit: int, window_seconds: int = 60) -> None:
    ip = request.client.host if request.client else "unknown"
    subject = _digest(f"{bucket}:{ip}")
    window = int(time.time()) // window_seconds * window_seconds
    with database() as conn:
        conn.execute("DELETE FROM rate_limits WHERE window_start < ?", (window - window_seconds * 4,))
        conn.execute("INSERT INTO rate_limits(bucket,subject_hash,window_start,hits) VALUES(?,?,?,1) ON CONFLICT(bucket,subject_hash,window_start) DO UPDATE SET hits=hits+1", (bucket, subject, window))
        hits = conn.execute("SELECT hits FROM rate_limits WHERE bucket=? AND subject_hash=? AND window_start=?", (bucket, subject, window)).fetchone()[0]
    if hits > limit:
        raise HTTPException(429, "Too many requests. Please wait a minute and try again.")
