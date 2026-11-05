"""PostgreSQL/PostGIS integration fixtures; tests never substitute SQLite."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from reliefgrid.db import engine
from reliefgrid.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def actors(client):
    suffix = uuid4().hex[:12]
    password = "development-test-password-123"
    names = {}
    for role in ["supplier", "requester"]:
        email = f"{role}-{suffix}@synthetic.example"
        response = client.post("/v1/auth/register", json={"email": email, "password": password,
                          "organization": f"Test {role} {suffix}"})
        assert response.status_code == 201, response.text
        org = response.json()["organization_id"]
        csrf = client.cookies.get("csrf")
        headers = {"X-Organization-ID": org, "X-CSRF-Token": csrf}
        response = client.post("/v1/locations", headers=headers, json={"name": f"{role} site", "kind": "warehouse" if role=="supplier" else "shelter",
                            "latitude": -6.1 if role=="supplier" else -6.2, "longitude": 106.8})
        assert response.status_code == 201, response.text
        names[role] = {"email": email, "org": org, "location": response.json()["id"]}
    with engine.begin() as conn:
        conn.execute(text("UPDATE organizations SET share_inventory=true WHERE id=:id"), {"id": names["supplier"]["org"]})
        conn.execute(text("UPDATE memberships SET role='request_coordinator' WHERE organization_id=:org"), {"org": names["requester"]["org"]})
        resource = conn.execute(text("""INSERT INTO resources(category,name,unit) VALUES ('Water',:name,'liter') RETURNING id"""),
                                {"name": f"Water-{suffix}"}).scalar_one()
    names["resource"] = str(resource)
    names["password"] = password
    return names


def login(client, actors, role):
    response=client.post("/v1/auth/login", json={"email": actors[role]["email"], "password": actors["password"]})
    assert response.status_code==200, response.text
    return {"X-Organization-ID": actors[role]["org"], "X-CSRF-Token": client.cookies.get("csrf")}
