# Security model

Report vulnerabilities privately to the project maintainer. Do not include real credentials or beneficiary data in reports.

## Trust boundaries

- **Browser to API:** authenticated database-backed sessions use random tokens stored as SHA-256 hashes. Cookies are HttpOnly for the session and SameSite=Lax; mutating requests require a second CSRF token and an allowed Origin. Set `SECURE_COOKIES=true` behind HTTPS. Login and registration are limited per hashed identity/IP bucket over 15 minutes. A session can be revoked on logout or rotated.
- **Organization boundary:** every protected route resolves an active membership and role on the server. Inventory and requests are scoped to the selected organization. Matching may read only active suppliers that explicitly share inventory; allocation rechecks eligibility under transaction locks. Supplier dispatch and requester delivery each validate the allocation relationship.
- **Database boundary:** the application role lacks schema ownership and cannot change or delete audit/status events. Migration uses an owner credential. Critical counts use `numeric(16,3)` and database checks. All SQL values use bound parameters; dynamic ordering is a closed allowlist.
- **Operational records:** audit rows store actor, organization, entity, action, correlation ID and relevant state; a database trigger rejects update/delete. Logs contain method, path, status and request ID; credentials, cookie values and bodies are excluded. The prototype does not collect recipient contact details or upload evidence files.

## Threats and residual risks

- Stolen sessions: HttpOnly cookies, 12-hour expiry, server-side revocation; use TLS, HTTPS cookies and short-lived credentials in deployment.
- Cross-tenant access: membership and object ownership checks, tested against both organization and role boundaries.
- Double allocation: request and lot row locks, conditional reserved update, database checks and idempotency key; concurrent PostgreSQL integration test.
- Abuse and denial of service: database-backed login throttling; reverse-proxy request limits and network controls are required for Internet exposure.
- Privileged database compromise: the owner credential can modify audit history and is reserved for migrations. External tamper-evident log shipping is a future hardening step.

This repository uses only synthetic demo data. Do not deploy with `.env.example` passwords or `DEMO_SEED=true`.
