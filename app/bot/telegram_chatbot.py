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
    CallbackContext,
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
    "careverga",
)

CANCEL_KEYWORDS = (
    "no", "cancelar", "cancela", "nada", "olvídelo", "olvídalo",
    "olvidalo", "no quiero", "ya no", "mejor no", "nop",
)

CONFIRM_KEYWORDS = (
    "sí", "si", "confirmar", "confirmo", "acepto", "aceptar",
    "enviar", "envío", "envio", "dale", "ok", "de acuerdo",
)

DONE_KEYWORDS = (
    "listo", "eso es todo", "ya", "terminé", "termine", "completo",
    "nada más", "nada mas", "no mas", "con eso", "creo que si",
    "eso sería todo", "eso seria todo", "creo que ya",
)

NEGATIVE_SENTIMENT_KEYWORDS = (
    "malo", "mala", "horrible", "terrible", "pésimo", "pesimo",
    "deficiente", "negligencia", "negligente", "inaceptable",
    "indignante", "vergüenza", "verguenza", "injusto", "injusta",
    "abuso", "abusivo", "abusiva", "atropello", "desatención",
    "desatencion", "demora", "retardo", "espera", "esperando",
    "estuve esperando", "me dejaron", "no me atendieron",
    "no me atendio", "no resolvieron", "sin solución", "sin solucion",
    "frustración", "frustracion", "molestia", "molesto", "molesta",
    "enojado", "enojada", "furioso", "furiosa", "hartado", "hartada",
)

FRIENDLY_REJECTION = (
    "Lo siento, solo puedo ayudarte a registrar solicitudes, quejas, reclamos "
    "o sugerencias relacionadas con servicios públicos.\n\n"
    "¿Tienes alguna petición que pueda ayudarte a tramitar? 😊"
)

PQRS_QUESTIONS = {
    "queja": [
        "¿Qué servicio público quieres quejarte?",
        "¿En qué municipio o localidad ocurrió?",
        "¿Podrías detallar qué sucedió exactamente?",
    ],
    "peticion": [
        "¿Qué necesitas específicamente?",
        "¿Para cuándo lo necesitas?",
        "¿A nombre de quién va la solicitud?",
    ],
    "reclamo": [
        "¿Qué producto o servicio estás reclamando?",
        "¿Cuál fue el problema?",
        "¿Qué solución esperas obtener?",
    ],
    "sugerencia": [
        "¿Cuál es tu idea o sugerencia?",
        "¿Cómo mejoraría el servicio?",
        "¿En qué área aplicaría?",
    ],
}

PQRS_TYPE_LABELS = {
    "queja": "Queja",
    "peticion": "Petición",
    "reclamo": "Reclamo",
    "sugerencia": "Sugerencia",
}

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
    "Eres Alexa, asistente de la secretaría del estado colombiano.\n"
    "Analiza el mensaje del usuario y determina si:\n\n"
    "1. Quiere hacer una solicitud, queja, reclamo, sugerencia o felicitación "
    "relacionada con servicios públicos, trámites o atención institucional.\n"
    "2. Es un saludo o pregunta sobre ti (el bot).\n"
    "3. Es un tema ajeno a servicios del gobierno (hora, fecha, chistes, etc.)\n\n"
    "Responde SOLO con un JSON:\n"
    "- {{\"tipo\": \"pqrs\"}} si es una solicitud ciudadana\n"
    "- {{\"tipo\": \"saludo\"}} si es saludo o pregunta sobre el bot\n"
    "- {{\"tipo\": \"otro\"}} si es tema ajeno\n\n"
    "Mensaje: {texto}"
)

