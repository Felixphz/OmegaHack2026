from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def normalize_tipo(item: dict[str, Any]) -> str:
    tipo = safe(item.get("tipo"), default="").strip()
    if tipo:
        return tipo

    pqrs = safe(item.get("pqrs"), default="").lower()
    if "queja" in pqrs or "inconformidad" in pqrs:
        return "Queja"
    if "peticion" in pqrs or "solicitud" in pqrs:
        return "Peticion"
    if "reclamo" in pqrs:
        return "Reclamo"
    return "Informacion"


def build_dependencia(item: dict[str, Any]) -> str:
    secretaria = safe(item.get("secretaria"), default="Secretaria por definir")
    division = safe(item.get("division"), default="Division por definir")
    return f"{secretaria} - {division}"


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


def build_title(pqrs: str, tipo: str) -> str:
    sentences = split_sentences(pqrs)
    if not sentences:
        return f"{tipo}: caso sin detalle textual"
    return f"{tipo}: {clip(sentences[0], 95)}"


def build_bullets(pqrs: str, dependencia: str, canal: str) -> list[str]:
    sentences = split_sentences(pqrs)
    bullets = sentences[:2]

    if not bullets:
        bullets.append("No se recibio detalle narrativo de la PQRS en el texto de entrada.")
    if len(bullets) == 1:
        bullets.append("Se recomienda validar anexos, antecedentes y normativa aplicable antes de la respuesta final.")

    bullets.append(f"Canal de recepcion: {safe(canal)}. Dependencia sugerida: {dependencia}.")
    return bullets


def format_nombre(nombre: str) -> str:
    clean = clean_text(nombre)
    if not clean:
        return "ciudadano"
    return clean.split(" ")[0]


def infer_tema(pqrs: str, tipo: str) -> str:
    sentences = split_sentences(pqrs)
    if not sentences:
        return f"solicitud asociada a {tipo.lower()}"
    return clip(sentences[0], 110)


def build_borrador(item: dict[str, Any], tipo: str, dependencia: str) -> str:
    nombre = format_nombre(safe(item.get("nombre"), default=""))
    canal = safe(item.get("canal"))
    fecha_utc = safe(item.get("fecha_utc"), default="sin fecha registrada")
    tema = infer_tema(safe(item.get("pqrs"), default=""), tipo)

    return (
        f"Estimada {nombre},\n\n"
        f"De acuerdo con su {tipo.lower()} recibida por el canal {canal} el {fecha_utc}, "
        f"le informamos que su caso fue direccionado a {dependencia} para la revision tecnica correspondiente.\n\n"
        f"Sobre el asunto reportado ({tema}), adelantaremos la validacion de antecedentes y requisitos "
        "aplicables para entregarle una respuesta clara y completa dentro de los terminos legales de PQRSD.\n\n"
        "Si requiere ampliar informacion, puede responder por este mismo canal indicando su numero de radicado.\n\n"
        "Cordial saludo,\n"
        "Equipo de Atencion PQRSD"
    )


def resumir_item(item: dict[str, Any]) -> dict[str, Any]:
    item = normalize_incoming_item(item)
    pqrs = safe(item.get("pqrs"), default="")
    tipo = normalize_tipo(item)
    dependencia = build_dependencia(item)
    titulo_ia = build_title(pqrs, tipo)

    result = dict(item)
    result["tipo"] = tipo
    result["dependencia_sugerida"] = dependencia
    result["titulo_ia"] = titulo_ia
    result["resumen_bullets"] = build_bullets(pqrs, dependencia, safe(item.get("canal")))
    result["borrador_respuesta"] = build_borrador(item, tipo, dependencia)
    result["resumido_en_utc"] = now_utc_iso()
    return result


def load_json_array(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
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
