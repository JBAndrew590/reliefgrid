"""Upgrade and downgrade an isolated temporary PostgreSQL database."""
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from reliefgrid.config import settings


def test_migration_round_trip():
    name="migration_"+uuid4().hex[:12]
    admin=create_engine(settings.migration_database_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url=make_url(settings.migration_database_url).set(database=name)
    environment={**os.environ,"MIGRATION_DATABASE_URL":url.render_as_string(hide_password=False)}
    try:
        for target in ("head","base","head"):
            command="upgrade" if target=="head" else "downgrade"
            subprocess.run(["alembic",command,target],cwd=str(Path(__file__).resolve().parents[1]),env=environment,check=True,capture_output=True,text=True)
        probe=create_engine(url)
        with probe.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one()==1
            assert conn.execute(text("SELECT postgis_version()")).scalar_one()
        probe.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:db"),{"db":name})
            conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        admin.dispose()
