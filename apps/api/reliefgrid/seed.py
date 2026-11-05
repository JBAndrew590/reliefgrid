"""Idempotent synthetic demo seed. Enabled explicitly in development only."""
import os
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy import text

from reliefgrid.config import settings
from reliefgrid.db import engine

RESOURCES = [
    ("Food", "Rice", "kilogram"), ("Water", "Drinking water", "liter"),
    ("Medical", "First aid kit", "pack"), ("Medical", "Oral rehydration salts", "pack"),
    ("Shelter", "Emergency blanket", "piece"),
]


def seed():
    if not settings.demo_seed:
        raise SystemExit("Set DEMO_SEED=true to enable synthetic demo data")
    password = os.environ.get("DEMO_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("Set DEMO_PASSWORD to a development-only password of at least 12 characters")
    ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
    with engine.begin() as conn:
        for category, name, unit in RESOURCES:
            conn.execute(text("""INSERT INTO resources(category,name,unit) VALUES (:category,:name,:unit)
                ON CONFLICT (name,unit) DO NOTHING"""), {"category":category,"name":name,"unit":unit})
        for name, email, share in [("North Jakarta Logistics", "ops@synthetic.example", True),
                                    ("Harbor Relief Network", "help@synthetic.example", False)]:
            conn.execute(text("""INSERT INTO organizations(name,contact_email,share_inventory)
                VALUES (:name,:email,:share) ON CONFLICT (name) DO NOTHING"""),
                {"name": name, "email": email, "share": share})
        north = conn.execute(text("SELECT id FROM organizations WHERE name='North Jakarta Logistics'")).scalar_one()
        harbor = conn.execute(text("SELECT id FROM organizations WHERE name='Harbor Relief Network'")).scalar_one()
        for email, org, role in [
            ("admin@reliefgrid.local", north, "organization_admin"),
            ("inventory@reliefgrid.local", north, "inventory_manager"),
            ("dispatcher@reliefgrid.local", north, "dispatcher"),
            ("coordinator@reliefgrid.local", harbor, "request_coordinator"),
            ("auditor@reliefgrid.local", harbor, "auditor"),
        ]:
            user = conn.execute(text("SELECT id FROM users WHERE email=:email"), {"email": email}).scalar_one_or_none()
            if not user:
                user = conn.execute(text("INSERT INTO users(email,password_hash) VALUES (:email,:hash) RETURNING id"),
                                    {"email": email, "hash": ph.hash(password)}).scalar_one()
            conn.execute(text("""INSERT INTO memberships(user_id,organization_id,role) VALUES (:uid,:org,:role)
                ON CONFLICT (user_id,organization_id) DO NOTHING"""),
                {"uid": user, "org": org, "role": role})
        for org, name, kind, lat, lon in [
            (north, "Tanjung Priok Warehouse", "warehouse", -6.1132, 106.8803),
            (north, "Kelapa Gading Distribution", "distribution_center", -6.1584, 106.9050),
            (harbor, "Pluit Emergency Shelter", "shelter", -6.1250, 106.7900),
            (harbor, "Harbor Field Hospital", "hospital", -6.1467, 106.8100),
        ]:
            conn.execute(text("""INSERT INTO locations(organization_id,name,kind,position)
                VALUES (:org,:name,:kind,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography)
                ON CONFLICT (organization_id,name) DO NOTHING"""),
                {"org": org, "name": name, "kind": kind, "lat": lat, "lon": lon})
        wh = conn.execute(text("SELECT id FROM locations WHERE organization_id=:org AND name='Tanjung Priok Warehouse'"),
                          {"org": north}).scalar_one()
        actor = conn.execute(text("SELECT id FROM users WHERE email='inventory@reliefgrid.local'")).scalar_one()
        for resource_name, qty, code, expiry in [
            ("Rice", 600, "RICE-2026-A", 120), ("Drinking water", 1200, "WATER-2026-A", 45),
            ("First aid kit", 80, "MED-2026-A", 400), ("Emergency blanket", 400, "SHELTER-2026-A", None),
        ]:
            res = conn.execute(text("SELECT id FROM resources WHERE name=:name"), {"name": resource_name}).scalar_one()
            lot = conn.execute(text("""INSERT INTO inventory_lots(organization_id,location_id,resource_id,lot_code,expires_on,on_hand)
                VALUES (:org,:loc,:res,:code,:expiry,:qty)
                ON CONFLICT (organization_id,resource_id,lot_code) DO NOTHING RETURNING id"""),
                {"org": north, "loc": wh, "res": res, "code": code,
                 "expiry": (datetime.now(UTC).date()+timedelta(days=expiry)) if expiry else None,
                 "qty": qty}).scalar_one_or_none()
            if lot:
                conn.execute(text("INSERT INTO inventory_movements(lot_id,delta,reason,actor_id) VALUES (:lot,:qty,'demo_seed',:actor)"),
                             {"lot": lot, "qty": qty, "actor": actor})
    print("Synthetic accounts: admin, inventory, dispatcher, coordinator, auditor @reliefgrid.local")


if __name__ == "__main__":
    seed()
