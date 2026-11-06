# Changelog

## 2026-11-06

- Verified migration upgrade/downgrade/upgrade and 10 non-concurrency tests against embedded PostgreSQL/PostGIS (PGlite); native PostgreSQL concurrency and browser checks remain pending.
- Hid inactive organizations from shared map results and restricted role changes to existing members while preserving at least one active administrator.
- Removed an unnecessary `request_items` row lock that required a permission the limited runtime role does not have; request and lot locks still serialize allocations.
- Installed API test dependencies during test-image build, and added a PowerShell quick start.

## 0.1.0 — 2026-11-05

- Initial modular monolith for resource supply, requests, explainable matching, atomic reservations, shipment delivery, audit events and operational dashboard.
- PostGIS schema, synthetic demo seed, reproducible benchmark harness, browser UI and CI verification configuration.
