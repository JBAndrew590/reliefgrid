# API examples

OpenAPI: `http://localhost:8000/docs` after local startup. The API returns an error envelope: `{"error":{"code":"INSUFFICIENT_STOCK","message":"..."},"request_id":"..."}`. Numeric quantities in responses are decimal strings.

The browser client sends `X-Organization-ID` for the selected active membership and `X-CSRF-Token` matching the CSRF cookie on mutations. Use a cookie jar in CLI requests. IDs shown below must be taken from earlier responses; examples are schematic, not a fixed dataset.

```bash
curl -c cookies.txt -b cookies.txt -H 'Content-Type: application/json' \
  -d '{"email":"inventory@reliefgrid.local","password":"<your DEMO_PASSWORD>"}' \
  http://localhost:8000/v1/auth/login
curl -b cookies.txt -H 'X-Organization-ID: <org-uuid>' \
  http://localhost:8000/v1/resources
curl -b cookies.txt -H 'X-Organization-ID: <org-uuid>' \
  'http://localhost:8000/v1/locations?latitude=-6.14&longitude=106.84&radius_km=60'
```

For `POST /v1/requests`, supply an `Idempotency-Key` (8–120 characters), destination, priority, reason, timezone-aware deadline and resource items. `POST /v1/requests/{id}/transition/SUBMITTED` and `/VERIFIED` advance the request. `POST /v1/requests/{id}/match` returns component scores; `POST /v1/allocations` accepts `request_item_id`, `lot_id`, `quantity` and an idempotency key. The source organization creates a shipment, records pickup and dispatch; either authorized party can confirm delivery. A repeated identical idempotency key returns the stored response; a changed input returns HTTP 409.

Inventory and request lists support `limit` (1–100), `sort=newest|oldest`, `cursor`, and optional resource/status/priority/date-range/full-text filters. Audit events use a descending `before_id` cursor. All list filtering is server-side.
