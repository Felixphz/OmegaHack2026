import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg
import urllib.error
import urllib.request
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama

load_dotenv()

ROUTER_PROMPT = """
Eres un asistente experto en enrutamiento de PQRSD para administracion publica colombiana.
Con base en el texto de la PQRSD y el contexto normativo/documental recuperado por RAG,
debes decidir a cual secretaria se debe remitir.

Reglas:
- Responde SOLO JSON valido.
- Formato exacto: {{"secretaria":"<nombre>"}}
- Usa un nombre concreto de secretaria (ejemplo: "Secretaria de Movilidad", "Secretaria de Salud").
- Si no hay evidencia suficiente, usa: {{"secretaria":"Secretaria General"}}.

Texto PQRSD:
{pqrs_text}

Contexto RAG:
{rag_context}
"""


def _resolve_pqrs_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        raise ValueError("Falta POSTGRES_DSN (o DATABASE_URL).")
    if dsn.startswith("psql "):
        dsn = dsn[5:].strip()
    if (dsn.startswith("'") and dsn.endswith("'")) or (dsn.startswith('"') and dsn.endswith('"')):
        dsn = dsn[1:-1].strip()
    return dsn


def _resolve_supabase_connect_kwargs() -> dict[str, Any]:
    host = os.getenv("SUPABASE_DB_HOST", "").strip()
    password = os.getenv("SUPABASE_DB_PASSWORD", "").strip()
    if host or password:
        user = os.getenv("SUPABASE_DB_USER", "postgres").strip() or "postgres"
        database = os.getenv("SUPABASE_DB_NAME", "postgres").strip() or "postgres"
        port = int(os.getenv("SUPABASE_DB_PORT", "5432").strip() or "5432")
        if not host:
            raise ValueError("Falta SUPABASE_DB_HOST.")
        if not password:
            raise ValueError("Falta SUPABASE_DB_PASSWORD.")
        return {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "ssl": "require",
        }

    dsn = os.getenv("SUPABASE_DB_DSN", "").strip()
    if not dsn:
        raise ValueError("Falta SUPABASE_DB_DSN o variables host/port/user/password.")
    return {"dsn": dsn}


def _resolve_table_name() -> str:
    table = os.getenv("SUPABASE_VECTOR_TABLE", "documents").strip() or "documents"
    if not table.replace("_", "").isalnum():
        raise ValueError("SUPABASE_VECTOR_TABLE solo permite letras, numeros y guion bajo.")
    return table


def _resolve_cohere_embed_config() -> tuple[str, str]:
    model = os.getenv("COHERE_EMBED_MODEL", "embed-v4.0").strip()
    api_key = os.getenv("COHERE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Falta COHERE_API_KEY para embeddings.")
    return model, api_key


