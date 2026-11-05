# Operational runbook

## Start and verify

`cp .env.example .env`, replace local secrets consistently in both URLs, then `make up`. Check `GET /health` for the process and `GET /ready` for database, PostGIS and migration availability. Inspect `docker compose logs --tail=100 api db web` on failure. Set HTTPS and `SECURE_COOKIES=true` before exposing a deployment.

## Incident triage

- Database not ready: check the `db` health check, disk space, `POSTGRES_*` values and owner/app credentials. A changed password does not update an existing volume.
- Reservation conflict: return the stable HTTP 409 code to the coordinator, refresh the match list and retry with a new key for a new attempt. Do not edit `reserved` directly.
- Lost response after a critical operation: retry with the original idempotency key and identical payload. Inspect audit records by correlation ID for traceability.
- Shipment cannot dispatch: ensure the request is fully allocated and pickup was recorded. Multiple allocations require separate shipments; all deliveries must be confirmed before fulfillment.
- Incident involving data integrity: stop mutating traffic, capture logs and a backup, inspect `inventory_movements`, `allocations`, `shipments`, `status_events` and `audit_events`; restore only after identifying the cause.

## Release and rollback

Run tests against a fresh database, take a backup, apply Alembic migrations, deploy API and web, and check readiness and the five-minute demo. `alembic downgrade -1` removes the entire initial schema at revision 0001 and is destructive; do not use it as a production rollback. Restore a tested backup for schema-level rollback. Follow the backup guide.
