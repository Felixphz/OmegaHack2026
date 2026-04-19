import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from app.storage.postgres_pqrs_store import save_pqrs_to_postgres
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

OUT_OF_SCOPE_RESPONSE = (
    "No puedo resolver estas solicitudes. "
    "Solo puedo ayudarte a registrar y canalizar PQRSAI."
)

GREETING_KEYWORDS = (
    "hola", "buenas", "buen dia", "buenos dias",
    "buenas tardes", "buenas noches",
)

QUESTION_PREFIXES = (
    "que ", "qué ", "quien ", "quién ", "como ", "cómo ",
    "cuando ", "cuándo ", "donde ", "dónde ", "cual ", "cuál ",
    "cuanto ", "cuánto ",
)

GENERAL_QUESTION_PATTERNS = (
    "que dia es hoy", "qué dia es hoy", "qué día es hoy", "que día es hoy",
    "que hora es", "qué hora es", "fecha de hoy", "dia de la semana",
    "cuanto es", "cuánto es", "capital de", "quien es", "quién es",
    "como estas", "cómo estas", "cómo estás", "como estás",
)

PQRS_CONTEXT_KEYWORDS = (
    "pqrs", "peticion", "petición", "queja", "reclamo", "sugerencia",
    "felicitacion", "felicitación", "tramite", "trámite", "servicio",
    "atencion", "atención", "entidad", "alcaldia", "alcaldía", "secretaria",
    "secretaría", "impuesto", "subsidio", "permiso", "licencia", "factura",
    "cobro", "pago", "agua", "luz", "gas", "basura", "transporte",
    "hospital", "salud", "educacion", "educación", "espacio publico", "espacio público",
)

PQRS_EXPERIENCE_KEYWORDS = (
    "servicio al cliente", "atencion al cliente", "atención al cliente",
    "me dejaron esperando", "mucho tiempo", "sin solucion", "sin solución",
    "no me dieron", "mala experiencia", "inconformidad", "frustracion", "frustración",
    "no resolvieron", "no solucionaron", "demora", "demorado",
    "peticion", "petición", "queja", "reclamo", "sugerencia", "felicitacion", "felicitación",
)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

CLASSIFIER_PROMPT = (
    "Eres el clasificador de mensajes de PQRSAI, el canal digital de atencion ciudadana "
    "de una secretaria del estado colombiano.\n\n"
    "Tu unica tarea es determinar si el mensaje del ciudadano corresponde a una PQRSD "
    "valida dentro del ambito de los servicios publicos y la administracion estatal colombiana, "
    "o si esta fuera de ese dominio.\n\n"
    "Categorias:\n\n"
    "- es_pqrs: El ciudadano expresa algo relacionado con servicios publicos, tramites, "
    "atencion institucional, obras, salud, educacion, transporte, servicios de agua/luz/gas, "
    "licencias, permisos, subsidios, impuestos, seguridad o espacio publico.\n\n"
    "- es_saludo: Mensajes unicamente de saludo (hola, buenos dias) o preguntas sobre el funcionamiento del bot.\n\n"
    "- fuera_de_alcance: Cualquier tema AJENO a tramites y servicios del estado. "
    "Esto incluye:\n"
    "  1. Tiempo y Calendario: preguntas sobre el dia actual, la hora, años bisiestos, etc.\n"
    "  2. Cultura General: capitales, historia, datos cientificos, matematicas.\n"
    "  3. Temas Personales o Humor: '¿como estas?', chistes, opiniones personales.\n"
    "  4. Politica y Noticias: figuras publicas o eventos actuales.\n\n"
    "Ejemplos:\n"
    "'llevan semanas sin recoger la basura' -> "
    "{{\"es_pqrs\": true, \"es_saludo\": false, \"fuera_de_alcance\": false}}\n"
    "'que dia de la semana es hoy' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": false, \"fuera_de_alcance\": true}}\n"
    "'cuantos dias tiene un año' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": false, \"fuera_de_alcance\": true}}\n"
    "'hola' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": true, \"fuera_de_alcance\": false}}\n"
    "'quien es el alcalde' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": false, \"fuera_de_alcance\": true}}\n\n"
    "Responde UNICAMENTE con el JSON. Sin texto adicional.\n\n"
    "Mensaje del usuario: {texto}"
)


def _is_greeting(text: str) -> bool:
    normalized = text.lower().strip()
    return any(normalized.startswith(kw) for kw in GREETING_KEYWORDS)


