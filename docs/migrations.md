# DB Migration Convention

IRMS uses an **inline Python migration system** built into `src/database.py`.
Each migration is a code block that runs once on server startup and is then
recorded in the `schema_migrations` table so it never re-runs.

This file documents how to add a new migration safely. The system is small on
purpose — there is no Alembic, no SQL files, no CLI. Every change goes through
the same single function so the order is always reproducible.

---

## How it works

1. On startup, `init_db()` (in `src/database.py`) opens a connection and calls
   `apply_schema_migrations(connection)`.
2. `apply_schema_migrations` executes every migration block in declaration
   order. Each block:
   - Checks `has_migration(connection, "<name>")` — skips if already applied.
   - Runs the SQL/Python work (DDL, data backfill, etc.).
   - Calls `record_migration(connection, "<name>")` to mark it done.
3. The `schema_migrations` table stores `(name TEXT PRIMARY KEY, applied_at TEXT)`.

Idempotent helpers already provided:
- `ensure_column(conn, table, column, definition)` — adds a column only if
  missing. Use this instead of bare `ALTER TABLE ADD COLUMN` to avoid errors
  on re-runs.

---

## Adding a new migration

1. Open `src/database.py`.
2. Append a new block at the **end** of `apply_schema_migrations` (never
   insert in the middle — order matters across deployed PCs).
3. Pick a unique snake_case name. The convention is
   `<feature>_<change>` — e.g. `recipes_add_archived_flag`,
   `attendance_users_drop_legacy_email`.
4. Wrap the work in a `has_migration` guard.

Template:

```python
# <short purpose, link to PDCA report if available>
if not has_migration(connection, "<your_migration_name>"):
    connection.execute(
        """
        UPDATE recipes
           SET archived = 0
         WHERE archived IS NULL
        """
    )
    record_migration(connection, "<your_migration_name>")
```

For schema additions, prefer `ensure_column` so a manual hot-fix on one PC
won't desync from a fresh install:

```python
ensure_column(connection, "recipes", "archived", "INTEGER DEFAULT 0")
```

If you're adding a new table:

```python
if not has_migration(connection, "create_table_<name>"):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS <name> (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ...
        )
        """
    )
    record_migration(connection, "create_table_<name>")
```

Also add the new table to `_ALLOWED_TABLES` so `ensure_column` permits future
column changes.

---

## Don'ts

- ❌ Don't edit a migration that has already shipped to the field server.
  Add a follow-up migration instead. The recorded name in
  `schema_migrations` won't re-run, so your edit silently has no effect on
  PCs that already applied it.
- ❌ Don't reorder existing migration blocks — newer PCs will get a different
  state than older ones.
- ❌ Don't run destructive operations (`DROP TABLE`, `DELETE FROM ... WHERE`
  on bulk data) without a fresh DB backup. `update_and_run.bat` snapshots
  `data/irms.db` to `backups/` before every pull, but a manual run bypasses
  that.

---

## Audit trail

To inspect what's been applied on a running server:

```sql
SELECT name, applied_at
  FROM schema_migrations
 ORDER BY applied_at;
```

Past migrations live in `apply_schema_migrations` and are easy to grep:

```bash
grep -n "has_migration" src/database.py
```
