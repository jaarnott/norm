# Database Package

Will contain database schemas, migrations, and connection setup.

## Planned Setup
- PostgreSQL via Docker
- SQLAlchemy + Alembic for migrations
- Or Prisma if we go TypeScript-first for DB layer

## Current State
Data is in-memory via `apps/api/app/data/seed.py`.