GUIDANCE_PROMPT = (
    "Eres Alexa, la asistente de la secretaría del estado colombiano.\n\n"
    "El usuario te acaba de contar un problema o solicitud relacionada con servicios públicos.\n"
    "Tu tarea es generar 2-3 preguntas breves para obtener más detalles y poder registrar la solicitud.\n\n"
    "Reglas:\n"
    "- Solo incluye preguntas que NO estén ya respondidas en el mensaje del usuario.\n"
    "- Sé específica y directa.\n"
    "- Usa un tono amable y cercano.\n"
    "- NO incluyas saludos ni despedidas.\n"
    "- Responde SOLO con las preguntas, una por línea, sin numeración ni viñetas.\n"
    "- Ejemplo de respuesta válida:\n"
    "  ¿Qué sucedió exactamente?\n"
    "  ¿Cuándo ocurrió?\n"
    "  ¿En qué sede o lugar fue?\n\n"
    "Mensaje del usuario: {user_message}"
)

memory_store = PQRSMemoryStore()


def escape_text(text: str) -> str:
    chars = r"_*[]()~`>#+-=|{}.!"
    result = text
    for char in chars:
        result = result.replace(char, f"\\{char}")
    return result


def check_offensive_language(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return any(keyword in normalized for keyword in IRRESPECTFUL_KEYWORDS)


def detect_negative_sentiment(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return any(keyword in normalized for keyword in NEGATIVE_SENTIMENT_KEYWORDS)


def _is_greeting(text: str) -> bool:
    normalized = text.lower().strip()
    return any(normalized.startswith(kw) for kw in GREETING_KEYWORDS)


def _is_cancel(text: str) -> bool:
    normalized = text.lower().strip()
    return normalized in CANCEL_KEYWORDS or any(normalized.startswith(kw) for kw in CANCEL_KEYWORDS)


def _is_confirm(text: str) -> bool:
    normalized = text.lower().strip()
    return normalized in CONFIRM_KEYWORDS or any(normalized.startswith(kw) for kw in CONFIRM_KEYWORDS)


def _is_done(text: str) -> bool:
    normalized = text.lower().strip()
    return normalized in DONE_KEYWORDS or any(normalized.startswith(kw) for kw in DONE_KEYWORDS)


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


def _extract_details_from_text(text: str, pending_questions: list[str]) -> dict[str, str]:
    details = {}
    normalized = text.lower().strip()

    if any(kw in normalized for kw in ("qué pasó", "que pasó", "que paso", "qué pasó", "qué sucedió", "que sucedio")):
        details["Qué pasó"] = text
    elif any(kw in normalized for kw in ("cuándo", "cuando", "qué día", "que dia", "fecha")):
        details["Cuándo"] = text
    elif any(kw in normalized for kw in ("dónde", "donde", "en qué", "en que", "lugar", "sede", "ubicación")):
        details["Dónde"] = text
    else:
        if pending_questions:
            details[pending_questions[0]] = text
        else:
            details["Información adicional"] = text

    return details


def detect_pqrs_type(text: str) -> str:
    normalized = text.lower().strip()
    if any(kw in normalized for kw in ("queja", "quejarme", "inconformidad", "inconforme", "molesto", "enojado", "frustrado", "mal servicio", "mala atención", "pésimo", "horrible")):
        return "queja"
    if any(kw in normalized for kw in ("reclamo", "reclamar", "reclame", "cobro indebido", "me cobraron", "no me devolvieron")):
        return "reclamo"
    if any(kw in normalized for kw in ("sugerencia", "sugerir", "propongo", "propuesta", "recomendación", "deberían", "podrían mejorar")):
        return "sugerencia"
    if any(kw in normalized for kw in ("petición", "peticion", "solicito", "solicitar", "necesito", "requiero", "pido", "quiero pedir")):
        return "peticion"
    return "peticion"


def is_valid_answer(text: str) -> bool:
    if _is_greeting(text):
        return False
    if _is_done(text) or _is_cancel(text) or _is_confirm(text):
        return False
    if _looks_out_of_scope_question(text) and not _looks_like_pqrs(text):
        return False
    words = text.strip().split()
    if len(words) < 2:
        return False
    alpha_chars = [c for c in text if c.isalpha()]
    if len(alpha_chars) < 4:
        return False
    return True


def get_questions_for_type(pqrs_type: str) -> list[str]:
    return list(PQRS_QUESTIONS.get(pqrs_type, PQRS_QUESTIONS["peticion"]))


async def classify_message(text: str, llm: ChatOllama) -> dict:
    if _is_greeting(text):
        return {"tipo": "saludo"}
    if _looks_out_of_scope_question(text) and not _looks_like_pqrs(text):
        return {"tipo": "otro"}
    try:
        prompt = ChatPromptTemplate.from_messages([("human", CLASSIFIER_PROMPT)])
        chain = prompt | llm | StrOutputParser()
        raw = await chain.ainvoke({"texto": text})
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = json.loads(clean)
        if "tipo" not in result:
            raise ValueError("Clave 'tipo' faltante")
        if result.get("tipo") == "otro" and _looks_like_pqrs(text):
            return {"tipo": "pqrs"}
        if result.get("tipo") == "pqrs" and _looks_out_of_scope_question(text) and not _looks_like_pqrs(text):
            return {"tipo": "otro"}
        return result
    except Exception as exc:
        logger.warning("Clasificador fallo, usando heuristica local: %s", exc)
        if _looks_like_pqrs(text):
            return {"tipo": "pqrs"}
        return {"tipo": "otro"}


async def generate_guidance_questions(text: str, llm: ChatOllama) -> list[str]:
    try:
        prompt = ChatPromptTemplate.from_messages([("human", GUIDANCE_PROMPT)])
        chain = prompt | llm | StrOutputParser()
        raw = await chain.ainvoke({"user_message": text})
        questions = [q.strip() for q in raw.strip().split("\n") if q.strip() and "?" in q]
        return questions[:3]
    except Exception as exc:
        logger.warning("Error generando preguntas de guía: %s", exc)
        return [
            "¿Qué sucedió exactamente?",
            "¿Cuándo ocurrió?",
            "¿En qué sede o lugar fue?",
        ]


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


def build_cancel_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📝 Escribir nueva", callback_data="new_pqrs"),
            InlineKeyboardButton("✏️ Continuar con esta", callback_data="continue_pqrs"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_done_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("✅ Listo, enviar solicitud", callback_data="finish_details"),
            InlineKeyboardButton("➕ Agregar más detalles", callback_data="add_more"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_validation_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🔄 Reintentar", callback_data="retry_answer"),
            InlineKeyboardButton("⏭️ Saltar pregunta", callback_data="skip_question"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_confirmation_message(draft_text: str, is_update: bool = False) -> str:
    prefix = "📝 *Tu solicitud actualizada:*" if is_update else "📝 *Tu solicitud:*"
    return (
        f"{prefix}\n\n"
        f"_{draft_text}_\n\n"
        f"¿Deseas confirmar el envío?"
    )


def build_empathy_message(text: str) -> str:
    normalized = " ".join(text.lower().strip().split())
    if any(kw in normalized for kw in ("horrible", "terrible", "pésimo", "pesimo", "inaceptable")):
        return "Lamento mucho que hayas tenido esa experiencia 😔. Tu molestia es importante y vamos a atenderla."
    if any(kw in normalized for kw in ("malo", "mala", "deficiente", "negligencia")):
        return "Entiendo tu frustración, eso no es lo que mereces 😔. Vamos a registrar tu solicitud para que sea atendida."
    if any(kw in normalized for kw in ("esperando", "espera", "demora", "retardo")):
        return "Siento que hayas tenido que esperar tanto tiempo 😔. Tu tiempo es valioso y vamos a atender tu solicitud."
    if any(kw in normalized for kw in ("no me atendieron", "no me atendio", "no resolvieron")):
        return "Espero que la entidad pueda resolver tu situación pronto 😔. Vamos a registrar tu solicitud."
    return "Gracias por contarme tu experiencia 😔. Voy a revisar tu solicitud."


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
            "Eres Alexa, la asistente virtual de la secretaria del estado colombiano.\n\n"
            "Tu personalidad:\n"
            "- Eres cercana, amigable y empatica.\n"
            "- Te presentas como 'Alexa' y usas un tono amable y profesional.\n"
            "- Cuando el usuario exprese una queja o experiencia negativa, primero reconoces "
            "su sentir con empatia antes de ofrecer ayuda.\n"
            "- Guias al usuario paso a paso para registrar su solicitud.\n"
            "- Usas emojis con moderacion para ser mas cercana.\n"
            "- Respondes en espanol colombiano.\n\n"
            "Tu funcion:\n"
            "- Recibir y canalizar solicitudes PQRSD (peticiones, quejas, reclamos, sugerencias, felicitaciones).\n"
            "- Cuando el usuario envie una solicitud, confirma que la recibiras y la enviaras al area competente.\n"
            "- Si el usuario esta enojado o frustrado, valida su sentir con empatia.",
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


async def cleanup_draft(context: CallbackContext) -> None:
    chat_id = context.job.data
    draft = memory_store.get(chat_id)
    if draft is None or draft.status not in ("collecting_details", "pending_confirmation"):
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
        "¡Hola! Soy Alexa 👋, tu asistente de la secretaría.\n\n"
        "Estoy aquí para recibir tu solicitud y enviarla al área competente.\n\n"
        "Puedes contarme peticiones, quejas, reclamos, sugerencias o felicitaciones.\n"
        "No necesitas ser formal, cuéntame con tus propias palabras 😊"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    llm = context.application.bot_data["llm"]
    chain = context.application.bot_data["chain"]

    draft = memory_store.get(chat_id)

    if draft is not None and draft.status == "collecting_details":
        if _is_greeting(user_text):
            memory_store.clear(chat_id)
            try:
                response = await chain.ainvoke({"user_message": user_text})
                await update.message.reply_text(response)
            except Exception:
                await update.message.reply_text("¡Hola! Soy Alexa. ¿En qué puedo ayudarte?")
            return

        if _is_cancel(user_text):
            keyboard = build_cancel_keyboard()
            await update.message.reply_text(
                "¿Qué deseas hacer?",
                reply_markup=keyboard,
            )
            return

        if not draft.pending_questions:
            questions = get_questions_for_type(draft.pqrs_type)
            draft.pending_questions = questions
            memory_store._drafts[chat_id] = draft

        current_question = draft.pending_questions[0]

        if not is_valid_answer(user_text):
            type_label = PQRS_TYPE_LABELS.get(draft.pqrs_type, "Solicitud")
            await update.message.reply_text(
                f"🤔 Esa respuesta no parece ser la información que necesito para tu {type_label.lower()}.\n\n"
                f"¿Podrías responder la pregunta que te hice?\n\n"
                f"👉 *{current_question}*",
                parse_mode="Markdown",
                reply_markup=build_validation_keyboard(),
            )
            return

        draft.collected_details[current_question] = user_text.strip()
        draft.pending_questions = draft.pending_questions[1:]
        memory_store._drafts[chat_id] = draft

        if draft.pending_questions:
            next_q = draft.pending_questions[0]
            progress = f"({len(draft.collected_details)}/{len(draft.collected_details) + len(draft.pending_questions)})"
            await update.message.reply_text(
                f"Perfecto, gracias 😊 {progress}\n\n"
                f"👉 *{next_q}*",
                parse_mode="Markdown",
                reply_markup=build_done_keyboard(),
            )
        else:
            await update.message.reply_text(
                "¡Excelente! Tengo toda la información necesaria 🎉\n\n"
                "Revisemos tu solicitud antes de enviarla.",
                reply_markup=build_done_keyboard(),
            )
        return

    if draft is not None and draft.status == "pending_confirmation":
        if _is_greeting(user_text):
            memory_store.clear(chat_id)
            try:
                response = await chain.ainvoke({"user_message": user_text})
                await update.message.reply_text(response)
            except Exception:
                await update.message.reply_text("¡Hola! Soy Alexa. ¿En qué puedo ayudarte?")
            return

        if _is_cancel(user_text):
            keyboard = build_cancel_keyboard()
            await update.message.reply_text(
                "¿Qué deseas hacer?",
                reply_markup=keyboard,
            )
            return

        if _is_confirm(user_text):
            pqrs_json = build_pqrs_json(update, draft.get_full_text())
            radicado = pqrs_json["radicado"]
            memory_store.clear(chat_id)
            try:
                await persist_pqrs(pqrs_json)
                await update.message.reply_text(
                    f"✅ Tu solicitud quedó registrada.\n\n*Radicado:* #{radicado}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.exception("Error al persistir PQRS %s: %s", radicado, exc)
                await update.message.reply_text(
                    "No fue posible registrar tu solicitud en este momento. "
                    "Intenta de nuevo o contacta a soporte."
                )
            return

        updated = memory_store.update_text(chat_id, user_text)
        if updated is None:
            memory_store.clear(chat_id)
            await update.message.reply_text("Tu borrador quedó vacío. Envíame tu solicitud cuando quieras.")
            return

        new_irrespetuosa = check_offensive_language(user_text)
        updated.irrespetuosa = new_irrespetuosa
        memory_store._drafts[chat_id] = updated

        msg = build_confirmation_message(updated.get_full_text(), is_update=True)
        if new_irrespetuosa:
            msg += "\n\n⚠️ Tu solicitud contiene lenguaje que podría considerarse ofensivo. Te pedimos amablemente reformular."
        await update.message.reply_text(msg, reply_markup=build_confirmation_keyboard())
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    classification = await classify_message(user_text, llm)

    tipo = classification.get("tipo", "otro")

    if tipo == "otro":
        await update.message.reply_text(FRIENDLY_REJECTION)
        return

    if tipo == "saludo":
        try:
            response = await chain.ainvoke({"user_message": user_text})
            await update.message.reply_text(response)
        except Exception as exc:
            logger.exception("Error al responder saludo: %s", exc)
            await update.message.reply_text("¡Hola! Soy Alexa 👋 ¿En qué puedo ayudarte?")
        return

    if tipo == "pqrs":
        if len(user_text) < 12:
            await update.message.reply_text(
                "Tu mensaje parece ser muy corto para procesar una solicitud. "
                "¿Podrías contarme con más detalles lo que necesitas?"
            )
            return

        if detect_negative_sentiment(user_text):
            empathy = build_empathy_message(user_text)
            await update.message.reply_text(empathy)
            await asyncio.sleep(1)

        is_offensive = check_offensive_language(user_text)

        pqrs_type = detect_pqrs_type(user_text)
        questions = get_questions_for_type(pqrs_type)

        draft = memory_store.set(
            chat_id,
            user_text,
            status="collecting_details",
            irrespetuosa=is_offensive,
            pending_questions=questions,
            pqrs_type=pqrs_type,
        )

        type_label = PQRS_TYPE_LABELS.get(pqrs_type, "Solicitud")
        first_q = questions[0]
        await update.message.reply_text(
            f"He recibido tu {type_label.lower()} 📝\n\n"
            f"Para registrarla bien, ayúdame con algunos datos:\n\n"
            f"👉 *{first_q}*",
            parse_mode="Markdown",
            reply_markup=build_done_keyboard(),
        )

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

    data = query.data

    if data == "new_pqrs":
        memory_store.clear(chat_id)
        await query.edit_message_text(
            "Perfecto. Envíame tu nueva solicitud cuando quieras 😊"
        )
        return

    if data == "continue_pqrs":
        if draft is not None and draft.status in ("collecting_details", "pending_confirmation"):
            draft.status = "pending_confirmation"
            memory_store._drafts[chat_id] = draft
            msg = build_confirmation_message(draft.get_full_text())
            if draft.irrespetuosa:
                msg += "\n\n⚠️ Tu solicitud contiene lenguaje que podría considerarse ofensivo. Te pedimos amablemente reformular."
            await query.edit_message_text(msg, reply_markup=build_confirmation_keyboard())
        else:
            await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    if data == "finish_details":
        if draft is not None and draft.status == "collecting_details":
            draft.status = "pending_confirmation"
            memory_store._drafts[chat_id] = draft
            msg = build_confirmation_message(draft.get_full_text())
            if draft.irrespetuosa:
                msg += "\n\n⚠️ Tu solicitud contiene lenguaje que podría considerarse ofensivo. Te pedimos amablemente reformular."
            await query.edit_message_text(msg, reply_markup=build_confirmation_keyboard())
        else:
            await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    if data == "add_more":
        if draft is not None and draft.status == "collecting_details":
            unanswered = [q for q in get_questions_for_type(draft.pqrs_type) if q not in draft.collected_details]
            if not unanswered:
                await query.edit_message_text(
                    "Ya has respondido todas las preguntas 😊\n"
                    "¿Quieres confirmar el envío de tu solicitud?",
                    reply_markup=build_done_keyboard(),
                )
                return
            next_q = unanswered[0]
            draft.pending_questions = unanswered
            memory_store._drafts[chat_id] = draft
            await query.edit_message_text(
                f"Perfecto, agreguemos más detalles 😊\n\n"
                f"👉 *{next_q}*",
                parse_mode="Markdown",
                reply_markup=build_done_keyboard(),
            )
        else:
            await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    if data == "retry_answer":
        if draft is not None and draft.status == "collecting_details" and draft.pending_questions:
            current_question = draft.pending_questions[0]
            type_label = PQRS_TYPE_LABELS.get(draft.pqrs_type, "Solicitud")
            await query.edit_message_text(
                f"👍 Vamos de nuevo con la {type_label.lower()}.\n\n"
                f"👉 *{current_question}*",
                parse_mode="Markdown",
                reply_markup=build_validation_keyboard(),
            )
        else:
            await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    if data == "skip_question":
        if draft is not None and draft.status == "collecting_details" and draft.pending_questions:
            current_question = draft.pending_questions[0]
            draft.collected_details[current_question] = "(no respondido)"
            draft.pending_questions = draft.pending_questions[1:]
            memory_store._drafts[chat_id] = draft
            if draft.pending_questions:
                next_q = draft.pending_questions[0]
                await query.edit_message_text(
                    f"Ok, saltamos esa pregunta 👍\n\n"
                    f"👉 *{next_q}*",
                    parse_mode="Markdown",
                    reply_markup=build_done_keyboard(),
                )
            else:
                await query.edit_message_text(
                    "Hemos terminado con las preguntas 🎉\n"
                    "Revisemos tu solicitud antes de enviarla.",
                    reply_markup=build_done_keyboard(),
                )
        else:
            await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    if draft is None or draft.status != "pending_confirmation":
        await query.edit_message_text("Tu solicitud ya no está activa. Envíame una nueva cuando quieras.")
        return

    if data == "confirm":
        pqrs_json = build_pqrs_json(update, draft.get_full_text())
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
        if draft is not None:
            questions = get_questions_for_type(draft.pqrs_type)
            draft.collected_details = {}
            draft.pending_questions = list(questions)
            draft.status = "collecting_details"
            memory_store._drafts[chat_id] = draft
        await query.edit_message_text(
            "Perfecto, vamos a empezar de cero 🔄\n\n"
            "Tu solicitud y detalles anteriores fueron eliminados.\n\n"
            f"👉 *{questions[0]}*",
            parse_mode="Markdown",
            reply_markup=build_done_keyboard(),
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
