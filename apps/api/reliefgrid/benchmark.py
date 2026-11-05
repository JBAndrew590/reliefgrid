"""Reproducible, rollback-only EXPLAIN ANALYZE benchmark for a local database.

Never run against production: it temporarily drops indexes inside a transaction.
"""
import json
from uuid import uuid4

from sqlalchemy import create_engine, text

from reliefgrid.config import settings

QUERIES = {
    "nearest_inventory": ("locations_position_gix", "CREATE INDEX locations_position_gix ON locations USING gist (position)",
        """SELECT l.id,ST_Distance(l.position,ST_SetSRID(ST_MakePoint(106.84,-6.14),4326)::geography)
        FROM locations l WHERE ST_DWithin(l.position,ST_SetSRID(ST_MakePoint(106.84,-6.14),4326)::geography,10000)
        ORDER BY l.position <-> ST_SetSRID(ST_MakePoint(106.84,-6.14),4326)::geography LIMIT 20"""),
    "urgent_requests": ("requests_queue_idx", "CREATE INDEX requests_queue_idx ON resource_requests (organization_id, priority, required_by, id) WHERE status NOT IN ('FULFILLED','CANCELLED','EXPIRED','REJECTED')",
        """SELECT id,required_by FROM resource_requests WHERE organization_id=:org
        AND priority='critical' AND status NOT IN ('FULFILLED','CANCELLED','EXPIRED','REJECTED')
        ORDER BY required_by ASC LIMIT 30"""),
    "expiring_inventory": ("lots_fefo_idx", "CREATE INDEX lots_fefo_idx ON inventory_lots (resource_id, expires_on ASC NULLS LAST, id) WHERE on_hand > reserved",
        """SELECT id,expires_on,on_hand-reserved FROM inventory_lots WHERE resource_id=:resource
        AND on_hand>reserved AND expires_on BETWEEN current_date AND current_date+30
        ORDER BY expires_on ASC LIMIT 50"""),
}


def explain(conn, query, params):
    row=conn.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+query),params).scalar_one()[0]
    plan=row["Plan"]
    return {"execution_ms": row["Execution Time"], "planning_ms": row["Planning Time"],
            "root_node":plan["Node Type"], "plan":plan,
            "shared_hit_blocks":plan.get("Shared Hit Blocks",0),
            "shared_read_blocks":plan.get("Shared Read Blocks",0)}


def run():
    marker=uuid4().hex[:12]
    engine=create_engine(settings.migration_database_url)
    with engine.connect() as conn:
        tx=conn.begin()
        try:
            org=conn.execute(text("INSERT INTO organizations(name,contact_email) VALUES (:name,'benchmark@synthetic.example') RETURNING id"),
                {"name":"Benchmark "+marker}).scalar_one()
            user=conn.execute(text("INSERT INTO users(email,password_hash) VALUES (:email,'benchmark-only') RETURNING id"),
                {"email":f"benchmark-{marker}@synthetic.example"}).scalar_one()
            res=conn.execute(text("INSERT INTO resources(category,name,unit) VALUES ('Water',:name,'liter') RETURNING id"),
                {"name":"Benchmark water "+marker}).scalar_one()
            conn.execute(text("""INSERT INTO locations(organization_id,name,kind,position)
                SELECT :org,'Synthetic site '||g,'warehouse',
                ST_SetSRID(ST_MakePoint(106.5 + (g%50)*0.01,-6.45 + (g/50)*0.01),4326)::geography
                FROM generate_series(1,2500) g"""),{"org":org})
            dest=conn.execute(text("SELECT id FROM locations WHERE organization_id=:org LIMIT 1"),{"org":org}).scalar_one()
            conn.execute(text("""WITH loc AS (SELECT id,row_number() OVER (ORDER BY id) AS n FROM locations WHERE organization_id=:org)
                INSERT INTO inventory_lots(organization_id,location_id,resource_id,lot_code,expires_on,on_hand)
                SELECT :org,loc.id,:res,'BENCH-'||g,current_date+(g%100),100
                FROM generate_series(1,30000) g JOIN loc ON loc.n=(g%2500)+1"""),{"org":org,"res":res})
            conn.execute(text("""INSERT INTO resource_requests(organization_id,delivery_location_id,priority,status,reason,required_by,created_by)
                SELECT :org,:dest,CASE WHEN g%4=0 THEN 'critical' ELSE 'high' END,
                CASE WHEN g%7=0 THEN 'FULFILLED' ELSE 'VERIFIED' END,'Synthetic benchmark request',
                now()+(g%40)*interval '1 hour',:actor FROM generate_series(1,12000) g"""),
                {"org":org,"dest":dest,"actor":user})
            conn.execute(text("""INSERT INTO audit_events(organization_id,actor_id,action,entity_type,entity_id,correlation_id)
                SELECT :org,:actor,'benchmark.event','synthetic',g::text,'benchmark' FROM generate_series(1,12000) g"""),
                {"org":org,"actor":user})
            for table in ("locations","inventory_lots","resource_requests"):
                conn.execute(text(f"ANALYZE {table}"))
            result={"dataset":{"locations":2500,"inventory_lots":30000,"requests":12000,"audit_events":12000},"queries":{}}
            params={"org":org,"resource":res}
            for name,(index,ddl,query) in QUERIES.items():
                conn.execute(text(f"DROP INDEX {index}"))
                without=explain(conn,query,params)
                conn.execute(text(ddl))
                for table in ("locations","inventory_lots","resource_requests"):
                    conn.execute(text(f"ANALYZE {table}"))
                with_index=explain(conn,query,params)
                result["queries"][name]={"query":query,"without_index":without,"with_index":with_index,
                    "index":index,"observation":"Compare plan nodes, execution time and shared blocks; cold/warm cache affects timing."}
            print(json.dumps(result,indent=2,default=str))
        finally:
            tx.rollback()
    engine.dispose()


if __name__=="__main__":
    run()
