"""Small database-backed maintenance worker; no external queue required."""
import json
import logging
import time

from sqlalchemy import text

from reliefgrid.db import engine

log = logging.getLogger("reliefgrid.worker")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def expire_once() -> int:
    """Expire only unallocated overdue requests; each batch commits atomically."""
    with engine.begin() as conn:
        rows = conn.execute(text("""SELECT q.id,q.organization_id,q.status FROM resource_requests q
            WHERE q.required_by < now() AND q.status IN ('DRAFT','SUBMITTED','VERIFIED','MATCHED')
              AND NOT EXISTS (SELECT 1 FROM request_items i JOIN allocations a ON a.request_item_id=i.id WHERE i.request_id=q.id)
            ORDER BY q.required_by,q.id LIMIT 100 FOR UPDATE OF q SKIP LOCKED""")).mappings().all()
        for row in rows:
            conn.execute(text("UPDATE resource_requests SET status='EXPIRED',updated_at=now() WHERE id=:id"),
                         {"id": row["id"]})
            conn.execute(text("""INSERT INTO status_events(request_id,from_status,to_status,actor_id)
                VALUES (:id,:old,'EXPIRED',NULL)"""), {"id": row["id"], "old": row["status"]})
            conn.execute(text("""INSERT INTO audit_events(organization_id,actor_id,action,entity_type,entity_id,
                before_state,after_state,correlation_id)
                VALUES (:org,NULL,'request.expired','request',:id,CAST(:before AS jsonb),CAST(:after AS jsonb),'worker-expiration')"""),
                {"org": row["organization_id"], "id": str(row["id"]),
                 "before": json.dumps({"status": row["status"]}), "after": json.dumps({"status": "EXPIRED"})})
        return len(rows)


def main() -> None:
    while True:
        try:
            count = expire_once()
            if count:
                log.info(json.dumps({"event": "requests_expired", "count": count}))
        except Exception:
            log.exception(json.dumps({"event": "expiration_scan_failed"}))
        time.sleep(60)


if __name__ == "__main__":
    main()
