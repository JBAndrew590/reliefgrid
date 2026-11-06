"""ReliefGrid HTTP API. Database transactions delimit each business operation."""
import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from reliefgrid.config import settings
from reliefgrid.core import (
    Principal,
    audit,
    data,
    decode_cursor,
    digest,
    encode_cursor,
    fail,
    idem_begin,
    idem_save,
    new_token,
    principal,
    rate_limit,
)
from reliefgrid.db import engine
from reliefgrid.domain import can_transition, score_match
from reliefgrid.schemas import (
    Adjustment,
    AllocationInput,
    DeliveryInput,
    LocationInput,
    Login,
    LotInput,
    Register,
    RequestInput,
    ResourceInput,
    ShipmentInput,
)

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("reliefgrid")
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
app = FastAPI(title="ReliefGrid API", version="0.1.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=[settings.web_origin], allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-CSRF-Token", "X-Organization-ID", "Idempotency-Key", "X-Request-ID"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.correlation_id = request.headers.get("x-request-id", "")[:100] or str(uuid4())
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(json.dumps({"event": "unhandled_error", "request_id": request.state.correlation_id}))
        response = JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error"}, "request_id": request.state.correlation_id})
    response.headers["X-Request-ID"] = request.state.correlation_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/docs") else "default-src 'none'; frame-ancestors 'none'"
    )
    if settings.secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    log.info(json.dumps({"event": "http_request", "method": request.method,
                         "path": request.url.path, "status": response.status_code,
                         "duration_ms": round((time.monotonic() - start) * 1000),
                         "request_id": request.state.correlation_id}))
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail, "request_id": request.state.correlation_id})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "Invalid request", "fields": [str(e["loc"][-1]) for e in exc.errors()]}, "request_id": request.state.correlation_id})


@app.exception_handler(IntegrityError)
async def constraint_error(request: Request, exc: IntegrityError):
    log.warning(json.dumps({"event": "constraint_conflict", "request_id": request.state.correlation_id}))
    return JSONResponse(status_code=409, content={"error": {"code": "CONSTRAINT_CONFLICT", "message": "Operation conflicts with existing data"}, "request_id": request.state.correlation_id})