def _embed_query(text: str) -> list[float]:
    model, api_key = _resolve_cohere_embed_config()
    endpoint = "https://api.cohere.com/v2/embed"
    payload = {
        "model": model,
        "texts": [text],
        "input_type": "search_query",
        "embedding_types": ["float"],
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
        embedding = ((body.get("embeddings") or {}).get("float") or [[]])[0]
        if not embedding:
            raise ValueError("Cohere no devolvio embedding de consulta.")
        return embedding
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise ValueError(f"Error embeddings Cohere ({exc.code}): {details}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"No se pudo conectar a Cohere: {exc.reason}") from exc


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(value, ".10f") for value in values) + "]"


def _build_llm() -> ChatOllama:
    model_name = os.getenv("OLLAMA_MODEL", "gpt-oss:20b").strip()
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com").strip()
    if not api_key:
        raise ValueError("Falta OLLAMA_API_KEY para el enrutamiento.")
    return ChatOllama(
        model=model_name,
        temperature=0.1,
        base_url=base_url,
        client_kwargs={"headers": {"Authorization": f"Bearer {api_key}"}},
    )


def _build_router_chain(llm: ChatOllama):
    prompt = ChatPromptTemplate.from_messages([("human", ROUTER_PROMPT)])
    return prompt | llm | StrOutputParser()


async def _fetch_pqrs_rows(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT radicado, pqrs, canal, fecha_utc, username, nombre
        FROM pqrs
        ORDER BY fecha_utc DESC
        """
    )


async def _search_rag_context(
    conn: asyncpg.Connection,
    table: str,
    query_embedding: list[float],
    top_k: int,
) -> list[asyncpg.Record]:
    query = f"""
        SELECT content, metadata, 1 - (embedding <=> $1::vector) AS similarity
        FROM {table}
        ORDER BY embedding <=> $1::vector
        LIMIT $2
    """
    return await conn.fetch(query, _vector_literal(query_embedding), top_k)


def _compact_context(rows: list[asyncpg.Record], max_chars_per_chunk: int = 900) -> str:
    parts: list[str] = []
    for i, row in enumerate(rows, start=1):
        content = (row["content"] or "").strip()
        metadata = row["metadata"] or {}
        snippet = content[:max_chars_per_chunk]
        parts.append(
            f"[Contexto {i}] similitud={float(row['similarity']):.4f}\n"
            f"metadata={json.dumps(metadata, ensure_ascii=False)}\n"
            f"texto={snippet}"
        )
    return "\n\n".join(parts)


def _parse_secretaria(raw: str) -> str:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(clean)
    secretaria = (data.get("secretaria") or "").strip()
    return secretaria or "Secretaria General"


async def route_single_pqrs(
    router_chain: Any,
    rag_conn: asyncpg.Connection,
    rag_table: str,
    pqrs_text: str,
    top_k: int,
) -> str:
    query_embedding = _embed_query(pqrs_text)
    rag_rows = await _search_rag_context(rag_conn, rag_table, query_embedding, top_k)
    rag_context = _compact_context(rag_rows)
    raw = await router_chain.ainvoke({"pqrs_text": pqrs_text, "rag_context": rag_context})
    try:
        return _parse_secretaria(raw)
    except Exception:
        return "Secretaria General"


async def generate_routed_json(top_k: int, output_path: Path | None) -> Path:
    pqrs_conn = await asyncpg.connect(dsn=_resolve_pqrs_dsn(), statement_cache_size=0)
    rag_conn = await asyncpg.connect(**_resolve_supabase_connect_kwargs(), statement_cache_size=0)
    rag_table = _resolve_table_name()
    llm = _build_llm()
    router_chain = _build_router_chain(llm)

    try:
        rows = await _fetch_pqrs_rows(pqrs_conn)
        routed: list[dict[str, Any]] = []
        for row in rows:
            secretaria = await route_single_pqrs(
                router_chain=router_chain,
                rag_conn=rag_conn,
                rag_table=rag_table,
                pqrs_text=row["pqrs"],
                top_k=top_k,
            )
            routed.append(
                {
                    "radicado": row["radicado"],
                    "pqrs": row["pqrs"],
                    "canal": row["canal"],
                    "fecha_utc": row["fecha_utc"].isoformat() if row["fecha_utc"] else None,
                    "username": row["username"],
                    "nombre": row["nombre"],
                    "secretaria": secretaria,
                }
            )
    finally:
        await pqrs_conn.close()
        await rag_conn.close()

    if output_path is None:
        out_dir = Path("pqrs_json")
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = out_dir / f"pqrs_ruteadas_{timestamp}.json"

    output_path.write_text(json.dumps(routed, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agente RAG para enrutar PQRSD a secretarias.")
    parser.add_argument("--top-k", type=int, default=4, help="Cantidad de chunks RAG por PQRSD.")
    parser.add_argument("--output", type=Path, default=None, help="Ruta de salida del JSON.")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    if args.top_k <= 0:
        raise ValueError("--top-k debe ser mayor que 0.")
    output = await generate_routed_json(top_k=args.top_k, output_path=args.output)
    print(f"JSON generado: {output}")


if __name__ == "__main__":
    asyncio.run(_main())
