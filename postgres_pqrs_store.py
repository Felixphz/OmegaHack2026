import os
from datetime import datetime
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


def _resolve_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise ValueError("Falta POSTGRES_DSN (o DATABASE_URL) para conectar PostgreSQL.")
    if dsn.startswith("psql "):
        dsn = dsn[5:].strip()
    if (dsn.startswith("'") and dsn.endswith("'")) or (dsn.startswith('"') and dsn.endswith('"')):
        dsn = dsn[1:-1].strip()
    return dsn


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=_resolve_dsn(), min_size=1, max_size=5)
    return _pool


async def save_pqrs_to_postgres(pqrs_json: dict[str, Any]) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pqrs (radicado, pqrs, canal, fecha_utc, username, nombre)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (radicado) DO UPDATE
            SET pqrs = EXCLUDED.pqrs,
                canal = EXCLUDED.canal,
                fecha_utc = EXCLUDED.fecha_utc,
                username = EXCLUDED.username,
                nombre = EXCLUDED.nombre
            """,
            pqrs_json["radicado"],
            pqrs_json["pqrs"],
            pqrs_json["canal"],
            datetime.fromisoformat(pqrs_json["fecha_utc"]),
            pqrs_json.get("username"),
            pqrs_json.get("nombre"),
        )
