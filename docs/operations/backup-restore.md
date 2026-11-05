# Backup and restore

The local `make backup` command writes a PostgreSQL custom-format dump to `backups/reliefgrid.dump`. Backups may contain sensitive production data; protect access and retention. The `backups/` directory is ignored by Git.

```bash
make backup
make restore FILE=backups/reliefgrid.dump
```

Restore uses `pg_restore --clean --if-exists` and overwrites matching database objects. Stop API writes, take a fresh backup and verify the target database before using it. For a production environment, use an encrypted backup destination, tested restore schedule, point-in-time recovery via WAL archiving and separate migration/restore credentials. A database dump alone does not prove recovery; run readiness and workflow checks after restore.
