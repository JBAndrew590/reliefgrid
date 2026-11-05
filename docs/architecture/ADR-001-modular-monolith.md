# ADR 001 — Modular monolith with PostgreSQL/PostGIS

**Status:** Accepted · 2026-11-05

## Context

A local emergency-coordination workflow must preserve inventory, request and shipment invariants across organizations. Cross-service transactions would add failure modes without a measured need for independent scaling.

## Decision

Use Next.js for the browser, a FastAPI application for business operations, and PostgreSQL/PostGIS for durable transactional and spatial data. The database is the single source of truth. Use explicit SQL in SQLAlchemy transactions for the critical workflow. Alembic owns versioned DDL. Do not introduce Redis, a queue or separate services until a measured workload requires them.

## Consequences

Reservations and audit records commit together. Deployments are simpler, and concurrency behavior can be tested against the actual database. The API package should be split into modules as it grows. A small database-backed expiration worker runs independently without changing the allocation transaction.
