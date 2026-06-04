import asyncio
import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
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

GREETING_KEYWORDS = (
    "hola", "buenas", "buen dia", "buenos dias",
    "buenas tardes", "buenas noches",
)

CONFIRM_KEYWORDS = (
    "sí", "si", "confirmar", "confirmo", "acepto", "aceptar",
    "dale", "ok", "de acuerdo",
)

DONE_KEYWORDS = (
    "listo", "eso es todo", "ya", "terminé", "termine",
    "completo", "nada más", "nada mas", "no mas", "con eso",
    "creo que si", "eso sería todo", "eso seria todo", "creo que ya",
)

QUESTION_PREFIXES = (
    "que ", "qué ", "quien ", "quién ", "como ", "cómo ",
    "cuando ", "cuándo ", "donde ", "dónde ", "cual ", "cuál ",
    "cuanto ", "cuánto ", "por que ", "por qué ",
)

GENERAL_QUESTION_PATTERNS = (
    "que dia es hoy", "qué dia es hoy", "qué día es hoy", "que día es hoy",
    "que hora es", "qué hora es", "fecha de hoy", "dia de la semana",
    "cuanto es", "cuánto es", "capital de", "quien es", "quién es",
    "quien fue", "quién fue", "como estas", "cómo estas",
    "cómo estás", "como estás", "que puedes hacer", "qué puedes hacer",
    "que sabes", "qué sabes", "quien te creo", "quién te creó",
)

PQRS_INTENT_KEYWORDS = (
    "pqrs", "peticion", "petición", "queja", "reclamo", "sugerencia",
    "felicitacion", "felicitación", "tramite", "trámite", "servicio",
    "atencion", "atención", "entidad", "alcaldia", "alcaldía", "secretaria",
    "secretaría", "impuesto", "subsidio", "permiso", "licencia", "factura",
    "cobro", "pago", "agua", "luz", "gas", "basura", "transporte",
    "hospital", "salud", "educacion", "educación", "espacio publico",
    "espacio público", "problema", "solicito", "solicitar", "ayuda",
    "urgente", "denuncia", "denunciar", "malo", "mala", "tardan", "demora", "espera",
    "esperando", "inconformidad", "frustracion", "frustración",
    "no me", "no resolv", "no atend", "me cobran", "me cobraron",
    "quejarme", "reclamar", "reclame", "reclamo", "sugerir", "propongo",
    "sugerencia", "necesito", "requiero", "pido", "quiero pedir",
    "abuso", "agresion", "agresión", "violencia", "maltrato", "acosar", "acoso",
    "policia", "policía", "autoridad", "funcionario", "uniformado",
    "menor", "menores", "adulto mayor", "anciano", "anciana",
    "robo", "hurto", "atraco", "asalto", "asaltaron", "robaron", "atracaron",
    "discriminacion", "discriminación", "racismo", "xenofobia",
    "corrupcion", "corrupción", "soborno", "cohecho",
    "extorsion", "extorsión", "amenaza", "amenazas", "intimidacion", "intimidación",
    "inseguridad", "peligro", "peligroso", "delincuencia", "delito", "delitos",
    "sancion", "sanción", "castigo", "carcel", "cárcel", "juicio", "juzgado",
    "asustado", "asustada", "temor", "miedo",
    "contaminacion", "contaminación", "ruido", "musica", "volumen",
    "perro", "mascota", "animal", "vecino", "vecina",
    "via", "vía", "calle", "carrera", "avenida", "carretera", "anden", "andén", "puente",
    "semaforo", "semáforo", "parque", "plaza", "barrio", "vereda", "corregimiento",
    "inundacion", "inundación", "deslave", "derrumbe", "hueco", "huecos",
    "alumbrado", "luminaria", "poste", "transformador",
    "escuela", "colegio", "universidad", "docente", "profesor",
    "accidente", "choque", "atropello",
    "citacion", "citación", "comparendo", "multa", "infraccion", "infracción",
    "reportar", "reporto", "delatar",
)

