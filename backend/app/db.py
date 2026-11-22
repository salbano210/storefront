"""Database engine + session factory.

SQLite (aiosqlite) by default -- which is exactly what a demo needs. Set
DATABASE_URL to a Postgres/Neon instance to make config edits and audit logs
survive redeploys.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from . import settings


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    url = settings.DATABASE_URL
    if not url:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{settings.DATA_DIR / 'storefront.sqlite'}"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    for param in ("sslmode=require", "channel_binding=require"):
        url = url.replace(f"&{param}", "").replace(f"?{param}", "")
    return url


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        future=True,
    )
else:  # pragma: no cover - exercised only against Postgres
    import ssl

    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"ssl": ssl.create_default_context()},
        pool_pre_ping=True,
        pool_recycle=120,
        pool_size=5,
        max_overflow=5,
    )

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if IS_SQLITE:

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_columns()


async def ensure_columns() -> list[str]:
    """Add columns that exist in the models but not yet in the database.

    ``create_all`` only creates missing *tables*, so a newly added column would
    never reach an existing SQLite file. Only nullable columns are added, which
    keeps this safe to run on every boot.
    """
    import sqlalchemy as sa

    from . import models  # noqa: F401

    added: list[str] = []

    async with engine.begin() as conn:

        def _sync(sync_conn):
            inspector = sa.inspect(sync_conn)
            tables = set(inspector.get_table_names())
            for table in Base.metadata.sorted_tables:
                if table.name not in tables:
                    continue
                present = {column["name"] for column in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in present or not column.nullable:
                        continue
                    ddl = (
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                        f"{column.type.compile(sync_conn.dialect)}"
                    )
                    sync_conn.execute(sa.text(ddl))
                    added.append(f"{table.name}.{column.name}")

        await conn.run_sync(_sync)
    return added


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
