import argparse
import asyncio
import io
import json
import logging
import os
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()
logging.getLogger("pypdf").setLevel(logging.ERROR)


def _resolve_supabase_dsn() -> str:
    dsn = os.getenv("SUPABASE_DB_DSN", "").strip()
    if not dsn:
        raise ValueError("Falta SUPABASE_DB_DSN en .env.")
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
            raise ValueError("Falta SUPABASE_DB_HOST en .env.")
        if not password:
            raise ValueError("Falta SUPABASE_DB_PASSWORD en .env.")
        return {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "ssl": "require",
        }
    return {"dsn": _resolve_supabase_dsn()}


def _resolve_cohere_embed_config() -> tuple[str, str]:
    model = os.getenv("COHERE_EMBED_MODEL", "embed-v4.0").strip()
    api_key = os.getenv("COHERE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Falta COHERE_API_KEY en .env.")
    return model, api_key


def _embed_text(model: str, api_key: str, text: str) -> list[float]:
    endpoint = "https://api.cohere.com/v2/embed"
    payload = {
        "model": model,
        "texts": [text],
        "input_type": "search_document",
        "embedding_types": ["float"],
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
        values = ((body.get("embeddings") or {}).get("float") or [[]])[0]
        if not values:
            raise ValueError("Cohere no retorno embedding para el chunk.")
        return values
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        if exc.code == 401:
            raise ValueError("Cohere devolvio 401 Unauthorized. Verifica COHERE_API_KEY.") from exc
        if exc.code == 403:
            raise ValueError("Cohere devolvio 403 Forbidden. Revisa permisos de la API key.") from exc
        raise ValueError(f"Error de Cohere embeddings ({exc.code}): {error_body}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"No se pudo conectar a Cohere: {exc.reason}") from exc


def _build_embedding_client() -> tuple[str, str]:
    return _resolve_cohere_embed_config()


def _build_pdf_reader(pdf_path: Path) -> PdfReader:
    try:
        return PdfReader(str(pdf_path), strict=False)
    except Exception:
        raw_bytes = pdf_path.read_bytes()
        header_index = raw_bytes.find(b"%PDF")
        if header_index == -1:
            raise ValueError(f"El archivo no contiene un encabezado PDF valido: {pdf_path}")
        repaired = raw_bytes[header_index:]
        return PdfReader(io.BytesIO(repaired), strict=False)


def _extract_pdf_chunks(pdf_path: Path, pages_per_chunk: int = 20) -> list[dict[str, Any]]:
    reader = _build_pdf_reader(pdf_path)
    total_pages = len(reader.pages)
    chunks: list[dict[str, Any]] = []

    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        pages_text: list[str] = []
        for page_number in range(start, end):
            text = (reader.pages[page_number].extract_text() or "").strip()
            if text:
                pages_text.append(text)

        content = "\n\n".join(pages_text).strip()
        if not content:
            continue

        chunks.append(
            {
                "content": content,
                "metadata": {
                    "source": str(pdf_path),
                    "file_name": pdf_path.name,
                    "chunk_index": (start // pages_per_chunk) + 1,
                    "start_page": start + 1,
                    "end_page": end,
                    "total_pages": total_pages,
                    "pages_per_chunk": pages_per_chunk,
                },
            }
        )
    return chunks


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(value, ".10f") for value in values) + "]"


def _resolve_table_name() -> str:
    table = os.getenv("SUPABASE_VECTOR_TABLE", "documents").strip() or "documents"
    if not table.replace("_", "").isalnum():
        raise ValueError("SUPABASE_VECTOR_TABLE solo permite letras, numeros y guion bajo.")
    return table


def _normalize_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower().replace(" ", "")
    return normalized


def _resolve_existing_pdf_path(input_path: Path) -> Path:
    if input_path.exists():
        return input_path
    if input_path.suffix.lower() != ".pdf":
        return input_path
    parent = input_path.parent if str(input_path.parent) else Path(".")
    if not parent.exists():
        return input_path
    expected = _normalize_filename(input_path.name)
    for candidate in parent.glob("*.pdf"):
        if _normalize_filename(candidate.name) == expected:
            return candidate
    return input_path


async def _insert_chunk(
    conn: asyncpg.Connection,
    table: str,
    content: str,
    embedding: list[float],
    metadata: dict[str, Any],
) -> None:
    query = f"""
        INSERT INTO {table} (content, embedding, metadata)
        VALUES ($1, $2::vector, $3::jsonb)
    """
    await conn.execute(query, content, _vector_literal(embedding), json.dumps(metadata))


async def ingest_pdf(pdf_path: Path, pages_per_chunk: int = 20) -> int:
    if not pdf_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"El archivo no es PDF: {pdf_path}")

    table = _resolve_table_name()
    connect_kwargs = _resolve_supabase_connect_kwargs()
    embedding_model, embedding_api_key = _build_embedding_client()
    chunks = _extract_pdf_chunks(pdf_path, pages_per_chunk=pages_per_chunk)
    if not chunks:
        return 0

    try:
        conn = await asyncpg.connect(**connect_kwargs)
    except ValueError as exc:
        if "IPv4 or IPv6" in str(exc):
            raise ValueError(
                "DSN invalido por caracteres especiales en credenciales. "
                "Usa SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_USER, "
                "SUPABASE_DB_PASSWORD y SUPABASE_DB_NAME en .env."
            ) from exc
        raise

    try:
        inserted = 0
        for i, chunk in enumerate(chunks, 1):
            print(f"  Embebiendo chunk {i}/{len(chunks)}...", flush=True)
            embedding = _embed_text(embedding_model, embedding_api_key, chunk["content"])
            await _insert_chunk(conn, table, chunk["content"], embedding, chunk["metadata"])
            inserted += 1
    finally:
        await conn.close()

    return inserted


def _collect_pdfs(input_path: Path) -> list[Path]:
    resolved_input = _resolve_existing_pdf_path(input_path)
    if resolved_input.is_file():
        return [resolved_input]
    if not resolved_input.exists():
        raise FileNotFoundError(f"No existe la ruta: {input_path}")
    return sorted(path for path in resolved_input.rglob("*.pdf") if path.is_file())


async def ingest_path(input_path: Path, pages_per_chunk: int = 20) -> tuple[int, int]:
    total_files = 0
    total_chunks = 0
    for pdf_file in _collect_pdfs(input_path):
        print(f"Procesando: {pdf_file.name}")
        inserted = await ingest_pdf(pdf_file, pages_per_chunk=pages_per_chunk)
        total_files += 1
        total_chunks += inserted
    return total_files, total_chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Carga PDFs en Supabase con embeddings de Cohere.")
    parser.add_argument("path", type=Path, help="Ruta a un PDF o carpeta con PDFs.")
    parser.add_argument("--pages-per-chunk", type=int, default=20, help="Paginas por chunk (default: 20).")
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    if args.pages_per_chunk <= 0:
        raise ValueError("--pages-per-chunk debe ser mayor que 0.")
    files, chunks = await ingest_path(args.path, pages_per_chunk=args.pages_per_chunk)
    print(f"\nArchivos procesados: {files}")
    print(f"Chunks insertados:   {chunks}")


if __name__ == "__main__":
    asyncio.run(_main())
