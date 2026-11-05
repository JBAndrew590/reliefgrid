"""Initial normalized operational schema, spatial indexes and audit trail.

Revision ID: 0001_initial
Revises:
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("""CREATE TABLE organizations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL UNIQUE,
        contact_email text NOT NULL, share_inventory boolean NOT NULL DEFAULT false,
        active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text NOT NULL UNIQUE,
        password_hash text NOT NULL, active boolean NOT NULL DEFAULT true,
        platform_admin boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT email_lowercase CHECK (email = lower(email)))""")
    op.execute("""CREATE TABLE memberships (
        user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
        role text NOT NULL CHECK (role IN ('organization_admin','inventory_manager','dispatcher','request_coordinator','auditor')),
        active boolean NOT NULL DEFAULT true,
        PRIMARY KEY (user_id, organization_id))""")
    op.execute("CREATE INDEX memberships_org_idx ON memberships (organization_id, role) WHERE active")
    op.execute("""CREATE TABLE sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash text NOT NULL UNIQUE, csrf_hash text NOT NULL,
        expires_at timestamptz NOT NULL, revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX sessions_user_active_idx ON sessions (user_id, expires_at) WHERE revoked_at IS NULL")
    op.execute("""CREATE TABLE locations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL REFERENCES organizations(id),
        name text NOT NULL, kind text NOT NULL CHECK (kind IN
        ('warehouse','distribution_center','hospital','shelter','emergency')),
        contact text NOT NULL DEFAULT '', capacity numeric(16,3) CHECK (capacity >= 0),
        position geography(Point,4326) NOT NULL, active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (id, organization_id), UNIQUE (organization_id, name),
        CONSTRAINT valid_position CHECK (ST_Y(position::geometry) BETWEEN -90 AND 90
            AND ST_X(position::geometry) BETWEEN -180 AND 180))""")
    op.execute("CREATE INDEX locations_position_gix ON locations USING gist (position)")
    op.execute("""CREATE TABLE resources (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), category text NOT NULL, name text NOT NULL,
        unit text NOT NULL CHECK (unit IN ('piece','liter','kilogram','pack')),
        active boolean NOT NULL DEFAULT true, UNIQUE (name, unit))""")
    op.execute("""CREATE TABLE inventory_lots (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
        location_id uuid NOT NULL, resource_id uuid NOT NULL REFERENCES resources(id),
        lot_code text NOT NULL, expires_on date, on_hand numeric(16,3) NOT NULL,
        reserved numeric(16,3) NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (location_id, organization_id) REFERENCES locations(id, organization_id),
        UNIQUE (organization_id, resource_id, lot_code),
        CHECK (on_hand >= 0 AND reserved >= 0 AND reserved <= on_hand))""")
    op.execute("CREATE INDEX lots_fefo_idx ON inventory_lots (resource_id, expires_on ASC NULLS LAST, id) WHERE on_hand > reserved")
    op.execute("CREATE INDEX lots_org_idx ON inventory_lots (organization_id, created_at DESC, id DESC)")
    op.execute("""CREATE TABLE inventory_movements (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), lot_id uuid NOT NULL REFERENCES inventory_lots(id),
        delta numeric(16,3) NOT NULL CHECK (delta <> 0), reason text NOT NULL,
        actor_id uuid NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE resource_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), organization_id uuid NOT NULL,
        delivery_location_id uuid NOT NULL, priority text NOT NULL CHECK (priority IN ('routine','high','critical')),
        status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN
        ('DRAFT','SUBMITTED','VERIFIED','MATCHED','PARTIALLY_ALLOCATED','FULLY_ALLOCATED','IN_TRANSIT','FULFILLED','CANCELLED','EXPIRED','REJECTED')),
        reason text NOT NULL, notes text NOT NULL DEFAULT '', required_by timestamptz NOT NULL,
        created_by uuid NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (delivery_location_id, organization_id) REFERENCES locations(id, organization_id))""")
    op.execute("CREATE INDEX requests_queue_idx ON resource_requests (organization_id, priority, required_by, id) WHERE status NOT IN ('FULFILLED','CANCELLED','EXPIRED','REJECTED')")
    op.execute("CREATE INDEX requests_recent_idx ON resource_requests (organization_id, created_at DESC, id DESC)")
    op.execute("CREATE INDEX requests_search_idx ON resource_requests USING gin (to_tsvector('english', reason || ' ' || notes))")
    op.execute("""CREATE TABLE request_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), request_id uuid NOT NULL REFERENCES resource_requests(id),
        resource_id uuid NOT NULL REFERENCES resources(id), quantity numeric(16,3) NOT NULL CHECK (quantity > 0),
        UNIQUE (request_id, resource_id))""")
    op.execute("""CREATE TABLE allocations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), request_item_id uuid NOT NULL REFERENCES request_items(id),
        lot_id uuid NOT NULL REFERENCES inventory_lots(id), quantity numeric(16,3) NOT NULL CHECK (quantity > 0),
        created_by uuid NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (request_item_id, lot_id), UNIQUE (id, lot_id))""")
    op.execute("CREATE INDEX allocations_lot_idx ON allocations (lot_id)")
    op.execute("""CREATE TABLE shipments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), allocation_id uuid NOT NULL UNIQUE REFERENCES allocations(id),
        status text NOT NULL DEFAULT 'PREPARED' CHECK (status IN ('PREPARED','PICKED_UP','IN_TRANSIT','DELIVERED')),
        reference text NOT NULL UNIQUE, evidence_note text NOT NULL DEFAULT '',
        picked_up_at timestamptz, dispatched_at timestamptz, delivered_at timestamptz,
        recipient_name text, created_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE status_events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, request_id uuid NOT NULL REFERENCES resource_requests(id),
        from_status text, to_status text NOT NULL, actor_id uuid REFERENCES users(id),
        occurred_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX status_events_request_idx ON status_events (request_id, id)")
    op.execute("""CREATE TABLE idempotency_keys (
        actor_id uuid NOT NULL REFERENCES users(id), scope text NOT NULL, key text NOT NULL,
        request_hash text NOT NULL, response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (actor_id, scope, key))""")
    op.execute("""CREATE TABLE match_candidates (
        request_item_id uuid NOT NULL REFERENCES request_items(id),
        lot_id uuid NOT NULL REFERENCES inventory_lots(id),
        components jsonb NOT NULL, total integer NOT NULL CHECK (total BETWEEN 0 AND 100),
        distance_m double precision NOT NULL CHECK (distance_m >= 0),
        generated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (request_item_id, lot_id))""")
    op.execute("""CREATE TABLE rate_limits (
        bucket text PRIMARY KEY, window_start timestamptz NOT NULL, attempts integer NOT NULL CHECK (attempts > 0))""")
    op.execute("""CREATE TABLE audit_events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, organization_id uuid REFERENCES organizations(id),
        actor_id uuid REFERENCES users(id), action text NOT NULL, entity_type text NOT NULL,
        entity_id text NOT NULL, before_state jsonb, after_state jsonb,
        correlation_id text NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX audit_org_idx ON audit_events (organization_id, id DESC)")
    op.execute("""CREATE FUNCTION protect_audit_events() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit_events is append only'; END $$""")
    op.execute("CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION protect_audit_events()")
    op.execute("GRANT SELECT, INSERT, UPDATE ON organizations, memberships, sessions, inventory_lots, resource_requests, allocations, shipments, rate_limits TO reliefgrid_app")
    op.execute("GRANT SELECT, INSERT ON users, locations, resources, inventory_movements, request_items, status_events, idempotency_keys, audit_events TO reliefgrid_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON match_candidates TO reliefgrid_app")
    op.execute("GRANT SELECT ON alembic_version TO reliefgrid_app")
    op.execute("GRANT USAGE, SELECT ON status_events_id_seq, audit_events_id_seq TO reliefgrid_app")


def downgrade() -> None:
    for name in ("audit_events", "rate_limits", "match_candidates", "idempotency_keys", "status_events", "shipments", "allocations",
                 "request_items", "resource_requests", "inventory_movements", "inventory_lots",
                 "resources", "locations", "sessions", "memberships", "users", "organizations"):
        op.execute(f"DROP TABLE {name}")
    op.execute("DROP FUNCTION protect_audit_events()")