PQRS_CONTEXT_KEYWORDS = (
    "menor", "menores", "anciano", "anciana", "adulto mayor",
    "calle", "barrio", "via", "vía", "avenida", "carrera", "parque", "plaza",
    "hueco", "huecos", "semaforo", "semáforo", "alumbrado", "poste",
    "perro", "mascota", "animal", "vecino", "vecina",
    "robo", "hurto", "atraco", "asalto",
    "policia", "policía", "funcionario", "uniformado",
    "alcaldia", "alcaldía", "secretaria", "secretaría", "hospital", "colegio", "escuela",
    "abuso", "agresion", "agresión", "violencia", "maltrato", "amenaza",
)

NEED_VERBS = (
    "necesito", "necesitamos", "solicito", "solicitar", "requiero",
    "pido", "quiero pedir", "me pueden", "me pueden ayudar",
    "ayuda", "ayudenme", "auxilio", "por favor",
    "quiero", "solicita", "pedir", "tengo que",
)

VALIDATION_PROMPT = (
    "Eres un validador de suficiencia para un sistema de PQRS de una "
    "Alcaldía colombiana. Tu único objetivo es determinar si el texto "
    "del ciudadano provee suficiente contexto informativo para entender "
    "QUÉ quiere, QUÉ le pasa o QUÉ reporta.\n\n"
    "Responde EXCLUSIVAMENTE con un JSON válido, sin texto adicional, "
    "sin markdown, sin explicaciones fuera del JSON:\n"
    '{{"valido": true, "razon": "..."}} '
    'o {{"valido": false, "razon": "..."}}\n\n'
    "El campo 'razon' debe ser MUY breve (máx 8 palabras) explicando "
    "por qué es válido o inválido.\n\n"
    "RECHAZA (valido=false) cuando:\n"
    "1. Saludos o cortesía aislada: 'hola', 'buenas tardes', 'buenos días'.\n"
    "2. Expresiones vagas sin objeto: 'tuve un problema', 'necesito ayuda', "
    "'tengo una duda', 'quiero poner algo' (sin especificar qué).\n"
    "3. Texto sin sentido, spam, emojis solos, caracteres repetidos.\n"
    "4. Preguntas generales no relacionadas: 'qué día es hoy', 'cómo estás'.\n"
    "5. Confusiones con el bot: 'qué puedes hacer', 'quién te creó'.\n\n"
    "ACEPTA (valido=true) cuando el texto mencione al menos UNO de:\n"
    "1. Acción concreta del ciudadano: 'quiero pedir', 'solicito', 'necesito [X]'.\n"
    "2. Hecho reportado: 'me cobraron de más', 'no me llegó el recibo', "
    "'me atendieron mal'.\n"
    "3. Servicio público o entidad: 'alcaldía', 'salud', 'agua', 'luz', "
    "'secretaría', 'trámite'.\n"
    "4. Lugar, dirección o sede: 'calle 10', 'barrio centro', 'sede norte'.\n"
    "5. Queja sobre funcionario o proceso: 'me exigieron', 'me negaron'.\n"
    "6. Solicitud de documento: 'certificado', 'licencia', 'permiso'.\n\n"
    "Texto a validar: {texto}\n\n"
    "JSON:"
)

STATE_AWAITING_ID_TYPE = "awaiting_id_type"
STATE_AWAITING_DOCUMENTO = "awaiting_documento"
STATE_AWAITING_NOMBRE = "awaiting_nombre"
STATE_AWAITING_EMAIL = "awaiting_email"
STATE_AWAITING_CASE = "awaiting_case"
STATE_AWAITING_OCR_VALIDATION = "awaiting_ocr_validation"
STATE_READY_TO_SEND = "ready_to_send"

ACTIVE_STATES = (
    STATE_AWAITING_ID_TYPE,
    STATE_AWAITING_DOCUMENTO,
    STATE_AWAITING_NOMBRE,
    STATE_AWAITING_EMAIL,
    STATE_AWAITING_CASE,
    STATE_AWAITING_OCR_VALIDATION,
    STATE_READY_TO_SEND,
)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
DRAFT_TIMEOUT_SECONDS = int(os.getenv("DRAFT_TIMEOUT_SECONDS", "600"))
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL") or os.getenv("OLLAMA_MODEL", "gemma4:31b").strip()
OLLAMA_VISION_TIMEOUT = int(os.getenv("OLLAMA_VISION_TIMEOUT", "60"))

