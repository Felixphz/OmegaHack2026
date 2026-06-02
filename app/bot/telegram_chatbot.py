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
from app.bot.pqrs_memory import PQRSMemoryStore
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

IRRESPECTFUL_KEYWORDS = (
    "idiota", "estupido", "estúpido", "imbecil", "imbécil", "malparido",
    "hp", "hpta", "gonorrea", "perra", "mierda", "hijueputa",
    "puto", "puta", "cabron", "cabrón", "carajo", "maldito",
    "basura", "inutil", "inútil", "desgraciado", "desgraciada",
    "gonorrea", "careverga", "gonorrea", "hp", "hijueputa",
)

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
DRAFT_TIMEOUT_SECONDS = 600

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
    "'cuantos dias tiene un ano' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": false, \"fuera_de_alcance\": true}}\n"
    "'hola' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": true, \"fuera_de_alcance\": false}}\n"
    "'quien es el alcalde' -> "
    "{{\"es_pqrs\": false, \"es_saludo\": false, \"fuera_de_alcance\": true}}\n\n"
    "Responde UNICAMENTE con el JSON. Sin texto adicional.\n\n"
    "Mensaje del usuario: {texto}"
)


memory_store = PQRSMemoryStore()


def check_offensive_language(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return any(keyword in normalized for keyword in IRRESPECTFUL_KEYWORDS)


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


def build_confirmation_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="confirm"),
            InlineKeyboardButton("✏️ Editar", callback_data="edit"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_confirmation_message(draft_text: str, is_update: bool = False) -> str:
    prefix = "📝 *Tu solicitud actualizada:*" if is_update else "📝 *Tu solicitud:*"
    warning = "\n\n⚠️ Tu solicitud contiene lenguaje que podría considerarse ofensivo. Te pedimos amablemente reformular."
    return (
        f"{prefix}\n\n"
        f"_{draft_text}_\n\n"
        f"¿Deseas confirmar el envío?"
    )


def build_llm() -> ChatOllama:
    model_name = os.getenv("OLLAMA_MODEL", "gemma4:31b").strip()
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
            "Eres Alexa, un asistente conversacional para recibir y canalizar solicitudes PQRSD "
            "(peticiones, quejas, reclamos, sugerencias, felicitaciones e inquietudes) "
            "para una secretaria del estado colombiano.\n\n"
            "Siempre responde en espanol, de forma amable y empatica.\n"
            "Cuando el usuario exprese inconformidad o una experiencia negativa, reconoce "
            "su sentir con empatia antes de confirmar que registraras la solicitud.\n"
            "Presentate como Alexa, asistente de la secretaria.\n"
            "Si el mensaje esta fuera de alcance responde exactamente: "
            "'No puedo resolver estas solicitudes. Solo puedo ayudarte a registrar y canalizar PQRSAI.'",
        ),
        ("human", "{user_message}"),
    ])
    return prompt | llm | StrOutputParser()


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


async def cleanup_draft(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    draft = memory_store.get(chat_id)
    if draft is None or draft.status != "pending_confirmation":
        return
    memory_store.clear(chat_id)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏰ Tu borrador ha expirado por inactividad.\n"
                 "Si deseas registrar una nueva solicitud, envíamela cuando quieras.",
        )
    except Exception as exc:
        logger.warning("No se pudo enviar notificación de timeout a %s: %s", chat_id, exc)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    memory_store.clear(chat_id)
    await update.message.reply_text(
        "¡Hola! Soy Alexa, asistente de la secretaría.\n"
        "Estoy aquí para recibir tu solicitud y enviarla al área competente.\n\n"
        "Puedes radicar peticiones, quejas, reclamos, sugerencias o felicitaciones.\n"
        "No necesitas usar términos formales, cuéntame con tus propias palabras."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    llm = context.application.bot_data["llm"]
    chain = context.application.bot_data["chain"]

    draft = memory_store.get(chat_id)

    if draft is not None and draft.status == "pending_confirmation":
        if _is_greeting(user_text):
            memory_store.clear(chat_id)
            try:
                response = await chain.ainvoke({"user_message": user_text})
                await update.message.reply_text(response)
            except Exception:
                await update.message.reply_text("¡Hola! Soy Alexa. En qué puedo ayudarte?")
            return

        updated = memory_store.update_text(chat_id, user_text)
        if updated is None:
            memory_store.clear(chat_id)
            await update.message.reply_text("Tu borrador quedó vacío. Envíame tu solicitud cuando quieras.")
            return

        new_irrespetuosa = check_offensive_language(user_text)
        updated.irrespetuosa = new_irrespetuosa
        memory_store._drafts[chat_id] = updated

        msg = build_confirmation_message(updated.text, is_update=True)
        if new_irrespetuosa:
            msg += "\n\n⚠️ Tu solicitud contiene lenguaje que podría considerarse ofensivo. Te pedimos amablemente reformular."
        await update.message.reply_text(msg, reply_markup=build_confirmation_keyboard(), parse_mode="Markdown")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
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
            await update.message.reply_text("¡Hola! Soy Alexa. En qué puedo ayudarte?")
        return

    if classification.get("es_pqrs"):
        if len(user_text) < 12:
            await update.message.reply_text(
                "Tu mensaje parece ser muy corto para procesar una solicitud. "
                "¿Podrías darnos más detalles sobre tu petición o queja?"
            )
            return

        is_offensive = check_offensive_language(user_text)
        draft = memory_store.set(
            chat_id,
            user_text,
            status="pending_confirmation",
            irrespetuosa=is_offensive,
        )

        msg = build_confirmation_message(draft.text)
        if is_offensive:
            msg += "\n\n⚠️ Tu solicitud contiene lenguaje que podría considerarse ofensivo. Te pedimos amablemente reformular."
        await update.message.reply_text(msg, reply_markup=build_confirmation_keyboard(), parse_mode="Markdown")

        try:
            job = context.job_queue.run_once(
                cleanup_draft,
                when=DRAFT_TIMEOUT_SECONDS,
                data=chat_id,
                chat_id=chat_id,
                name=f"draft_timeout_{chat_id}",
            )
            draft.timeout_task = job
            memory_store._drafts[chat_id] = draft
        except Exception as exc:
            logger.warning("No se pudo programar timeout para borrador %s: %s", chat_id, exc)
        return

    try:
        response = await chain.ainvoke({"user_message": user_text})
        await update.message.reply_text(response)
    except Exception as exc:
        logger.exception("Error al invocar el modelo: %s", exc)
        await update.message.reply_text("Hubo un problema. Intenta de nuevo.")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query: CallbackQuery = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    draft = memory_store.get(chat_id)

    if draft is None or draft.status != "pending_confirmation":
        await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    data = query.data

    if data == "confirm":
        pqrs_json = build_pqrs_json(update, draft.text)
        radicado = pqrs_json["radicado"]

        memory_store.clear(chat_id)

        try:
            await persist_pqrs(pqrs_json)
            await query.edit_message_text(
                f"✅ Tu solicitud quedó registrada.\n\n*Radicado:* #{radicado}",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.exception("Error al persistir PQRS %s: %s", radicado, exc)
            await query.edit_message_text(
                "No fue posible registrar tu solicitud en este momento. "
                "Intenta de nuevo o contacta a soporte."
            )
        return

    if data == "edit":
        await query.edit_message_text(
            "Por favor, escribe tu solicitud corregida:"
        )
        return

    await query.edit_message_text("Opción no reconocida. Envíame una nueva solicitud cuando quieras.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Error inesperado:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Ocurrió un error inesperado. Intenta de nuevo.")


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
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    logger.info("Bot Alexa iniciado. Presiona Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