def cookies(response: JSONResponse, session: str, csrf: str):
    response.set_cookie("session", session, httponly=True, secure=settings.secure_cookies,
                        samesite="lax", max_age=settings.session_hours * 3600, path="/")
    response.set_cookie("csrf", csrf, httponly=False, secure=settings.secure_cookies,
                        samesite="lax", max_age=settings.session_hours * 3600, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


def create_session(conn, user_id):
    token, csrf = new_token(), new_token()
    conn.execute(text("""INSERT INTO sessions (user_id, token_hash, csrf_hash, expires_at)
        VALUES (:uid,:token,:csrf,:expires)"""), {"uid": user_id, "token": digest(token),
        "csrf": digest(csrf), "expires": datetime.now(UTC) + timedelta(hours=settings.session_hours)})
    return token, csrf


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    with engine.connect() as conn:
        conn.execute(text("SELECT postgis_version()"))
        conn.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
    return {"status": "ready"}


@app.post("/v1/auth/register", status_code=201)
def register(body: Register, request: Request):
    with engine.begin() as conn:
        rate_limit(conn, f"register:{request.client.host if request.client else 'unknown'}", maximum=30)
    with engine.begin() as conn:
        org: UUID = conn.execute(text("""INSERT INTO organizations(name,contact_email) VALUES (:name,:email)
            RETURNING id"""), {"name": body.organization, "email": body.email}).scalar_one()
        user: UUID = conn.execute(text("""INSERT INTO users(email,password_hash) VALUES (:email,:hash)
            RETURNING id"""), {"email": body.email, "hash": password_hasher.hash(body.password)}).scalar_one()
        conn.execute(text("""INSERT INTO memberships(user_id,organization_id,role)
            VALUES (:uid,:oid,'organization_admin')"""), {"uid": user, "oid": org})
        token, csrf = create_session(conn, user)
        p = Principal(str(user), str(org), "organization_admin", False)
        audit(conn, p, request, "organization.created", "organization", str(org), after={"name": body.organization})
    return cookies(JSONResponse(status_code=201, content={"user_id": str(user), "organization_id": str(org)}), token, csrf)


@app.post("/v1/auth/login")
def login(body: Login, request: Request):
    email = body.email.strip().lower()
    with engine.begin() as conn:
        rate_limit(conn, f"login:{email}:{request.client.host if request.client else 'unknown'}")
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id,password_hash FROM users WHERE email=:email AND active"), {"email": email}).mappings().first()
        try:
            if row:
                password_hasher.verify(row["password_hash"], body.password)
            else:
                password_hasher.verify(DUMMY_HASH, body.password)
        except VerifyMismatchError:
            fail("INVALID_CREDENTIALS", "Invalid email or password", 401)
        if not row:
            fail("INVALID_CREDENTIALS", "Invalid email or password", 401)
        token, csrf = create_session(conn, row["id"])
    return cookies(JSONResponse(content={"status": "signed_in"}), token, csrf)


# A fixed valid hash avoids a quick user-enumeration path for unknown email addresses.
DUMMY_HASH = password_hasher.hash("not-a-real-account-password")


@app.get("/v1/auth/me")
def me(request: Request):
    with engine.connect() as conn:
        token = request.cookies.get("session", "")
        row = conn.execute(text("""SELECT u.id,u.email,u.platform_admin FROM users u JOIN sessions s ON s.user_id=u.id
            WHERE s.token_hash=:hash AND s.revoked_at IS NULL AND s.expires_at > now() AND u.active"""),
            {"hash": digest(token)}).mappings().first()
        if not row:
            fail("UNAUTHENTICATED", "Sign in required", 401)
        orgs = conn.execute(text("""SELECT o.id,o.name,m.role FROM memberships m JOIN organizations o ON o.id=m.organization_id
            WHERE m.user_id=:uid AND m.active AND o.active ORDER BY o.name"""), {"uid": row["id"]}).mappings().all()
        return {"id": str(row["id"]), "email": row["email"], "platform_admin": row["platform_admin"], "organizations": data(orgs)}


@app.post("/v1/auth/logout")
def logout(request: Request):
    with engine.begin() as conn:
        principal(request, conn)
        conn.execute(text("UPDATE sessions SET revoked_at=now() WHERE token_hash=:hash"),
                     {"hash": digest(request.cookies["session"])})
    response = JSONResponse(content={"status": "signed_out"})
    response.delete_cookie("session")
    response.delete_cookie("csrf")
    return response


@app.post("/v1/auth/rotate")
def rotate(request: Request):
    with engine.begin() as conn:
        p = principal(request, conn)
        conn.execute(text("UPDATE sessions SET revoked_at=now() WHERE token_hash=:hash"),
                     {"hash": digest(request.cookies["session"])})
        token, csrf = create_session(conn, p.user_id)
    return cookies(JSONResponse(content={"status": "rotated"}), token, csrf)


@app.get("/v1/resources")
def resources(request: Request):
    with engine.connect() as conn:
        principal(request, conn)
        return data(conn.execute(text("SELECT id,category,name,unit FROM resources WHERE active ORDER BY category,name")).mappings().all())


@app.post("/v1/resources", status_code=201)
def create_resource(body: ResourceInput, request: Request):
    with engine.begin() as conn:
        p = principal(request, conn, {"organization_admin"})
        resource_id: UUID = conn.execute(text("""INSERT INTO resources(category,name,unit)
            VALUES (:category,:name,:unit) RETURNING id"""), body.model_dump()).scalar_one()
        audit(conn, p, request, "resource.created", "resource", str(resource_id), after=body.model_dump())
        return {"id": str(resource_id)}


@app.post("/v1/locations", status_code=201)
def create_location(body: LocationInput, request: Request):
    with engine.begin() as conn:
        p = principal(request, conn, {"organization_admin"})
        row: UUID = conn.execute(text("""INSERT INTO locations(organization_id,name,kind,contact,capacity,position)
            VALUES (:org,:name,:kind,:contact,:capacity,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography)
            RETURNING id"""), {"org": p.organization_id, "name": body.name, "kind": body.kind,
                "contact": body.contact, "capacity": body.capacity, "lat": body.latitude,
                "lon": body.longitude}).scalar_one()
        audit(conn, p, request, "location.created", "location", str(row), after=body.model_dump(mode="json"))
        return {"id": str(row)}


@app.get("/v1/locations")
def locations(request: Request, kind: str | None = None, latitude: float | None = None,
              longitude: float | None = None, radius_km: float = Query(200, gt=0, le=1000)):
    with engine.connect() as conn:
        p = principal(request, conn)
        if (latitude is None) != (longitude is None):
            fail("INVALID_GEO_FILTER", "Provide both latitude and longitude")
        rows = conn.execute(text("""SELECT l.id,l.organization_id,l.name,l.kind,l.contact,l.capacity,
            ST_Y(l.position::geometry) AS latitude, ST_X(l.position::geometry) AS longitude,
            CASE WHEN CAST(:lat AS double precision) IS NULL THEN NULL ELSE ST_Distance(l.position,
                ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography) END AS distance_m
            FROM locations l JOIN organizations o ON o.id=l.organization_id
            WHERE l.active AND o.active AND (l.organization_id=:org OR o.share_inventory)
                AND (CAST(:kind AS text) IS NULL OR l.kind=:kind)
                AND (CAST(:lat AS double precision) IS NULL OR ST_DWithin(l.position, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography, :radius))
            ORDER BY distance_m ASC NULLS LAST,l.id LIMIT 200"""),
            {"org": p.organization_id, "kind": kind, "lat": latitude, "lon": longitude,
             "radius": radius_km * 1000}).mappings().all()
        return data(rows)


@app.get("/v1/inventory")
def inventory(request: Request, resource_id: UUID | None = None, expires_before: date | None = None, cursor: str | None = None,
              limit: int = Query(30, ge=1, le=100), sort: str = Query("newest", pattern="^(newest|oldest)$")):
    page = decode_cursor(cursor)
    comparator = "<" if sort == "newest" else ">"
    ordering = "DESC" if sort == "newest" else "ASC"
    with engine.connect() as conn:
        p = principal(request, conn)
        rows = conn.execute(text(f"""SELECT l.id,l.organization_id,l.location_id,l.resource_id,l.lot_code,l.expires_on,
            l.on_hand,l.reserved,(l.on_hand-l.reserved) AS available,l.created_at,r.name AS resource_name,
            r.unit,loc.name AS location_name FROM inventory_lots l
            JOIN resources r ON r.id=l.resource_id JOIN locations loc ON loc.id=l.location_id
            WHERE l.organization_id=:org AND (CAST(:resource AS uuid) IS NULL OR l.resource_id=:resource)
                AND (CAST(:expiry AS date) IS NULL OR l.expires_on <= :expiry)
                AND (CAST(:created AS timestamptz) IS NULL OR (l.created_at,l.id) {comparator} (:created,CAST(:cursor_id AS uuid)))
            ORDER BY l.created_at {ordering},l.id {ordering} LIMIT :limit"""),
            {"org": p.organization_id, "resource": resource_id, "expiry": expires_before, "created": page[0] if page else None,
             "cursor_id": page[1] if page else None, "limit": limit+1}).mappings().all()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": data(rows), "next_cursor": encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if more else None}


@app.post("/v1/inventory", status_code=201)
def create_lot(body: LotInput, request: Request):
    with engine.begin() as conn:
        p = principal(request, conn, {"inventory_manager", "organization_admin"})
        location = conn.execute(text("SELECT id FROM locations WHERE id=:id AND organization_id=:org AND active"),
                                {"id": body.location_id, "org": p.organization_id}).scalar_one_or_none()
        if not location:
            fail("LOCATION_NOT_FOUND", "Location unavailable", 404)
        row: UUID = conn.execute(text("""INSERT INTO inventory_lots(organization_id,location_id,resource_id,lot_code,expires_on,on_hand)
            VALUES (:org,:loc,:res,:code,:exp,:qty) RETURNING id"""),
            {"org": p.organization_id, "loc": body.location_id, "res": body.resource_id,
             "code": body.lot_code, "exp": body.expires_on, "qty": body.quantity}).scalar_one()
        conn.execute(text("INSERT INTO inventory_movements(lot_id,delta,reason,actor_id) VALUES (:id,:qty,'initial_receipt',:actor)"),
                     {"id": row, "qty": body.quantity, "actor": p.user_id})
        audit(conn, p, request, "inventory.received", "inventory_lot", str(row), after=body.model_dump(mode="json"))
        return {"id": str(row)}


@app.post("/v1/inventory/{lot_id}/adjust")
def adjust_lot(lot_id: UUID, body: Adjustment, request: Request):
    with engine.begin() as conn:
        p = principal(request, conn, {"inventory_manager", "organization_admin"})
        row = conn.execute(text("""UPDATE inventory_lots SET on_hand=on_hand+:delta
            WHERE id=:id AND organization_id=:org AND on_hand+:delta>=reserved AND on_hand+:delta>=0
            RETURNING on_hand,reserved"""), {"delta": body.delta, "id": lot_id, "org": p.organization_id}).mappings().first()
        if not row:
            fail("ADJUSTMENT_CONFLICT", "Lot missing or reserved stock would be affected", 409)
        conn.execute(text("INSERT INTO inventory_movements(lot_id,delta,reason,actor_id) VALUES (:id,:delta,:reason,:actor)"),
                     {"id": lot_id, "delta": body.delta, "reason": body.reason, "actor": p.user_id})
        audit(conn, p, request, "inventory.adjusted", "inventory_lot", str(lot_id), after={"delta": body.delta, "reason": body.reason})
        return data(row)

@app.post("/v1/requests", status_code=201)
def create_request(body: RequestInput, request: Request, idempotency_key: str | None = Header(None)):
    if body.required_by <= datetime.now(UTC):
        fail("INVALID_DEADLINE", "Required-by time must be in the future")
    with engine.begin() as conn:
        p = principal(request, conn, {"request_coordinator", "organization_admin"})
        signature, prior = idem_begin(conn, p, "create_request", idempotency_key, body.model_dump(mode="json"))
        if prior is not None:
            return prior
        location = conn.execute(text("SELECT id FROM locations WHERE id=:id AND organization_id=:org AND active"),
                                {"id": body.delivery_location_id, "org": p.organization_id}).scalar_one_or_none()
        if not location:
            fail("LOCATION_NOT_FOUND", "Delivery location unavailable", 404)
        req: UUID = conn.execute(text("""INSERT INTO resource_requests
            (organization_id,delivery_location_id,priority,reason,notes,required_by,created_by)
            VALUES (:org,:loc,:priority,:reason,:notes,:deadline,:actor) RETURNING id"""),
            {"org": p.organization_id, "loc": body.delivery_location_id, "priority": body.priority,
             "reason": body.reason, "notes": body.notes, "deadline": body.required_by,
             "actor": p.user_id}).scalar_one()
        for item in body.items:
            conn.execute(text("INSERT INTO request_items(request_id,resource_id,quantity) VALUES (:req,:res,:qty)"),
                         {"req": req, "res": item.resource_id, "qty": item.quantity})
        conn.execute(text("INSERT INTO status_events(request_id,to_status,actor_id) VALUES (:req,'DRAFT',:actor)"),
                     {"req": req, "actor": p.user_id})
        result = {"id": str(req), "status": "DRAFT"}
        audit(conn, p, request, "request.created", "request", str(req), after=body.model_dump(mode="json"))
        idem_save(conn, p, "create_request", str(idempotency_key), signature, result)
        return result


@app.get("/v1/requests")
def list_requests(request: Request, status: str | None = None, priority: str | None = None,
                  start: datetime | None = None, end: datetime | None = None,
                  search: str | None = Query(None, max_length=100), cursor: str | None = None, limit: int = Query(30, ge=1, le=100),
                  sort: str = Query("newest", pattern="^(newest|oldest)$")):
    page = decode_cursor(cursor)
    if (start and start.tzinfo is None) or (end and end.tzinfo is None):
        fail("INVALID_DATE_RANGE", "Date filters require a timezone")
    if start and end and start > end:
        fail("INVALID_DATE_RANGE", "Start must precede end")
    comparator = "<" if sort == "newest" else ">"
    ordering = "DESC" if sort == "newest" else "ASC"
    with engine.connect() as conn:
        p = principal(request, conn)
        rows = conn.execute(text(f"""SELECT q.id,q.priority,q.status,q.reason,q.required_by,q.created_at,
            l.name AS delivery_location, ST_Y(l.position::geometry) AS latitude,
            ST_X(l.position::geometry) AS longitude FROM resource_requests q
            JOIN locations l ON l.id=q.delivery_location_id
            WHERE q.organization_id=:org AND (CAST(:status AS text) IS NULL OR q.status=:status)
                AND (CAST(:priority AS text) IS NULL OR q.priority=:priority)
                AND (CAST(:start AS timestamptz) IS NULL OR q.created_at >= :start)
                AND (CAST(:end AS timestamptz) IS NULL OR q.created_at <= :end)
                AND (CAST(:search AS text) IS NULL OR to_tsvector('english',q.reason || ' ' || q.notes) @@ websearch_to_tsquery('english',:search))
                AND (CAST(:created AS timestamptz) IS NULL OR (q.created_at,q.id) {comparator} (:created,CAST(:cursor_id AS uuid)))
            ORDER BY q.created_at {ordering},q.id {ordering} LIMIT :limit"""),
            {"org": p.organization_id, "status": status, "priority": priority,
             "start": start, "end": end, "search": search, "created": page[0] if page else None, "cursor_id": page[1] if page else None,
             "limit": limit+1}).mappings().all()
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": data(rows), "next_cursor": encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if more else None}


@app.get("/v1/requests/{request_id}")
def request_detail(request_id: UUID, request: Request):
    with engine.connect() as conn:
        p = principal(request, conn)
        req = conn.execute(text("SELECT * FROM resource_requests WHERE id=:id AND organization_id=:org"),
                           {"id": request_id, "org": p.organization_id}).mappings().first()
        if not req:
            fail("REQUEST_NOT_FOUND", "Request not found", 404)
        items = conn.execute(text("""SELECT i.id,i.resource_id,r.name AS resource_name,r.unit,i.quantity,
            COALESCE(SUM(a.quantity),0) AS allocated FROM request_items i
            JOIN resources r ON r.id=i.resource_id LEFT JOIN allocations a ON a.request_item_id=i.id
            WHERE i.request_id=:id GROUP BY i.id,r.id ORDER BY r.name"""), {"id": request_id}).mappings().all()
        events = conn.execute(text("SELECT from_status,to_status,occurred_at FROM status_events WHERE request_id=:id ORDER BY id"),
                              {"id": request_id}).mappings().all()
        return {"request": data(req), "items": data(items), "history": data(events)}


def change_status(conn, p: Principal, request: Request, req_id: UUID, current: str, target: str, org_id: str | None = None):
    if not can_transition(current, target):
        fail("INVALID_TRANSITION", f"Cannot move {current} to {target}", 409)
    conn.execute(text("UPDATE resource_requests SET status=:target,updated_at=now() WHERE id=:id"),
                 {"target": target, "id": req_id})
    conn.execute(text("INSERT INTO status_events(request_id,from_status,to_status,actor_id) VALUES (:id,:old,:new,:actor)"),
                 {"id": req_id, "old": current, "new": target, "actor": p.user_id})
    audit(conn, p, request, "request.status_changed", "request", str(req_id),
          before={"status": current}, after={"status": target}, org_id=org_id)


@app.post("/v1/requests/{request_id}/transition/{target}")
def transition(request_id: UUID, target: str, request: Request):
    allowed_roles = {"SUBMITTED": {"request_coordinator", "organization_admin"},
                     "VERIFIED": {"request_coordinator", "organization_admin"},
                     "CANCELLED": {"request_coordinator", "organization_admin"},
                     "REJECTED": {"organization_admin"}}
    if target not in allowed_roles:
        fail("INVALID_TRANSITION", "This transition is system managed", 400)
    with engine.begin() as conn:
        p = principal(request, conn, allowed_roles[target])
        req = conn.execute(text("SELECT status FROM resource_requests WHERE id=:id AND organization_id=:org FOR UPDATE"),
                           {"id": request_id, "org": p.organization_id}).mappings().first()
        if not req:
            fail("REQUEST_NOT_FOUND", "Request not found", 404)
        change_status(conn, p, request, request_id, req["status"], target)
        return {"id": str(request_id), "status": target}


@app.post("/v1/requests/{request_id}/match")
def match(request_id: UUID, request: Request, radius_km: int = Query(500, ge=1, le=2000)):
    with engine.begin() as conn:
        p = principal(request, conn, {"request_coordinator", "organization_admin"})
        req = conn.execute(text("""SELECT q.status,q.priority,q.required_by,l.position
            FROM resource_requests q JOIN locations l ON l.id=q.delivery_location_id
            WHERE q.id=:id AND q.organization_id=:org FOR UPDATE OF q"""),
            {"id": request_id, "org": p.organization_id}).mappings().first()
        if not req:
            fail("REQUEST_NOT_FOUND", "Request not found", 404)
        if req["required_by"] < datetime.now(UTC):
            fail("REQUEST_EXPIRED", "Request deadline has passed", 409)
        if req["status"] not in {"VERIFIED", "MATCHED", "PARTIALLY_ALLOCATED"}:
            fail("INVALID_TRANSITION", "Verify request before matching", 409)
        candidates = conn.execute(text("""SELECT i.id AS request_item_id,i.quantity AS requested,
            l.id AS lot_id,l.resource_id,l.organization_id,l.expires_on,l.on_hand-l.reserved AS available,
            ST_Distance(src.position,dst.position) AS distance_m,src.name AS source_name,
            r.name AS resource_name,r.unit
            FROM request_items i JOIN resource_requests q ON q.id=i.request_id
            JOIN locations dst ON dst.id=q.delivery_location_id
            JOIN inventory_lots l ON l.resource_id=i.resource_id
            JOIN organizations o ON o.id=l.organization_id AND o.active
            JOIN locations src ON src.id=l.location_id AND src.active
            JOIN resources r ON r.id=i.resource_id
            WHERE i.request_id=:id AND l.on_hand>l.reserved AND (l.organization_id=:org OR o.share_inventory)
              AND (l.expires_on IS NULL OR l.expires_on >= CAST(q.required_by AS date))
              AND ST_DWithin(src.position,dst.position,:radius)
            ORDER BY l.expires_on ASC NULLS LAST,l.id LIMIT 500"""),
            {"id": request_id, "org": p.organization_id, "radius": radius_km*1000}).mappings().all()
        conn.execute(text("DELETE FROM match_candidates WHERE request_item_id IN (SELECT id FROM request_items WHERE request_id=:id)"),
                     {"id": request_id})
        scored = []
        snapshots = []
        for row in candidates:
            score = score_match(req["priority"], row["distance_m"], row["available"],
                                row["requested"], row["expires_on"], req["required_by"])
            components = {"urgency": score.urgency, "distance": score.distance,
                          "coverage": score.coverage, "expiry": score.expiry, "deadline": score.deadline}
            snapshots.append({"item": row["request_item_id"], "lot": row["lot_id"],
                              "parts": json.dumps(components), "total": score.total,
                              "distance": row["distance_m"]})
            scored.append({**data(row), "score": score.total, "components": components})
        if snapshots:
            conn.execute(text("""INSERT INTO match_candidates(request_item_id,lot_id,components,total,distance_m)
                VALUES (:item,:lot,CAST(:parts AS jsonb),:total,:distance)"""), snapshots)
        scored.sort(key=lambda r: (-r["score"], r["expires_on"] or "9999-12-31", r["distance_m"], r["lot_id"]))
        if req["status"] == "VERIFIED":
            change_status(conn, p, request, request_id, "VERIFIED", "MATCHED")
        audit(conn, p, request, "matching.generated", "request", str(request_id), after={"candidate_count": len(scored)})
        return {"request_id": str(request_id), "candidates": scored[:100],
                "truncated": len(scored) > 100, "radius_km": radius_km}


@app.post("/v1/allocations", status_code=201)
def allocate(body: AllocationInput, request: Request, idempotency_key: str | None = Header(None)):
    with engine.begin() as conn:
        p = principal(request, conn, {"request_coordinator", "organization_admin"})
        signature, prior = idem_begin(conn, p, "allocate", idempotency_key, body.model_dump(mode="json"))
        if prior is not None:
            return prior
        item = conn.execute(text("""SELECT i.id,i.resource_id,i.quantity,q.id AS request_id,q.status,q.required_by
            FROM request_items i JOIN resource_requests q ON q.id=i.request_id
            WHERE i.id=:item AND q.organization_id=:org FOR UPDATE OF q"""),
            {"item": body.request_item_id, "org": p.organization_id}).mappings().first()
        if not item:
            fail("ITEM_NOT_FOUND", "Request item not found", 404)
        if item["required_by"] < datetime.now(UTC):
            fail("REQUEST_EXPIRED", "Request deadline has passed", 409)
        if item["status"] not in {"MATCHED", "PARTIALLY_ALLOCATED"}:
            fail("INVALID_TRANSITION", "Request is not open for allocation", 409)
        lot = conn.execute(text("""SELECT l.id,l.resource_id,l.organization_id,l.on_hand,l.reserved,l.expires_on,
            o.share_inventory FROM inventory_lots l JOIN organizations o ON o.id=l.organization_id AND o.active
            WHERE l.id=:lot FOR UPDATE OF l"""), {"lot": body.lot_id}).mappings().first()
        if not lot or (str(lot["organization_id"]) != p.organization_id and not lot["share_inventory"]):
            fail("LOT_NOT_FOUND", "Eligible inventory lot not found", 404)
        if lot["resource_id"] != item["resource_id"] or (lot["expires_on"] and lot["expires_on"] < item["required_by"].date()):
            fail("LOT_INCOMPATIBLE", "Resource or expiration does not meet request", 409)
        chosen = conn.execute(text("SELECT 1 FROM match_candidates WHERE request_item_id=:item AND lot_id=:lot"),
                              {"item": body.request_item_id, "lot": body.lot_id}).scalar_one_or_none()
        if not chosen:
            fail("MATCH_REQUIRED", "Run matching before allocation", 409)
        already: Decimal = conn.execute(text("SELECT COALESCE(sum(quantity),0) FROM allocations WHERE request_item_id=:item"),
                               {"item": body.request_item_id}).scalar_one()
        if already + body.quantity > item["quantity"]:
            fail("REQUEST_OVERALLOCATED", "Quantity exceeds outstanding request", 409)
        changed = conn.execute(text("""UPDATE inventory_lots SET reserved=reserved+:qty
            WHERE id=:lot AND on_hand-reserved>=:qty RETURNING id"""),
            {"qty": body.quantity, "lot": body.lot_id}).scalar_one_or_none()
        if not changed:
            fail("INSUFFICIENT_STOCK", "Available quantity changed; refresh matching", 409)
        shipped = conn.execute(text("""SELECT s.id FROM shipments s JOIN allocations a ON a.id=s.allocation_id
            WHERE a.request_item_id=:item AND a.lot_id=:lot"""),
            {"item": body.request_item_id, "lot": body.lot_id}).scalar_one_or_none()
        if shipped:
            fail("ALLOCATION_SHIPPED", "Cannot increase an allocation after shipment preparation", 409)
        allocation_id: UUID = conn.execute(text("""INSERT INTO allocations(request_item_id,lot_id,quantity,created_by)
            VALUES (:item,:lot,:qty,:actor) ON CONFLICT (request_item_id,lot_id)
            DO UPDATE SET quantity=allocations.quantity+EXCLUDED.quantity RETURNING id"""),
            {"item": body.request_item_id, "lot": body.lot_id, "qty": body.quantity,
             "actor": p.user_id}).scalar_one()
        remaining: int = conn.execute(text("""SELECT count(*) FROM request_items i WHERE i.request_id=:id
            AND (SELECT COALESCE(sum(a.quantity),0) FROM allocations a WHERE a.request_item_id=i.id) < i.quantity"""),
            {"id": item["request_id"]}).scalar_one()
        target = "PARTIALLY_ALLOCATED" if remaining else "FULLY_ALLOCATED"
        if target != item["status"]:
            change_status(conn, p, request, item["request_id"], item["status"], target)
        result = {"id": str(allocation_id), "status": target, "quantity": str(body.quantity)}
        audit(conn, p, request, "allocation.created", "allocation", str(allocation_id), after={**result, "lot_id": str(body.lot_id)})
        idem_save(conn, p, "allocate", str(idempotency_key), signature, result)
        return result

@app.get("/v1/allocations")
def list_allocations(request: Request):
    with engine.connect() as conn:
        p = principal(request, conn)
        rows = conn.execute(text("""SELECT a.id,a.quantity,a.created_at,a.lot_id,a.request_item_id,
            q.id AS request_id,q.organization_id AS requester_id,l.organization_id AS supplier_id,
            r.name AS resource_name,COALESCE(s.status,'UNSHIPPED') AS shipment_status,s.id AS shipment_id
            FROM allocations a JOIN request_items i ON i.id=a.request_item_id
            JOIN resource_requests q ON q.id=i.request_id JOIN inventory_lots l ON l.id=a.lot_id
            JOIN resources r ON r.id=i.resource_id LEFT JOIN shipments s ON s.allocation_id=a.id
            WHERE q.organization_id=:org OR l.organization_id=:org
            ORDER BY a.created_at DESC,a.id DESC LIMIT 100"""), {"org": p.organization_id}).mappings().all()
        return data(rows)


@app.post("/v1/shipments", status_code=201)
def create_shipment(body: ShipmentInput, request: Request, idempotency_key: str | None = Header(None)):
    with engine.begin() as conn:
        p = principal(request, conn, {"dispatcher", "organization_admin"})
        signature, prior = idem_begin(conn, p, "create_shipment", idempotency_key, body.model_dump(mode="json"))
        if prior is not None:
            return prior
        row = conn.execute(text("""SELECT a.id,q.status,q.id AS request_id,l.organization_id AS supplier_id
            FROM allocations a JOIN request_items i ON i.id=a.request_item_id
            JOIN resource_requests q ON q.id=i.request_id JOIN inventory_lots l ON l.id=a.lot_id
            WHERE a.id=:id AND l.organization_id=:org FOR UPDATE OF q"""),
            {"id": body.allocation_id, "org": p.organization_id}).mappings().first()
        if not row:
            fail("ALLOCATION_NOT_FOUND", "Allocation not found", 404)
        if row["status"] not in {"FULLY_ALLOCATED", "IN_TRANSIT"}:
            fail("REQUEST_NOT_READY", "Complete all allocations before dispatch", 409)
        shipment_id: UUID = conn.execute(text("""INSERT INTO shipments(allocation_id,reference)
            VALUES (:allocation,:reference) RETURNING id"""),
            {"allocation": body.allocation_id, "reference": body.reference}).scalar_one()
        result = {"id": str(shipment_id), "status": "PREPARED"}
        audit(conn, p, request, "shipment.created", "shipment", str(shipment_id), after=result)
        idem_save(conn, p, "create_shipment", str(idempotency_key), signature, result)
        return result


@app.post("/v1/shipments/{shipment_id}/pickup")
def pickup(shipment_id: UUID, request: Request):
    with engine.begin() as conn:
        p = principal(request, conn, {"dispatcher", "organization_admin"})
        row = conn.execute(text("""SELECT s.status,l.organization_id FROM shipments s JOIN allocations a ON a.id=s.allocation_id
            JOIN inventory_lots l ON l.id=a.lot_id WHERE s.id=:id FOR UPDATE OF s"""),
            {"id": shipment_id}).mappings().first()
        if not row or str(row["organization_id"]) != p.organization_id:
            fail("SHIPMENT_NOT_FOUND", "Shipment not found", 404)
        if row["status"] != "PREPARED":
            fail("INVALID_TRANSITION", "Shipment already picked up", 409)
        conn.execute(text("UPDATE shipments SET status='PICKED_UP',picked_up_at=now() WHERE id=:id"), {"id": shipment_id})
        audit(conn, p, request, "shipment.picked_up", "shipment", str(shipment_id), before={"status": row["status"]}, after={"status": "PICKED_UP"})
        return {"id": str(shipment_id), "status": "PICKED_UP"}


@app.post("/v1/shipments/{shipment_id}/dispatch")
def dispatch(shipment_id: UUID, request: Request, idempotency_key: str | None = Header(None)):
    with engine.begin() as conn:
        p = principal(request, conn, {"dispatcher", "organization_admin"})
        signature, prior = idem_begin(conn, p, "dispatch", idempotency_key, {"shipment_id": str(shipment_id)})
        if prior is not None:
            return prior
        row = conn.execute(text("""SELECT s.status,q.id AS request_id,q.status AS request_status,q.organization_id AS requester_id,l.organization_id AS supplier_id
            FROM shipments s JOIN allocations a ON a.id=s.allocation_id JOIN inventory_lots l ON l.id=a.lot_id
            JOIN request_items i ON i.id=a.request_item_id JOIN resource_requests q ON q.id=i.request_id
            WHERE s.id=:id FOR UPDATE OF s,q"""), {"id": shipment_id}).mappings().first()
        if not row or str(row["supplier_id"]) != p.organization_id:
            fail("SHIPMENT_NOT_FOUND", "Shipment not found", 404)
        if row["status"] != "PICKED_UP":
            fail("INVALID_TRANSITION", "Pick up shipment first", 409)
        conn.execute(text("UPDATE shipments SET status='IN_TRANSIT',dispatched_at=now() WHERE id=:id"), {"id": shipment_id})
        if row["request_status"] == "FULLY_ALLOCATED":
            # A source dispatcher is authorized for this shipment; audit is attributed to the requester.
            change_status(conn, p, request, row["request_id"], "FULLY_ALLOCATED", "IN_TRANSIT", org_id=str(row["requester_id"]))
        result = {"id": str(shipment_id), "status": "IN_TRANSIT"}
        audit(conn, p, request, "shipment.dispatched", "shipment", str(shipment_id), after=result)
        idem_save(conn, p, "dispatch", str(idempotency_key), signature, result)
        return result


@app.post("/v1/shipments/{shipment_id}/deliver")
def deliver(shipment_id: UUID, body: DeliveryInput, request: Request, idempotency_key: str | None = Header(None)):
    with engine.begin() as conn:
        p = principal(request, conn, {"dispatcher", "organization_admin", "request_coordinator"})
        signature, prior = idem_begin(conn, p, "deliver", idempotency_key, {"id": str(shipment_id), **body.model_dump()})
        if prior is not None:
            return prior
        row = conn.execute(text("""SELECT s.status,a.quantity,a.lot_id,q.id AS request_id,q.organization_id AS requester_id,
            l.organization_id AS supplier_id FROM shipments s JOIN allocations a ON a.id=s.allocation_id
            JOIN inventory_lots l ON l.id=a.lot_id JOIN request_items i ON i.id=a.request_item_id
            JOIN resource_requests q ON q.id=i.request_id WHERE s.id=:id FOR UPDATE OF s,q,l"""),
            {"id": shipment_id}).mappings().first()
        if not row or p.organization_id not in {str(row["supplier_id"]), str(row["requester_id"])}:
            fail("SHIPMENT_NOT_FOUND", "Shipment not found", 404)
        if row["status"] != "IN_TRANSIT":
            fail("INVALID_TRANSITION", "Shipment is not in transit", 409)
        conn.execute(text("""UPDATE shipments SET status='DELIVERED',delivered_at=now(),
            recipient_name=:recipient,evidence_note=:note WHERE id=:id"""),
            {"recipient": body.recipient_name, "note": body.evidence_note, "id": shipment_id})
        conn.execute(text("UPDATE inventory_lots SET reserved=reserved-:qty,on_hand=on_hand-:qty WHERE id=:lot"),
                     {"qty": row["quantity"], "lot": row["lot_id"]})
        conn.execute(text("INSERT INTO inventory_movements(lot_id,delta,reason,actor_id) VALUES (:lot,:delta,'delivery',:actor)"),
                     {"lot": row["lot_id"], "delta": -row["quantity"], "actor": p.user_id})
        outstanding: int = conn.execute(text("""SELECT count(*) FROM allocations a JOIN request_items i ON i.id=a.request_item_id
            LEFT JOIN shipments s ON s.allocation_id=a.id WHERE i.request_id=:req AND (s.id IS NULL OR s.status<>'DELIVERED')"""),
            {"req": row["request_id"]}).scalar_one()
        if not outstanding:
            change_status(conn, p, request, row["request_id"], "IN_TRANSIT", "FULFILLED", org_id=str(row["requester_id"]))
        result = {"id": str(shipment_id), "status": "DELIVERED"}
        audit(conn, p, request, "shipment.delivered", "shipment", str(shipment_id), after={**result, "recipient_name": body.recipient_name})
        idem_save(conn, p, "deliver", str(idempotency_key), signature, result)
        return result


@app.get("/v1/dashboard")
def dashboard(request: Request):
    with engine.connect() as conn:
        p = principal(request, conn)
        stats = conn.execute(text("""SELECT
            (SELECT count(*) FROM resource_requests q WHERE q.organization_id=:org AND q.status NOT IN ('FULFILLED','CANCELLED','REJECTED','EXPIRED')) AS open_requests,
            (SELECT count(*) FROM resource_requests q WHERE q.organization_id=:org AND q.priority='critical' AND q.status NOT IN ('FULFILLED','CANCELLED','REJECTED','EXPIRED')) AS critical_requests,
            (SELECT COALESCE(sum(on_hand-reserved),0) FROM inventory_lots WHERE organization_id=:org) AS available_units,
            (SELECT count(*) FROM inventory_lots WHERE organization_id=:org AND expires_on BETWEEN current_date AND current_date+30 AND on_hand>reserved) AS expiring_lots,
            (SELECT count(*) FROM allocations a JOIN request_items i ON i.id=a.request_item_id JOIN resource_requests q ON q.id=i.request_id WHERE q.organization_id=:org) AS allocations,
            (SELECT count(*) FROM resource_requests WHERE organization_id=:org AND status='FULFILLED') AS fulfilled_requests,
            (SELECT round(extract(epoch from avg(s.delivered_at-q.created_at))/3600,1)
             FROM shipments s JOIN allocations a ON a.id=s.allocation_id JOIN request_items i ON i.id=a.request_item_id
             JOIN resource_requests q ON q.id=i.request_id WHERE q.organization_id=:org AND s.status='DELIVERED') AS avg_delivery_hours"""),
            {"org": p.organization_id}).mappings().one()
        flow = conn.execute(text("""SELECT r.name,COALESCE(sum(a.quantity),0) AS allocated
            FROM resources r JOIN request_items i ON i.resource_id=r.id JOIN resource_requests q ON q.id=i.request_id
            LEFT JOIN allocations a ON a.request_item_id=i.id WHERE q.organization_id=:org
            GROUP BY r.id ORDER BY allocated DESC LIMIT 8"""), {"org": p.organization_id}).mappings().all()
        recent = conn.execute(text("""SELECT id,action,entity_type,entity_id,occurred_at FROM audit_events
            WHERE organization_id=:org ORDER BY id DESC LIMIT 10"""), {"org": p.organization_id}).mappings().all()
        return {"metrics": data(stats), "resource_flow": data(flow), "activity": data(recent)}


@app.get("/v1/map")
def map_data(request: Request, latitude: float | None = None, longitude: float | None = None,
             radius_km: float = Query(200, gt=0, le=1000)):
    with engine.connect() as conn:
        p = principal(request, conn)
        rows = conn.execute(text("""SELECT l.id,l.name,l.kind,ST_Y(l.position::geometry) AS latitude,
            ST_X(l.position::geometry) AS longitude,l.organization_id FROM locations l JOIN organizations o ON o.id=l.organization_id
            WHERE l.active AND o.active AND (l.organization_id=:org OR o.share_inventory)
            AND (CAST(:lat AS double precision) IS NULL OR ST_DWithin(l.position,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,:radius))
            LIMIT 300"""), {"org": p.organization_id, "lat": latitude,
             "lon": longitude, "radius": radius_km*1000}).mappings().all()
        requests = conn.execute(text("""SELECT q.id,q.status,q.priority,l.name AS delivery_location,ST_Y(l.position::geometry) AS latitude,
            ST_X(l.position::geometry) AS longitude FROM resource_requests q JOIN locations l ON l.id=q.delivery_location_id
            WHERE q.organization_id=:org AND q.status NOT IN ('FULFILLED','CANCELLED','REJECTED','EXPIRED') LIMIT 200"""),
            {"org": p.organization_id}).mappings().all()
        shipments = conn.execute(text("""SELECT s.id,s.status,src.name AS source,dst.name AS destination,
            ST_Y(src.position::geometry) AS source_lat,ST_X(src.position::geometry) AS source_lon,
            ST_Y(dst.position::geometry) AS target_lat,ST_X(dst.position::geometry) AS target_lon
            FROM shipments s JOIN allocations a ON a.id=s.allocation_id JOIN inventory_lots il ON il.id=a.lot_id
            JOIN locations src ON src.id=il.location_id JOIN request_items ri ON ri.id=a.request_item_id
            JOIN resource_requests q ON q.id=ri.request_id JOIN locations dst ON dst.id=q.delivery_location_id
            WHERE (q.organization_id=:org OR il.organization_id=:org) AND s.status IN ('PICKED_UP','IN_TRANSIT') LIMIT 100"""),
            {"org": p.organization_id}).mappings().all()
        return {"locations": data(rows), "requests": data(requests), "shipments": data(shipments)}


@app.get("/v1/audit")
def audit_log(request: Request, before_id: int | None = Query(None, gt=0),
              limit: int = Query(30, ge=1, le=100), action: str | None = None):
    with engine.connect() as conn:
        p = principal(request, conn, {"auditor", "organization_admin"})
        rows = conn.execute(text("""SELECT id,actor_id,action,entity_type,entity_id,before_state,after_state,correlation_id,occurred_at
            FROM audit_events WHERE organization_id=:org AND (CAST(:before AS bigint) IS NULL OR id<:before)
            AND (CAST(:action AS text) IS NULL OR action=:action) ORDER BY id DESC LIMIT :limit"""),
            {"org": p.organization_id, "before": before_id, "action": action,
             "limit": limit+1}).mappings().all()
        more = len(rows)>limit
        rows = rows[:limit]
        return {"items": data(rows), "next_cursor": rows[-1]["id"] if rows and more else None}

@app.get("/v1/organization")
def organization(request: Request):
    with engine.connect() as conn:
        p = principal(request, conn)
        return data(conn.execute(text("SELECT id,name,contact_email,share_inventory,active FROM organizations WHERE id=:id"),
                                 {"id": p.organization_id}).mappings().one())


@app.post("/v1/organization/sharing")
def sharing(request: Request, enabled: bool):
    with engine.begin() as conn:
        p = principal(request, conn, {"organization_admin"})
        old: bool = conn.execute(text("SELECT share_inventory FROM organizations WHERE id=:id FOR UPDATE"),
                           {"id": p.organization_id}).scalar_one()
        conn.execute(text("UPDATE organizations SET share_inventory=:enabled WHERE id=:id"),
                     {"enabled": enabled, "id": p.organization_id})
        audit(conn, p, request, "organization.sharing_changed", "organization", p.organization_id,
              before={"share_inventory": old}, after={"share_inventory": enabled})
        return {"share_inventory": enabled}


@app.get("/v1/members")
def members(request: Request):
    with engine.connect() as conn:
        p = principal(request, conn, {"organization_admin", "auditor"})
        rows = conn.execute(text("""SELECT u.id,u.email,u.active,m.role,m.active AS membership_active
            FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.organization_id=:org ORDER BY u.email"""),
            {"org": p.organization_id}).mappings().all()
        return data(rows)


@app.post("/v1/members/{user_id}/role")
def set_member_role(user_id: UUID, role: str, request: Request):
    if role not in {"organization_admin", "inventory_manager", "dispatcher", "request_coordinator", "auditor"}:
        fail("INVALID_ROLE", "Unknown organization role")
    with engine.begin() as conn:
        p = principal(request, conn, {"organization_admin"})
        # Serialize role changes within an organization, including last-admin checks.
        conn.execute(text("SELECT id FROM organizations WHERE id=:org FOR UPDATE"), {"org": p.organization_id})
        previous = conn.execute(text("""SELECT m.role,m.active FROM memberships m JOIN users u ON u.id=m.user_id
            WHERE m.user_id=:uid AND m.organization_id=:org AND u.active FOR UPDATE OF m"""),
            {"uid": user_id, "org": p.organization_id}).mappings().first()
        if not previous:
            fail("MEMBER_NOT_FOUND", "Active account is not a member of this organization", 404)
        if previous["role"] == "organization_admin" and previous["active"] and role != "organization_admin":
            admins: int = conn.execute(text("""SELECT count(*) FROM memberships m JOIN users u ON u.id=m.user_id
                WHERE m.organization_id=:org AND m.role='organization_admin' AND m.active AND u.active"""),
                {"org": p.organization_id}).scalar_one()
            if admins == 1:
                fail("LAST_ADMIN", "Assign another administrator before changing this role", 409)
        conn.execute(text("""UPDATE memberships SET role=:role,active=true
            WHERE user_id=:uid AND organization_id=:org"""),
            {"uid": user_id, "org": p.organization_id, "role": role})
        audit(conn, p, request, "membership.role_changed", "membership", str(user_id),
              before={"role": previous["role"], "active": previous["active"]},
              after={"role": role, "active": True})
        return {"user_id": str(user_id), "role": role}