def _looks_out_of_scope_question(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    if any(pattern in normalized for pattern in GENERAL_QUESTION_PATTERNS):
        return True
    if "?" in normalized and any(normalized.startswith(prefix) for prefix in QUESTION_PREFIXES):
        return not any(keyword in normalized for keyword in PQRS_CONTEXT_KEYWORDS)
    return False


def _looks_like_pqrs(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    if any(keyword in normalized for keyword in PQRS_CONTEXT_KEYWORDS):
        return True
    return any(keyword in normalized for keyword in PQRS_EXPERIENCE_KEYWORDS)


async def classify_message(text: str, llm: ChatOllama) -> dict:
    if _is_greeting(text):
        return {"es_pqrs": False, "es_saludo": True, "fuera_de_alcance": False}
    if _looks_out_of_scope_question(text) and not _looks_like_pqrs(text):
        return {"es_pqrs": False, "es_saludo": False, "fuera_de_alcance": True}
    try:
        prompt = ChatPromptTemplate.from_messages([("human", CLASSIFIER_PROMPT)])
        chain = prompt | llm | StrOutputParser()
        raw = await chain.ainvoke({"texto": text})
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(clean)
        for key in ("es_pqrs", "es_saludo", "fuera_de_alcance"):
            if key not in result:
                raise ValueError(f"Clave faltante: {key}")
        if result.get("fuera_de_alcance") and _looks_like_pqrs(text):
            return {"es_pqrs": True, "es_saludo": False, "fuera_de_alcance": False}
        if result.get("es_pqrs") and _looks_out_of_scope_question(text) and not _looks_like_pqrs(text):
            return {"es_pqrs": False, "es_saludo": False, "fuera_de_alcance": True}
        return result
    except Exception as exc:
        logger.warning("Clasificador fallo, usando heuristica local: %s", exc)
        if _looks_like_pqrs(text):
            return {"es_pqrs": True, "es_saludo": False, "fuera_de_alcance": False}
        return {"es_pqrs": False, "es_saludo": False, "fuera_de_alcance": True}


def build_pqrs_json(update: Update, message_text: str) -> dict:
    user = update.effective_user
    return {
        "radicado": str(uuid.uuid4())[:8].upper(),
        "pqrs": message_text,
        "canal": "telegram",
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "username": user.username if user else None,
        "nombre": user.full_name if user else None,
    }


async def save_to_database(pqrs_json: dict) -> bool:
    await save_pqrs_to_postgres(pqrs_json)
    return True


async def persist_pqrs(pqrs_json: dict) -> tuple[bool, str]:
    for attempt in range(MAX_RETRIES):
        try:
            await save_to_database(pqrs_json)
            logger.info("PQRS guardada en BD. Radicado: %s", pqrs_json["radicado"])
            return True, "base_de_datos"
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning("Intento %d/%d fallido: %s. Reintentando en %ds...", attempt + 1, MAX_RETRIES, exc, wait)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(wait)
    raise RuntimeError("No fue posible guardar la PQRS en PostgreSQL.")


def build_llm() -> ChatOllama:
    model_name = os.getenv("OLLAMA_MODEL", "gpt-oss:20b").strip()
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com").strip()
    return ChatOllama(
        model=model_name,
        temperature=0.4,
        base_url=base_url,
        client_kwargs={"headers": {"Authorization": f"Bearer {api_key}"}},
    )


def build_chain(llm: ChatOllama):
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "Eres PQRSAI, un asistente para recibir y canalizar solicitudes PQRSD "
            "(peticiones, quejas, reclamos, sugerencias, felicitaciones e inquietudes). "
            "Siempre responde en espanol. "
            "Cuando el usuario exprese inconformidad o una experiencia negativa, reconoce "
            "su sentir con empatia antes de confirmar que registraras la solicitud. "
            "En cada respuesta saluda brevemente, presentate como PQRSAI e indica que "
            "envieras la solicitud al area competente. "
            "Si el mensaje esta fuera de alcance responde exactamente: "
            "'No puedo resolver estas solicitudes. Solo puedo ayudarte a registrar y canalizar PQRSAI.'",
        ),
        ("human", "{user_message}"),
    ])
    return prompt | llm | StrOutputParser()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola! Soy PQRSAI\n"
        "Estoy aqui para recibir tu solicitud y enviarla al area competente.\n\n"
        "Puedes radicar peticiones, quejas, reclamos, sugerencias o felicitaciones.\n"
        "No necesitas usar terminos formales, cuentame con tus propias palabras."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    llm = context.application.bot_data["llm"]
    chain = context.application.bot_data["chain"]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    classification = await classify_message(user_text, llm)

    if classification.get("fuera_de_alcance"):
        await update.message.reply_text(OUT_OF_SCOPE_RESPONSE)
        return

    if classification.get("es_saludo"):
        try:
            response = await chain.ainvoke({"user_message": user_text})
            await update.message.reply_text(response)
        except Exception as exc:
            logger.exception("Error al responder saludo: %s", exc)
            await update.message.reply_text("Hola! Soy PQRSAI. En que puedo ayudarte?")
        return

    if classification.get("es_pqrs"):
        if len(user_text) < 12:
             await update.message.reply_text(
                 "Tu mensaje parece ser muy corto para procesar una solicitud. "
                 "¿Podrías darnos más detalles sobre tu petición o queja?"
             )
             return
        pqrs_json = build_pqrs_json(update, user_text)
        radicado = pqrs_json["radicado"]
        context.application.bot_data["last_pqrs"] = pqrs_json
        try:
            saved_in_db, location = await persist_pqrs(pqrs_json)
        except Exception as exc:
            logger.exception("Error al persistir PQRS %s: %s", radicado, exc)
            await update.message.reply_text("No fue posible registrar tu solicitud en este momento. Intenta de nuevo.")
            return
        logger.info("PQRS %s persistida. BD=%s ubicacion=%s", radicado, saved_in_db, location)
        await update.message.reply_text(
            f"Tu solicitud quedo registrada.\nRadicado: #{radicado}"
        )
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response = await chain.ainvoke({"user_message": user_text})
        await update.message.reply_text(response)
    except Exception as exc:
        logger.exception("Error al invocar el modelo: %s", exc)
        await update.message.reply_text("Hubo un problema. Intenta de nuevo.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Error inesperado:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Ocurrio un error inesperado. Intenta de nuevo.")


def main():
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    ollama_api_key = os.getenv("OLLAMA_API_KEY", "").strip()

    if not telegram_token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN en variables de entorno.")
    if not ollama_api_key:
        raise ValueError("Falta OLLAMA_API_KEY en variables de entorno.")

    llm = build_llm()
    app = Application.builder().token(telegram_token).build()
    app.bot_data["llm"] = llm
    app.bot_data["chain"] = build_chain(llm)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    logger.info("Bot PQRSAI iniciado. Presiona Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
