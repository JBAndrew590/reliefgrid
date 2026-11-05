"""Tests of actual PostGIS, tenant authorization and transactional allocation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from reliefgrid.db import engine
from reliefgrid.main import app

from .conftest import login


def key():
    return str(uuid4())


def prepare_request(client, actors, headers, amount="7"):
    body={"delivery_location_id":actors["requester"]["location"],"priority":"critical","reason":"Emergency drinking water",
          "required_by":(datetime.now(UTC)+timedelta(days=2)).isoformat(),
          "items":[{"resource_id":actors["resource"],"quantity":amount}]}
    response=client.post("/v1/requests",headers={**headers,"Idempotency-Key":key()},json=body)
    assert response.status_code==201, response.text
    req=response.json()["id"]
    for state in ("SUBMITTED","VERIFIED"):
        response=client.post(f"/v1/requests/{req}/transition/{state}",headers=headers)
        assert response.status_code==200, response.text
    response=client.post(f"/v1/requests/{req}/match",headers=headers)
    assert response.status_code==200, response.text
    detail=client.get(f"/v1/requests/{req}",headers=headers).json()
    return req,detail["items"][0]["id"],response.json()["candidates"]


def test_live_postgis_constraints_and_tenant_isolation(client,actors):
    supplier=login(client,actors,"supplier")
    response=client.post("/v1/inventory",headers=supplier,json={"location_id":actors["supplier"]["location"],
        "resource_id":actors["resource"],"lot_code":"A","quantity":"10.000"})
    assert response.status_code==201, response.text
    lot=response.json()["id"]
    with engine.begin() as conn:
        try:
            with conn.begin_nested():
                conn.execute(text("UPDATE inventory_lots SET reserved=11 WHERE id=:id"),{"id":lot})
        except Exception as exc:
            assert "check" in str(exc).lower()
        else:
            raise AssertionError("Database allowed reserved > on_hand")
    requester=login(client,actors,"requester")
    assert client.get("/v1/inventory",headers=requester).json()["items"]==[]
    assert client.get("/v1/inventory",headers={"X-Organization-ID":actors["supplier"]["org"]}).status_code==403
    assert client.post("/v1/inventory",headers=requester,json={"location_id":actors["requester"]["location"],
        "resource_id":actors["resource"],"lot_code":"B","quantity":"1"}).status_code==403
    assert client.get("/v1/locations?latitude=-6.2&longitude=106.8&radius_km=50",headers=requester).status_code==200


def test_idempotency_and_concurrent_allocation(client,actors):
    supplier=login(client,actors,"supplier")
    response=client.post("/v1/inventory",headers=supplier,json={"location_id":actors["supplier"]["location"],
        "resource_id":actors["resource"],"lot_code":"CONCURRENT","quantity":"10"})
    assert response.status_code==201, response.text
    lot=response.json()["id"]
    requester=login(client,actors,"requester")
    req1,item1,candidates1=prepare_request(client,actors,requester)
    req2,item2,candidates2=prepare_request(client,actors,requester)
    assert any(c["lot_id"]==lot for c in candidates1)
    assert any(c["lot_id"]==lot for c in candidates2)
    cookie=dict(client.cookies)
    def allocate(item):
        with TestClient(app) as worker:
            worker.cookies.update(cookie)
            return worker.post("/v1/allocations",headers={**requester,"Idempotency-Key":key()},
                               json={"request_item_id":item,"lot_id":lot,"quantity":"7"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(allocate,[item1,item2]))
    assert sorted(r.status_code for r in responses)==[201,409], [r.text for r in responses]
    with engine.connect() as conn:
        row=conn.execute(text("SELECT on_hand,reserved FROM inventory_lots WHERE id=:id"),{"id":lot}).one()
        assert row.reserved==7 and row.reserved<=row.on_hand
        assert conn.execute(text("SELECT COALESCE(sum(quantity),0) FROM allocations WHERE lot_id=:id"),{"id":lot}).scalar_one()==7
    winning_item=item1 if responses[0].status_code==201 else item2
    retry_key=key()
    payload={"request_item_id":winning_item,"lot_id":lot,"quantity":"1"}
    # The item is already fully allocated; further reservation must be rejected.
    first=client.post("/v1/allocations",headers={**requester,"Idempotency-Key":retry_key},json=payload)
    assert first.status_code==409
    assert client.post("/v1/allocations",headers={**requester,"Idempotency-Key":retry_key},json={**payload,"quantity":"2"}).status_code==409


def test_request_creation_idempotency_and_csrf(client,actors):
    headers=login(client,actors,"requester")
    body={"delivery_location_id":actors["requester"]["location"],"priority":"high","reason":"Urgent supply request",
          "required_by":(datetime.now(UTC)+timedelta(days=3)).isoformat(),
          "items":[{"resource_id":actors["resource"],"quantity":"2"}]}
    request_key=key()
    a=client.post("/v1/requests",headers={**headers,"Idempotency-Key":request_key},json=body)
    b=client.post("/v1/requests",headers={**headers,"Idempotency-Key":request_key},json=body)
    assert a.status_code==201 and b.status_code==201 and a.json()==b.json()
    changed={**body,"reason":"Different purpose"}
    assert client.post("/v1/requests",headers={**headers,"Idempotency-Key":request_key},json=changed).status_code==409
    assert client.post("/v1/requests",headers={"X-Organization-ID":headers["X-Organization-ID"],"Idempotency-Key":key()},json=body).status_code==403
    assert client.get("/ready").status_code==200


def test_full_delivery_changes_inventory_dashboard_and_audit(client,actors):
    supplier=login(client,actors,"supplier")
    lot_response=client.post("/v1/inventory",headers=supplier,json={"location_id":actors["supplier"]["location"],
        "resource_id":actors["resource"],"lot_code":"DELIVERY","quantity":"6"})
    assert lot_response.status_code==201,lot_response.text
    lot=lot_response.json()["id"]
    requester=login(client,actors,"requester")
    req,item,candidates=prepare_request(client,actors,requester,amount="6")
    assert any(c["lot_id"]==lot for c in candidates)
    allocation=client.post("/v1/allocations",headers={**requester,"Idempotency-Key":key()},
        json={"request_item_id":item,"lot_id":lot,"quantity":"6"})
    assert allocation.status_code==201,allocation.text
    assert allocation.json()["status"]=="FULLY_ALLOCATED"
    supplier=login(client,actors,"supplier")
    shipment=client.post("/v1/shipments",headers={**supplier,"Idempotency-Key":key()},
        json={"allocation_id":allocation.json()["id"],"reference":key()})
    assert shipment.status_code==201,shipment.text
    shipment_id=shipment.json()["id"]
    assert client.post(f"/v1/shipments/{shipment_id}/pickup",headers=supplier).status_code==200
    assert client.post(f"/v1/shipments/{shipment_id}/dispatch",headers={**supplier,"Idempotency-Key":key()}).status_code==200
    requester=login(client,actors,"requester")
    delivered=client.post(f"/v1/shipments/{shipment_id}/deliver",headers={**requester,"Idempotency-Key":key()},
        json={"recipient_name":"Synthetic recipient","evidence_note":"Received in full"})
    assert delivered.status_code==200,delivered.text
    assert client.get(f"/v1/requests/{req}",headers=requester).json()["request"]["status"]=="FULFILLED"
    assert client.get("/v1/dashboard",headers=requester).json()["metrics"]["fulfilled_requests"]>=1
    with engine.connect() as conn:
        row=conn.execute(text("SELECT on_hand,reserved FROM inventory_lots WHERE id=:id"),{"id":lot}).one()
        assert row.on_hand==0 and row.reserved==0
        assert conn.execute(text("SELECT count(*) FROM audit_events WHERE entity_id=:id"),{"id":shipment_id}).scalar_one()>=3


def test_session_revocation_and_role_boundary(client,actors):
    requester=login(client,actors,"requester")
    assert client.get("/v1/auth/me").status_code==200
    assert client.get("/v1/audit",headers=requester).status_code==403
    assert client.post("/v1/locations",headers=requester,json={"name":"Unauthorized site","kind":"shelter","latitude":-6.2,"longitude":106.8}).status_code==403
    assert client.post("/v1/auth/logout",headers=requester).status_code==200
    assert client.get("/v1/auth/me").status_code==401


def test_worker_expires_overdue_unallocated_request(client, actors):
    from reliefgrid.worker import expire_once
    requester=login(client,actors,"requester")
    body={"delivery_location_id":actors["requester"]["location"],"priority":"routine",
          "reason":"Synthetic overdue need","required_by":(datetime.now(UTC)+timedelta(days=1)).isoformat(),
          "items":[{"resource_id":actors["resource"],"quantity":"1"}]}
    response=client.post("/v1/requests",headers={**requester,"Idempotency-Key":key()},json=body)
    assert response.status_code==201,response.text
    request_id=response.json()["id"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE resource_requests SET required_by=now()-interval '1 hour' WHERE id=:id"),{"id":request_id})
    assert expire_once()>=1
    assert client.get(f"/v1/requests/{request_id}",headers=requester).json()["request"]["status"]=="EXPIRED"
