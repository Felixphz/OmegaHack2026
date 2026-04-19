from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg
from dotenv import load_dotenv

from app.agents.pqrs_classification_agent import (
    _add_business_days,
    _build_classifier_chain,
    _build_llm as _build_classifier_llm,
    _compact_context,
    _default_respuesta_sugerida,
    _embed_query,
    _parse_classifier_output,
    _parse_datetime_utc,
    _search_rag_context,
)
from app.agents.pqrs_resumidor_agent import resumir_item
from app.agents.pqrs_routing_agent import (
    _build_llm as _build_router_llm,
    _build_router_chain,
    _resolve_pqrs_dsn,
    _resolve_supabase_connect_kwargs,
    _resolve_table_name,
    route_single_pqrs,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _resolve_processed_table_name() -> str:
    table = os.getenv("PQRS_PROCESSED_TABLE", "pqrs_procesada").strip() or "pqrs_procesada"
    if not table.replace("_", "").isalnum():
        raise ValueError("PQRS_PROCESSED_TABLE solo permite letras, numeros y guion bajo.")
    return table


async def _ensure_processed_table(conn: asyncpg.Connection, table: str) -> None:
    query = f"""
        CREATE TABLE IF NOT EXISTS {table} (
            radicado VARCHAR(20) PRIMARY KEY,
            pqrs TEXT NOT NULL,
            canal VARCHAR(50),
            fecha_utc TIMESTAMP WITH TIME ZONE,
            username VARCHAR(100),
            nombre VARCHAR(150),
            secretaria VARCHAR(150),
            titulo_ia TEXT,
            resumen_ia TEXT,
            clasificacion VARCHAR(50),
            fecha_limite DATE,
            respuesta_sugerida TEXT,
            irrespetuosa BOOLEAN,
            resuelta BOOLEAN DEFAULT false
        )
    """
    await conn.execute(query)
    await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS respuesta_sugerida TEXT")


async def _fetch_pending_pqrs(
    conn: asyncpg.Connection,
    processed_table: str,
    batch_size: int,
) -> list[asyncpg.Record]:
    query = f"""
        SELECT p.radicado, p.pqrs, p.canal, p.fecha_utc, p.username, p.nombre
        FROM pqrs p
        LEFT JOIN {processed_table} pp ON pp.radicado = p.radicado
        WHERE pp.radicado IS NULL
        ORDER BY p.fecha_utc ASC NULLS LAST, p.radicado ASC
        LIMIT $1
    """
    return await conn.fetch(query, batch_size)


async def _insert_processed_row(
    conn: asyncpg.Connection,
    table: str,
    row: dict[str, Any],
) -> bool:
    query = f"""
        INSERT INTO {table} (
            radicado, pqrs, canal, fecha_utc, username, nombre,
            secretaria, titulo_ia, resumen_ia, clasificacion,
            fecha_limite, respuesta_sugerida, irrespetuosa, resuelta
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10,
            $11, $12, $13, $14
        )
        ON CONFLICT (radicado) DO NOTHING
    """
    status = await conn.execute(
        query,
        row["radicado"],
        row["pqrs"],
        row.get("canal"),
        row.get("fecha_utc"),
        row.get("username"),
        row.get("nombre"),
        row.get("secretaria"),
        row.get("titulo_ia"),
        row.get("resumen_ia"),
        row.get("clasificacion"),
        row.get("fecha_limite"),
        row.get("respuesta_sugerida"),
        row.get("irrespetuosa"),
        row.get("resuelta", False),
    )
    return status.endswith("1")


def _coerce_fecha_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return _parse_datetime_utc(str(value))


async def _process_single_pqrs(
    source_row: asyncpg.Record,
    routing_chain: Any,
    classifier_chain: Any,
    rag_conn: asyncpg.Connection,
    rag_table: str,
    routing_top_k: int,
    classification_top_k: int,
) -> dict[str, Any]:
    pqrs_text = str(source_row["pqrs"] or "").strip()
    if not pqrs_text:
        raise ValueError(f"PQRS vacia para radicado {source_row['radicado']}.")

    secretaria = await route_single_pqrs(
        router_chain=routing_chain,
        rag_conn=rag_conn,
        rag_table=rag_table,
        pqrs_text=pqrs_text,
        top_k=routing_top_k,
    )

    routed_item = {
        "radicado": source_row["radicado"],
        "pqrs": pqrs_text,
        "canal": source_row["canal"],
        "fecha_utc": source_row["fecha_utc"].isoformat() if source_row["fecha_utc"] else None,
        "username": source_row["username"],
        "nombre": source_row["nombre"],
        "secretaria": secretaria,
    }
    summarized_item = resumir_item(routed_item)

    query_embedding = _embed_query(pqrs_text)
    rag_rows = await _search_rag_context(rag_conn, rag_table, query_embedding, classification_top_k)
    rag_context = _compact_context(rag_rows)

    raw = await classifier_chain.ainvoke(
        {"pqrs_text": pqrs_text, "secretaria": secretaria, "rag_context": rag_context}
    )
    try:
        clasificacion, dias_respuesta, irrespetuosa, respuesta_sugerida = _parse_classifier_output(
            raw, pqrs_text
        )
    except Exception:
        clasificacion, dias_respuesta, irrespetuosa, respuesta_sugerida = (
            "peticion",
            15,
            False,
            _default_respuesta_sugerida(),
        )

    fecha_base = _coerce_fecha_utc(source_row["fecha_utc"])
    fecha_limite = _add_business_days(fecha_base, dias_respuesta).date()

    processed = dict(summarized_item)
    processed["clasificacion"] = clasificacion
    processed["fecha_limite"] = fecha_limite
    processed["irrespetuosa"] = irrespetuosa
    processed["respuesta_sugerida"] = respuesta_sugerida
    processed["resuelta"] = False
    processed["fecha_utc"] = fecha_base
    return processed


async def run_orchestrator(
    batch_size: int,
    routing_top_k: int,
    classification_top_k: int,
    watch: bool,
    poll_interval: float,
) -> None:
    processed_table = _resolve_processed_table_name()
    rag_table = _resolve_table_name()

    source_conn = await asyncpg.connect(dsn=_resolve_pqrs_dsn(), statement_cache_size=0)
    rag_conn = await asyncpg.connect(**_resolve_supabase_connect_kwargs(), statement_cache_size=0)
    target_conn = source_conn

    routing_llm = _build_router_llm()
    routing_chain = _build_router_chain(routing_llm)
    classifier_llm = _build_classifier_llm()
    classifier_chain = _build_classifier_chain(classifier_llm)

    try:
        await _ensure_processed_table(target_conn, processed_table)
        logger.info("Orquestador iniciado. Tabla destino: %s", processed_table)

        while True:
            pending_rows = await _fetch_pending_pqrs(source_conn, processed_table, batch_size)
            if not pending_rows:
                if not watch:
                    logger.info("No hay PQRSD pendientes.")
                    break
                await asyncio.sleep(poll_interval)
                continue

            inserted = 0
            for row in pending_rows:
                radicado = row["radicado"]
                try:
                    processed_row = await _process_single_pqrs(
                        source_row=row,
                        routing_chain=routing_chain,
                        classifier_chain=classifier_chain,
                        rag_conn=rag_conn,
                        rag_table=rag_table,
                        routing_top_k=routing_top_k,
                        classification_top_k=classification_top_k,
                    )
                    was_inserted = await _insert_processed_row(target_conn, processed_table, processed_row)
                    if was_inserted:
                        inserted += 1
                        logger.info("Radicado %s procesado y guardado.", radicado)
                    else:
                        logger.info("Radicado %s ya existia en %s.", radicado, processed_table)
                except Exception as exc:
                    logger.exception("Fallo procesando radicado %s: %s", radicado, exc)
            logger.info("Lote procesado. pendientes=%d insertados=%d", len(pending_rows), inserted)

            if not watch and len(pending_rows) < batch_size:
                break
    finally:
        await rag_conn.close()
        await source_conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Orquestador de agentes PQRS (routing -> resumen -> clasificacion).")
    parser.add_argument("--batch-size", type=int, default=20, help="Cantidad de PQRSD por lote.")
    parser.add_argument("--routing-top-k", type=int, default=4, help="Top-k de RAG para routing.")
    parser.add_argument("--classification-top-k", type=int, default=4, help="Top-k de RAG para clasificacion.")
    parser.add_argument("--watch", action="store_true", help="Ejecuta en modo continuo.")
    parser.add_argument("--poll-interval", type=float, default=15.0, help="Segundos de espera entre ciclos en --watch.")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size debe ser mayor que 0.")
    if args.routing_top_k <= 0:
        raise ValueError("--routing-top-k debe ser mayor que 0.")
    if args.classification_top_k <= 0:
        raise ValueError("--classification-top-k debe ser mayor que 0.")
    if args.poll_interval <= 0:
        raise ValueError("--poll-interval debe ser mayor que 0.")

    await run_orchestrator(
        batch_size=args.batch_size,
        routing_top_k=args.routing_top_k,
        classification_top_k=args.classification_top_k,
        watch=args.watch,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    asyncio.run(_main())
