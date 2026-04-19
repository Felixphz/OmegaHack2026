import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
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

CLASSIFIER_PROMPT = """
Eres un asistente experto en gestion de PQRSD en Colombia.
Debes clasificar una PQRSD y determinar el plazo de respuesta segun normativa,
usando estrictamente el contexto recuperado por RAG.
Tambien debes identificar, bajo el marco legal y de trato digno en la atencion publica,
si el lenguaje del ciudadano es grosero/ofensivo.

Reglas:
- Responde SOLO JSON valido.
- Formato exacto: {{"clasificacion":"<categoria>", "dias_respuesta":<entero>, "tipo_dias":"habiles", "irrespetuosa":<true|false>}}
- "clasificacion" debe ser una categoria clara (peticion, queja, reclamo, sugerencia, felicitacion, consulta, denuncia u otra equivalente).
- "dias_respuesta" debe ser entero positivo.
- "tipo_dias" siempre "habiles".
- "irrespetuosa" es true cuando hay insultos, agresiones verbales, humillaciones o amenazas directas.
- Si hay duda, usa clasificacion "peticion" y dias_respuesta 15.
- Si no hay evidencia clara de groseria, usa irrespetuosa=false.

PQRSD:
{pqrs_text}

Secretaria destino:
{secretaria}

Contexto RAG:
{rag_context}
"""

IRRESPECTFUL_KEYWORDS = (
    "idiota", "estupido", "estúpido", "imbecil", "imbécil", "malparido",
    "hp", "hpta", "gonorrea", "perra", "mierda", "hijueputa",
)


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
        raise ValueError("Falta OLLAMA_API_KEY para clasificacion.")
    return ChatOllama(
        model=model_name,
        temperature=0.0,
        base_url=base_url,
        client_kwargs={"headers": {"Authorization": f"Bearer {api_key}"}},
    )


def _build_classifier_chain(llm: ChatOllama):
    prompt = ChatPromptTemplate.from_messages([("human", CLASSIFIER_PROMPT)])
    return prompt | llm | StrOutputParser()


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
        parts.append(
            f"[Contexto {i}] similitud={float(row['similarity']):.4f}\n"
            f"metadata={json.dumps(metadata, ensure_ascii=False)}\n"
            f"texto={content[:max_chars_per_chunk]}"
        )
    return "\n\n".join(parts)


def _looks_irrespectful(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return any(keyword in normalized for keyword in IRRESPECTFUL_KEYWORDS)


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "verdadero", "si", "sí", "1")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _parse_classifier_output(raw: str, pqrs_text: str) -> tuple[str, int, bool]:
    clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    parsed = json.loads(clean)
    clasificacion = (parsed.get("clasificacion") or "").strip().lower() or "peticion"
    dias = int(parsed.get("dias_respuesta") or 15)
    if dias <= 0:
        dias = 15
    irrespetuosa = _parse_bool(parsed.get("irrespetuosa"), default=_looks_irrespectful(pqrs_text))
    return clasificacion, dias, irrespetuosa


def _parse_datetime_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _add_business_days(start_date: datetime, business_days: int) -> datetime:
    current = start_date
    remaining = business_days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _resolve_input_file(input_path: Path | None) -> Path:
    if input_path:
        return input_path
    base = Path("pqrs_json")
    files = sorted(base.glob("pqrs_ruteadas_*.json"))
    if not files:
        raise FileNotFoundError("No hay archivo pqrs_ruteadas_*.json en pqrs_json/.")
    return files[-1]


async def classify_json(input_path: Path, output_path: Path | None, top_k: int) -> Path:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("El JSON de entrada debe ser una lista de PQRSD.")

    rag_conn = await asyncpg.connect(**_resolve_supabase_connect_kwargs(), statement_cache_size=0)
    rag_table = _resolve_table_name()
    llm = _build_llm()
    chain = _build_classifier_chain(llm)

    try:
        enriched: list[dict[str, Any]] = []
        for item in data:
            pqrs_text = str(item.get("pqrs", "")).strip()
            secretaria = str(item.get("secretaria", "Secretaria General")).strip() or "Secretaria General"
            fecha_utc = str(item.get("fecha_utc", "")).strip()
            if not pqrs_text or not fecha_utc:
                raise ValueError("Cada registro debe incluir 'pqrs' y 'fecha_utc'.")

            query_embedding = _embed_query(pqrs_text)
            rag_rows = await _search_rag_context(rag_conn, rag_table, query_embedding, top_k)
            rag_context = _compact_context(rag_rows)

            raw = await chain.ainvoke(
                {"pqrs_text": pqrs_text, "secretaria": secretaria, "rag_context": rag_context}
            )
            try:
                clasificacion, dias_respuesta, irrespetuosa = _parse_classifier_output(raw, pqrs_text)
            except Exception:
                clasificacion, dias_respuesta, irrespetuosa = "peticion", 15, _looks_irrespectful(pqrs_text)

            fecha_base = _parse_datetime_utc(fecha_utc)
            fecha_limite = _add_business_days(fecha_base, dias_respuesta).date().isoformat()

            row = dict(item)
            row["clasificacion"] = clasificacion
            row["fecha_limite"] = fecha_limite
            row["irrespetuosa"] = irrespetuosa
            row["resuelta"] = False
            enriched.append(row)
    finally:
        await rag_conn.close()

    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path("pqrs_json") / f"pqrs_clasificadas_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clasifica PQRSD y calcula fecha limite con soporte RAG.")
    parser.add_argument("--input", type=Path, default=None, help="JSON de entrada de routing.")
    parser.add_argument("--output", type=Path, default=None, help="Ruta del JSON de salida.")
    parser.add_argument("--top-k", type=int, default=4, help="Cantidad de chunks RAG por PQRSD.")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    if args.top_k <= 0:
        raise ValueError("--top-k debe ser mayor que 0.")
    source = _resolve_input_file(args.input)
    output = await classify_json(input_path=source, output_path=args.output, top_k=args.top_k)
    print(f"JSON generado: {output}")


if __name__ == "__main__":
    asyncio.run(_main())
