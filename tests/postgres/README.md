# PostgreSQL Integration and Concurrency Tests

These tests are intentionally separate from the fast SQLite suite. They validate behavior that depends on PostgreSQL transaction scheduling, row locking, unique/partial indexes, and independent connections.

Run them only against a disposable test database:

```bash
export POSTGRES_TEST_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/gh_bot_factory_test'
export DATABASE_URL="$POSTGRES_TEST_DATABASE_URL"
make verify-postgres
```

The verification command applies all migrations, runs `alembic check`, truncates domain tables between tests, and executes the tests marked `postgres`.

Never point `POSTGRES_TEST_DATABASE_URL` at staging or production. The fixture issues `TRUNCATE ... CASCADE` before each test.
