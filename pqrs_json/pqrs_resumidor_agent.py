from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

def safe(value: Any, default: str = "no informado") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def normalize_incoming_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    usuario = item.get("usuario")

    if isinstance(usuario, dict):
        if not normalized.get("username") and usuario.get("username"):
            normalized["username"] = usuario.get("username")
        if not normalized.get("nombre") and usuario.get("nombre"):
            normalized["nombre"] = usuario.get("nombre")

    return normalized


def normalize_clasificacion(item: dict[str, Any]) -> str:
    clasificacion = safe(item.get("clasificacion"), default="").strip()
    if clasificacion:
        return clasificacion.capitalize()

    tipo = safe(item.get("tipo"), default="").strip()
    if tipo:
        return tipo.capitalize()

    pqrs = safe(item.get("pqrs"), default="").lower()
    if "queja" in pqrs or "inconformidad" in pqrs:
        return "Queja"
    if "peticion" in pqrs or "solicitud" in pqrs:
        return "Peticion"
    if "reclamo" in pqrs:
        return "Reclamo"
    return "Informacion"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    normalized = clean_text(text)
    if not normalized:
        return []
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(normalized) if s.strip()]


def clip(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def build_title(pqrs: str, clasificacion: str) -> str:
    sentences = split_sentences(pqrs)
    if not sentences:
        return f"{clasificacion}: caso sin detalle textual"
    return f"{clasificacion}: {clip(sentences[0], 95)}"


def build_summary(pqrs: str) -> str:
    sentences = split_sentences(pqrs)
    if not sentences:
        return "No se recibio contenido suficiente para generar un resumen."
    return clip(" ".join(sentences[:3]), 420)


def resumir_item(item: dict[str, Any]) -> dict[str, Any]:
    item = normalize_incoming_item(item)
    pqrs = safe(item.get("pqrs"), default="")
    clasificacion = normalize_clasificacion(item)
    titulo_ia = build_title(pqrs, clasificacion)

    result = dict(item)
    result["titulo_ia"] = titulo_ia
    result["resumen_ia"] = build_summary(pqrs)
    return result


def load_json_array(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if isinstance(data, dict):
        return [normalize_incoming_item(data)]

    if not isinstance(data, list):
        raise ValueError("El archivo de entrada debe contener un arreglo JSON o un objeto JSON.")

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Elemento en posicion {idx} no es un objeto JSON.")
        normalized.append(normalize_incoming_item(item))
    return normalized


def save_json(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def default_output_for(input_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return input_path.parent / f"pqrs_resumidas_{stamp}.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agente resumidor de PQRS clasificadas (estructura pqrs_json)."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Ruta del archivo JSON de entrada (array de PQRS clasificadas).",
    )
    parser.add_argument(
        "--output",
        required=False,
        help="Ruta del archivo JSON de salida. Si no se especifica, se genera en la misma carpeta.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_for(input_path)

    rows = load_json_array(input_path)
    resumidas = [resumir_item(item) for item in rows]
    save_json(output_path, resumidas)

    print(f"Entrada: {input_path}")
    print(f"Salida:  {output_path}")
    print(f"Procesadas: {len(resumidas)}")


if __name__ == "__main__":
    main()
