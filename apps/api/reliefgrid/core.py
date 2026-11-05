"""Request-scoped auth, audit, pagination and idempotency helpers."""
import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, NoReturn
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.engine import Connection

from reliefgrid.config import settings

logger = logging.getLogger("reliefgrid")


@dataclass(frozen=True)
class Principal:
    user_id: str
    organization_id: str
    role: str
    platform_admin: bool


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def data(row):
    """Serialize PostgreSQL values without converting exact numeric quantities to float."""
    if row is None:
        return None
    if isinstance(row, (UUID, datetime, date)):
        return str(row.isoformat() if isinstance(row, (datetime, date)) else row)
    if isinstance(row, Decimal):
        return str(row)
    if isinstance(row, dict):
        return {k: data(v) for k, v in row.items()}
    if isinstance(row, (list, tuple)):
        return [data(v) for v in row]
    if hasattr(row, "_mapping"):
        return data(dict(row._mapping))
    return row


def fail(code: str, message: str, status: int = 400) -> NoReturn:
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def principal(request: Request, conn: Connection, roles: set[str] | None = None) -> Principal:
    token = request.cookies.get("session")
    if token is None:
        fail("UNAUTHENTICATED", "Sign in required", 401)
    row = conn.execute(text("""SELECT u.id, u.active, u.platform_admin, s.csrf_hash
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash=:hash AND s.revoked_at IS NULL AND s.expires_at > now()"""),
        {"hash": digest(token)}).mappings().first()
    if not row or not row["active"]:
        fail("UNAUTHENTICATED", "Session expired or account inactive", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not hmac.compare_digest(digest(csrf), row["csrf_hash"]) or not hmac.compare_digest(csrf, request.cookies.get("csrf", "")):
            fail("CSRF_INVALID", "Invalid CSRF token", 403)
        origin = request.headers.get("origin")
        if origin and origin != settings.web_origin:
            fail("ORIGIN_INVALID", "Invalid request origin", 403)
    org = request.headers.get("x-organization-id", "")
    try:
        UUID(org)
    except ValueError:
        fail("ORGANIZATION_REQUIRED", "Select an organization", 400)
    membership = conn.execute(text("""SELECT m.role FROM memberships m JOIN organizations o ON o.id=m.organization_id
        WHERE m.user_id=:uid AND m.organization_id=:oid AND m.active AND o.active"""),
        {"uid": row["id"], "oid": org}).scalar_one_or_none()
    if not membership and not row["platform_admin"]:
        fail("FORBIDDEN", "No active organization membership", 403)
    role = membership or "platform_administrator"
    if roles and role not in roles and not row["platform_admin"]:
        fail("FORBIDDEN", "Insufficient permission", 403)
    return Principal(str(row["id"]), org, role, bool(row["platform_admin"]))


def audit(conn: Connection, p: Principal, request: Request, action: str, entity: str,
          entity_id: str, before=None, after=None, org_id: str | None = None):
    conn.execute(text("""INSERT INTO audit_events
        (organization_id, actor_id, action, entity_type, entity_id, before_state, after_state, correlation_id)
        VALUES (:org,:actor,:action,:entity,:eid,CAST(:before AS jsonb),CAST(:after AS jsonb),:rid)"""),
        {"org": org_id or p.organization_id, "actor": p.user_id, "action": action,
         "entity": entity, "eid": entity_id, "before": json.dumps(data(before)) if before is not None else None,
         "after": json.dumps(data(after)) if after is not None else None,
         "rid": request.state.correlation_id})


def idem_begin(conn: Connection, p: Principal, scope: str, key: str | None, payload: object) -> tuple[str, Any | None]:
    if not key or len(key) > 120 or len(key) < 8:
        fail("IDEMPOTENCY_KEY_REQUIRED", "Supply an Idempotency-Key of 8-120 characters", 400)
    signature = digest(json.dumps(data(payload), sort_keys=True, default=str))
    lock = f"{p.user_id}:{scope}:{key}"
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 0))"), {"value": lock})
    prior = conn.execute(text("""SELECT request_hash, response FROM idempotency_keys
        WHERE actor_id=:uid AND scope=:scope AND key=:key"""),
        {"uid": p.user_id, "scope": scope, "key": key}).mappings().first()
    if prior:
        if not hmac.compare_digest(prior["request_hash"], signature):
            fail("IDEMPOTENCY_CONFLICT", "Key reused with different input", 409)
        return signature, prior["response"]
    return signature, None


def idem_save(conn: Connection, p: Principal, scope: str, key: str, signature: str, result: object):
    conn.execute(text("""INSERT INTO idempotency_keys (actor_id,scope,key,request_hash,response)
        VALUES (:uid,:scope,:key,:sig,CAST(:response AS jsonb))"""),
        {"uid": p.user_id, "scope": scope, "key": key, "sig": signature,
         "response": json.dumps(data(result))})


def encode_cursor(created: datetime, identifier: UUID | str) -> str:
    return base64.urlsafe_b64encode(f"{created.isoformat()}|{identifier}".encode()).decode()


def decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        date_part, id_part = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        dt = datetime.fromisoformat(date_part)
        if dt.tzinfo is None:
            raise ValueError("Naive cursor")
        return dt, str(UUID(id_part))
    except (ValueError, UnicodeError):
        fail("INVALID_CURSOR", "Invalid page cursor", 400)


def rate_limit(conn: Connection, bucket: str, maximum: int = 5):
    attempts: int = conn.execute(text("""INSERT INTO rate_limits (bucket, window_start, attempts)
        VALUES (:bucket, now(), 1) ON CONFLICT (bucket) DO UPDATE SET
        window_start=CASE WHEN rate_limits.window_start < now()-interval '15 minutes' THEN now() ELSE rate_limits.window_start END,
        attempts=CASE WHEN rate_limits.window_start < now()-interval '15 minutes' THEN 1 ELSE rate_limits.attempts+1 END
        RETURNING attempts"""), {"bucket": digest(bucket)}).scalar_one()
    if attempts > maximum:
        fail("RATE_LIMITED", "Try again in 15 minutes", 429)


def new_token() -> str:
    return secrets.token_urlsafe(32)