VALIDATION_TIMEOUT = int(os.getenv("VALIDATION_TIMEOUT", "15"))
VALIDATION_TEMPERATURE = float(os.getenv("VALIDATION_TEMPERATURE", "0.1"))

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


def detect_pqrs_type(text: str) -> str:
    normalized = text.lower().strip()
    if any(kw in normalized for kw in ("queja", "quejarme", "inconformidad", "inconforme", "molesto", "enojado", "frustrado", "mal servicio", "mala atención", "pésimo", "horrible")):
        return "queja"
    if any(kw in normalized for kw in ("reclamo", "reclamar", "reclame", "cobro indebido", "me cobraron", "no me devolvieron")):
        return "reclamo"
    if any(kw in normalized for kw in ("sugerencia", "sugerir", "propongo", "propuesta", "recomendación", "deberían", "podrían mejorar")):
        return "sugerencia"
    return "peticion"


def is_valid_documento(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned.isdigit():
        return False
    return 6 <= len(cleaned) <= 15


def is_valid_email(text: str) -> bool:
    pattern = r"^[\w.+-]+@[\w-]+\.[\w.-]+$"
    return bool(re.match(pattern, text.strip()))


def is_valid_nombre(text: str) -> bool:
    words = text.strip().split()
    if len(words) < 2:
        return False
    for word in words:
        if len(word) < 2:
            return False
        if not all(c.isalpha() or c.isspace() for c in word):
            return False
    return True


def _is_greeting(text: str) -> bool:
    normalized = text.lower().strip()
    if not normalized:
        return False
    return any(normalized == kw or normalized.startswith(kw) for kw in GREETING_KEYWORDS)


def _is_confirm(text: str) -> bool:
    normalized = text.lower().strip()
    if not normalized:
        return False
    return normalized in CONFIRM_KEYWORDS


def _is_done(text: str) -> bool:
    normalized = text.lower().strip()
    if not normalized:
        return False
    return normalized in DONE_KEYWORDS or any(normalized.startswith(kw) for kw in DONE_KEYWORDS)


def _looks_out_of_scope_question(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    if any(pattern in normalized for pattern in GENERAL_QUESTION_PATTERNS):
        return True
    if "?" in normalized and any(normalized.startswith(prefix) for prefix in QUESTION_PREFIXES):
        return not any(keyword in normalized for keyword in PQRS_INTENT_KEYWORDS)
    return False


def _looks_like_pqrs(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    return any(keyword in normalized for keyword in PQRS_INTENT_KEYWORDS)


def is_valid_case(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _is_greeting(text):
        return False
    if _is_confirm(text) or _is_done(text):
        return False
    if _looks_out_of_scope_question(text):
        return False
    words = text.strip().split()
    if len(words) < 3:
        return False
    normalized = text.lower()
    if any(kw in normalized for kw in PQRS_INTENT_KEYWORDS):
        return True
    context_matches = sum(1 for kw in PQRS_CONTEXT_KEYWORDS if kw in normalized)
    if context_matches >= 2 and len(words) >= 4:
        return True
    if len(words) >= 5 and any(v in normalized for v in NEED_VERBS):
        return True
    return False


def _fallback_validation(text: str) -> tuple[bool, str]:
    """Si el LLM falla, usar is_valid_case() como fallback."""
    if is_valid_case(text):
        return True, "válido por palabras clave"
    return False, "no parece ser una solicitud, queja o reclamo"


async def validate_case_with_llm(text: str) -> tuple[bool, str]:
    """Retorna (valido, razon). Si falla, fallback a is_valid_case()."""
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com").strip()
    model = os.getenv("OLLAMA_MODEL", "gemma4:31b").strip()

    try:
        async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT) as client:
            response = await client.post(
                f"{base_url}/api/generate",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "prompt": VALIDATION_PROMPT.format(texto=text),
                    "stream": False,
                    "options": {"temperature": VALIDATION_TEMPERATURE},
                },
            )
            response.raise_for_status()
            data = response.json()
            raw = (data.get("response") or "").strip()

        match = re.search(r'\{[^{}]*"valido"[^{}]*\}', raw, re.DOTALL)
        if not match:
            logger.warning("LLM validation: no JSON in response, falling back. Raw: %s", raw[:200])
            return _fallback_validation(text)

        result = json.loads(match.group())
        return bool(result.get("valido")), str(result.get("razon", ""))

    except httpx.TimeoutException:
        logger.warning("LLM validation timeout (%ds), falling back to keywords", VALIDATION_TIMEOUT)
        return _fallback_validation(text)
    except httpx.HTTPError as exc:
        logger.warning("LLM validation HTTP error, falling back: %s", exc)
        return _fallback_validation(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM validation returned malformed JSON, falling back: %s", exc)
        return _fallback_validation(text)
    except Exception as exc:
        logger.exception("Unexpected error in LLM validation, falling back: %s", exc)
        return _fallback_validation(text)


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


def build_identification_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("👤 Radicar Identificado", callback_data="id_identificado"),
            InlineKeyboardButton("🕵️ Radicar Anónimo", callback_data="id_anonimo"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_identification_back_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("↩️ Empezar de nuevo", callback_data="restart_identification")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_send_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🚀 Enviar Solicitud", callback_data="send_request"),
            InlineKeyboardButton("✏️ Corregir", callback_data="edit_case"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_ocr_validation_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("👍 Sí, continuar", callback_data="ocr_yes"),
            InlineKeyboardButton("📝 No, volver a escribir", callback_data="ocr_no"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_invalid_case_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("↩️ Volver a escribir caso", callback_data="retry_case")],
        [InlineKeyboardButton("↩️ Empezar de nuevo", callback_data="restart_identification")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_case_preview(descripcion_caso: str) -> str:
    return (
        "📋 *Vista previa de tu caso:*\n\n"
        f"```\n{escape_text(descripcion_caso)}\n```\n\n"
        "¿Está correcto?"
    )


def build_pqrs_json(update: Update, draft) -> dict:
    user = update.effective_user
    payload = {
        "tipo_usuario": draft.tipo_usuario,
        "descripcion_caso": draft.descripcion_caso,
        "pqrs_type": draft.pqrs_type,
        "irrespetuosa": draft.irrespetuosa,
    }
    if draft.tipo_usuario == "Identificado":
        payload["identificacion"] = {
            "documento": draft.documento,
            "nombre": draft.nombre_completo,
            "email": draft.email,
        }
    return {
        "radicado": str(uuid.uuid4())[:8].upper(),
        "pqrs": json.dumps(payload, ensure_ascii=False),
        "canal": "telegram",
        "fecha_utc": datetime.now(timezone.utc).isoformat(),
        "username": user.username if user else None,
        "nombre": user.full_name if user else None,
    }


async def extract_text_from_image(image_bytes: bytes) -> str:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com").strip()
    async with httpx.AsyncClient(timeout=OLLAMA_VISION_TIMEOUT) as client:
        response = await client.post(
            f"{base_url}/api/chat",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": OLLAMA_VISION_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Extract all visible text from this image exactly as it appears. "
                            "Return ONLY the extracted text, no commentary, no markdown, no preamble."
                        ),
                        "images": [image_b64],
                    }
                ],
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return (data.get("message", {}).get("content") or "").strip()


async def save_to_database(pqrs_json: dict) -> bool:
    await save_pqrs_to_postgres(pqrs_json)
    return True


async def persist_pqrs(pqrs_json: dict) -> None:
    for attempt in range(MAX_RETRIES):
        try:
            await save_to_database(pqrs_json)
            logger.info("PQRS guardada en BD. Radicado: %s", pqrs_json["radicado"])
            return
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning(
                "Intento %d/%d fallido: %s. Reintentando en %ds...",
                attempt + 1, MAX_RETRIES, exc, wait,
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(wait)
    raise RuntimeError("No fue posible guardar la PQRS en PostgreSQL.")


async def cleanup_draft(context: CallbackContext) -> None:
    chat_id = context.job.data
    draft = memory_store.get(chat_id)
    if draft is None or draft.status not in ACTIVE_STATES:
        return
    memory_store.clear(chat_id)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏰ Tu borrador ha expirado por inactividad.\n"
                 "Si deseas registrar una nueva solicitud, envía /start.",
        )
    except Exception as exc:
        logger.warning("No se pudo enviar notificación de timeout a %s: %s", chat_id, exc)


def _schedule_timeout(context, chat_id: int, draft) -> None:
    try:
        if draft.timeout_task is not None and not draft.timeout_task.done():
            draft.timeout_task.cancel()
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    memory_store.clear(chat_id)
    memory_store.set(chat_id, status=STATE_AWAITING_ID_TYPE)
    await update.message.reply_text(
        "¡Hola! Soy Alexa 👋, tu asistente de la secretaría.\n\n"
        "Para radicar tu solicitud, primero dime cómo prefieres identificarte:",
        reply_markup=build_identification_keyboard(),
    )
    draft = memory_store.get(chat_id)
    _schedule_timeout(context, chat_id, draft)


async def _show_identification_step(
    query: CallbackQuery, draft, step: str
) -> None:
    keyboard = build_identification_back_keyboard()
    if step == STATE_AWAITING_DOCUMENTO:
        text = "Perfecto ✅\n\n📝 *1/3* ¿Cuál es tu número de documento de identidad (cédula o DNI)?"
    elif step == STATE_AWAITING_NOMBRE:
        text = "✅ Documento registrado.\n\n📝 *2/3* ¿Cuál es tu nombre completo?"
    elif step == STATE_AWAITING_EMAIL:
        text = "✅ Nombre registrado.\n\n📝 *3/3* ¿Cuál es tu correo electrónico?"
    else:
        return
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def _handle_awaiting_documento(update, context, draft, user_text: str) -> None:
    chat_id = update.effective_chat.id
    if is_valid_documento(user_text):
        draft.documento = user_text.strip()
        draft.status = STATE_AWAITING_NOMBRE
        memory_store._drafts[chat_id] = draft
        await update.message.reply_text(
            "✅ Documento registrado.\n\n📝 *2/3* ¿Cuál es tu nombre completo?",
            parse_mode="Markdown",
            reply_markup=build_identification_back_keyboard(),
        )
    else:
        await update.message.reply_text(
            "⚠️ Ese documento no parece válido. Debe tener entre 6 y 15 dígitos, solo números.",
            reply_markup=build_identification_back_keyboard(),
        )


async def _handle_awaiting_nombre(update, context, draft, user_text: str) -> None:
    chat_id = update.effective_chat.id
    if is_valid_nombre(user_text):
        draft.nombre_completo = user_text.strip()
        draft.status = STATE_AWAITING_EMAIL
        memory_store._drafts[chat_id] = draft
        await update.message.reply_text(
            "✅ Nombre registrado.\n\n📝 *3/3* ¿Cuál es tu correo electrónico?",
            parse_mode="Markdown",
            reply_markup=build_identification_back_keyboard(),
        )
    else:
        await update.message.reply_text(
            "⚠️ Ese nombre no parece válido. Escribe al menos nombre y apellido, solo letras.",
            reply_markup=build_identification_back_keyboard(),
        )


async def _handle_awaiting_email(update, context, draft, user_text: str) -> None:
    chat_id = update.effective_chat.id
    if is_valid_email(user_text):
        draft.email = user_text.strip()
        draft.status = STATE_AWAITING_CASE
        memory_store._drafts[chat_id] = draft
        await _show_case_instruction(update, chat_id, draft, context)
    else:
        await update.message.reply_text(
            "⚠️ Ese correo no parece válido. Intenta con formato: usuario@dominio.com",
            reply_markup=build_identification_back_keyboard(),
        )


async def _show_case_instruction(update_or_query, chat_id: int, draft, context=None) -> None:
    text = (
        "Perfecto ✅\n\n"
        "Por favor, cuéntanos en detalle tu caso. Puedes escribir el texto directamente aquí "
        "o, si tienes una carta o documento impreso, tómale una foto nítida y envíala. "
        "Nuestro sistema procesará la información automáticamente.\n\n"
        "⚠️ *Nota:* No se reciben archivos en formato PDF o Word, únicamente texto o fotos."
    )
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update_or_query.message.reply_text(text, parse_mode="Markdown")


async def _handle_awaiting_case_text(update, context, draft, user_text: str) -> None:
    chat_id = update.effective_chat.id

    # Capa 1: validación rápida por keywords
    if not is_valid_case(user_text):
        await update.message.reply_text(
            "🤔 Hmm, eso no parece ser una solicitud, queja o reclamo. "
            "¿Podrías contarme con más detalle qué necesitas o qué problema tuviste?",
            reply_markup=build_invalid_case_keyboard(),
        )
        return

    # Capa 2: validación semántica con LLM
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    valido, razon = await validate_case_with_llm(user_text)
    if not valido:
        await update.message.reply_text(
            f"❌ Tu mensaje no tiene suficiente contexto para registrarlo.\n\n"
            f"📝 Razón: {razon}\n\n"
            f"Por favor, cuéntame con más detalle qué necesitas. Por ejemplo:\n"
            f"• \"Necesito una cita con el doctor\"\n"
            f"• \"Quiero reclamar un cobro indebido de la factura de luz\"\n"
            f"• \"El servicio de agua en el barrio centro está pésimo\"",
            reply_markup=build_invalid_case_keyboard(),
        )
        return

    updated = memory_store.update_case(chat_id, user_text)
    if updated is None:
        memory_store.set(chat_id, status=STATE_AWAITING_CASE)
        await update.message.reply_text("Hubo un problema al guardar tu caso. Intenta de nuevo.")
        return

    updated.descripcion_caso = user_text.strip()
    updated.pqrs_type = detect_pqrs_type(user_text)
    updated.irrespetuosa = check_offensive_language(user_text)
    updated.status = STATE_READY_TO_SEND
    memory_store._drafts[chat_id] = updated

    if detect_negative_sentiment(user_text):
        empathy = build_empathy_message(user_text)
        await update.message.reply_text(empathy)
        await asyncio.sleep(1)

    await update.message.reply_text(
        build_case_preview(updated.descripcion_caso),
        parse_mode="Markdown",
        reply_markup=build_send_keyboard(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    user_text = update.message.text.strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    draft = memory_store.get(chat_id)

    if draft is None:
        await update.message.reply_text(
            "Para iniciar una solicitud, envía /start y elige cómo prefieres identificarte. 👋"
        )
        return

    if draft.status == STATE_AWAITING_ID_TYPE:
        await update.message.reply_text(
            "Por favor elige una de las opciones del menú 👆",
            reply_markup=build_identification_keyboard(),
        )
        return

    if draft.status == STATE_AWAITING_DOCUMENTO:
        await _handle_awaiting_documento(update, context, draft, user_text)
        return

    if draft.status == STATE_AWAITING_NOMBRE:
        await _handle_awaiting_nombre(update, context, draft, user_text)
        return

    if draft.status == STATE_AWAITING_EMAIL:
        await _handle_awaiting_email(update, context, draft, user_text)
        return

    if draft.status == STATE_AWAITING_CASE:
        await _handle_awaiting_case_text(update, context, draft, user_text)
        return

    if draft.status == STATE_AWAITING_OCR_VALIDATION:
        await update.message.reply_text(
            "Estoy esperando tu confirmación sobre el texto de la imagen. "
            "Por favor usa los botones 👆"
        )
        return

    if draft.status == STATE_READY_TO_SEND:
        await update.message.reply_text(
            "Tu caso está listo para enviar. Usa los botones 👆",
            reply_markup=build_send_keyboard(),
        )
        return

    await update.message.reply_text(
        "Para iniciar una nueva solicitud, envía /start."
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    draft = memory_store.get(chat_id)

    if draft is None or draft.status != STATE_AWAITING_CASE:
        await update.message.reply_text(
            "Envía una foto solo cuando te la pida el sistema. "
            "Si quieres iniciar una nueva solicitud, envía /start."
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        image_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        logger.exception("Error descargando imagen: %s", exc)
        await update.message.reply_text(
            "😕 No pude descargar la imagen. Intenta enviarla de nuevo."
        )
        return

    try:
        ocr_text = await extract_text_from_image(image_bytes)
    except httpx.TimeoutException:
        logger.warning("OCR timeout para chat %s", chat_id)
        await update.message.reply_text(
            "😕 El procesamiento de la imagen tardó demasiado. "
            "Intenta escribir tu caso o enviar otra foto más nítida."
        )
        return
    except Exception as exc:
        logger.exception("Error en OCR: %s", exc)
        await update.message.reply_text(
            "😕 No pude procesar la imagen. "
            "Intenta escribir tu caso o enviar otra foto más nítida."
        )
        return

    if not ocr_text:
        await update.message.reply_text(
            "😕 La imagen no contiene texto legible. "
            "Intenta escribir tu caso o enviar otra foto más nítida."
        )
        return

    if not is_valid_case(ocr_text):
        draft.status = STATE_AWAITING_CASE
        memory_store._drafts[chat_id] = draft
        await update.message.reply_text(
            "🤔 La imagen no parece contener un caso válido (solicitud, queja o reclamo). "
            "Intenta con una foto más clara de tu carta o documento, o escribe tu caso directamente.",
            reply_markup=build_invalid_case_keyboard(),
        )
        return

    # Capa 2: validación semántica con LLM
    valido, razon = await validate_case_with_llm(ocr_text)
    if not valido:
        draft.status = STATE_AWAITING_CASE
        memory_store._drafts[chat_id] = draft
        await update.message.reply_text(
            f"❌ La imagen no parece contener un caso válido.\n\n"
            f"📝 Razón: {razon}\n\n"
            f"Intenta con una foto más clara o escribe tu caso directamente.",
            reply_markup=build_invalid_case_keyboard(),
        )
        return

    draft.ocr_text = ocr_text
    draft.status = STATE_AWAITING_OCR_VALIDATION
    memory_store._drafts[chat_id] = draft

    await update.message.reply_text(
        f"📷 He leído esto de la imagen:\n\n\"{escape_text(ocr_text)}\"\n\n"
        f"¿Es correcto?",
        reply_markup=build_ocr_validation_keyboard(),
    )


async def handle_unsupported_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ No puedo procesar ese tipo de archivo. "
        "Por favor, envíame tu caso como texto o una foto."
    )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query: CallbackQuery = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    draft = memory_store.get(chat_id)
    data = query.data

    if data == "id_identificado":
        memory_store.set(chat_id, status=STATE_AWAITING_DOCUMENTO, tipo_usuario="Identificado")
        draft = memory_store.get(chat_id)
        _schedule_timeout(context, chat_id, draft)
        await _show_identification_step(query, draft, STATE_AWAITING_DOCUMENTO)
        return

    if data == "id_anonimo":
        memory_store.set(chat_id, status=STATE_AWAITING_CASE, tipo_usuario="Anonimo")
        draft = memory_store.get(chat_id)
        _schedule_timeout(context, chat_id, draft)
        await query.edit_message_text(
            "🔒 Tu solicitud será procesada de forma anónima. Al no registrar correo, "
            "deberás consultar el estado guardando el número de radicado que te "
            "daremos al final."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "Por favor, cuéntanos en detalle tu caso. Puedes escribir el texto directamente aquí "
                "o, si tienes una carta o documento impreso, tómale una foto nítida y envíala. "
                "Nuestro sistema procesará la información automáticamente.\n\n"
                "⚠️ *Nota:* No se reciben archivos en formato PDF o Word, únicamente texto o fotos."
            ),
            parse_mode="Markdown",
        )
        return

    if data == "restart_identification":
        memory_store.set(chat_id, status=STATE_AWAITING_ID_TYPE)
        draft = memory_store.get(chat_id)
        _schedule_timeout(context, chat_id, draft)
        await query.edit_message_text(
            "Perfecto, empezamos de nuevo 🔄\n\n"
            "¿Cómo prefieres identificarte?",
            reply_markup=build_identification_keyboard(),
        )
        return

    if data == "ocr_yes":
        if draft is None or draft.status != STATE_AWAITING_OCR_VALIDATION:
            await query.edit_message_text("Tu sesión expiró. Envía /start para iniciar de nuevo.")
            return
        draft.descripcion_caso = draft.ocr_text
        draft.pqrs_type = detect_pqrs_type(draft.ocr_text)
        draft.irrespetuosa = check_offensive_language(draft.ocr_text)
        draft.status = STATE_READY_TO_SEND
        memory_store._drafts[chat_id] = draft
        if detect_negative_sentiment(draft.descripcion_caso):
            empathy = build_empathy_message(draft.descripcion_caso)
            await query.edit_message_text(empathy)
            await asyncio.sleep(1)
            await context.bot.send_message(
                chat_id=chat_id,
                text=build_case_preview(draft.descripcion_caso),
                parse_mode="Markdown",
                reply_markup=build_send_keyboard(),
            )
        else:
            await query.edit_message_text(
                build_case_preview(draft.descripcion_caso),
                parse_mode="Markdown",
                reply_markup=build_send_keyboard(),
            )
        return

    if data == "ocr_no":
        if draft is None:
            await query.edit_message_text("Tu sesión expiró. Envía /start para iniciar de nuevo.")
            return
        draft.status = STATE_AWAITING_CASE
        draft.ocr_text = ""
        memory_store._drafts[chat_id] = draft
        await query.edit_message_text(
            "Entendido, vuelve a escribir tu caso o envía otra foto 📷"
        )
        return

    if data == "edit_case":
        if draft is None:
            await query.edit_message_text("Tu sesión expiró. Envía /start para iniciar de nuevo.")
            return
        draft.status = STATE_AWAITING_CASE
        draft.descripcion_caso = ""
        draft.ocr_text = ""
        memory_store._drafts[chat_id] = draft
        await query.edit_message_text(
            "Perfecto, vamos a corregir tu caso 🔄\n\n"
            "Envíamelo de nuevo como texto o como foto."
        )
        return

    if data == "retry_case":
        if draft is None:
            await query.edit_message_text("Tu sesión expiró. Envía /start para iniciar de nuevo.")
            return
        draft.status = STATE_AWAITING_CASE
        draft.descripcion_caso = ""
        draft.ocr_text = ""
        memory_store._drafts[chat_id] = draft
        await query.edit_message_text(
            "Entendido 📝\n\n"
            "Escríbeme tu caso con detalle. Cuéntame qué necesitas o qué problema tuviste. "
            "También puedes enviar una foto de un documento o carta."
        )
        return

    if data == "send_request":
        if draft is None or draft.status != STATE_READY_TO_SEND:
            await query.edit_message_text("Tu sesión expiró. Envía /start para iniciar de nuevo.")
            return

        pqrs_json = build_pqrs_json(update, draft)
        radicado = pqrs_json["radicado"]
        memory_store.clear(chat_id)

        try:
            await persist_pqrs(pqrs_json)
            await query.edit_message_text(
                f"✅ Tu solicitud quedó registrada.\n\n*Radicado:* #{radicado}\n\n"
                f"📌 Guárdalo para consultar el estado de tu solicitud.",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.exception("Error al persistir PQRS %s: %s", radicado, exc)
            await query.edit_message_text(
                "No fue posible registrar tu solicitud en este momento. "
                "Intenta de nuevo o contacta a soporte."
            )
        return

    await query.edit_message_text("Opción no reconocida. Envía /start para iniciar.")


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

    app = Application.builder().token(telegram_token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.AUDIO | filters.VIDEO) & ~filters.COMMAND,
        handle_unsupported_file,
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    logger.info("Bot Alexa iniciado. Presiona Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
