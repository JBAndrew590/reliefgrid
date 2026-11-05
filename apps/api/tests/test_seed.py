from sqlalchemy import text

from reliefgrid.db import engine
from reliefgrid.seed import seed


def test_repeatable_demo_seed():
    seed()
    seed()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM users WHERE email LIKE '%@reliefgrid.local'")).scalar_one()==5
        assert conn.execute(text("SELECT count(*) FROM inventory_lots WHERE lot_code='RICE-2026-A'")).scalar_one()==1
