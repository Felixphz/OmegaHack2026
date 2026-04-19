import logging
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from openai import AuthenticationError
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




def build_chain():
    model_name = os.getenv("OLLAMA_MODEL", "gpt-oss:20b  ")
    api_key = os.getenv("OLLAMA_API_KEY")
    base_url = os.getenv("OLLAMA_BASE_URL", "https://ollama.com")

    llm = ChatOllama(
        model=model_name,
        temperature=0.4,
        base_url=base_url,
        client_kwargs={
            "headers": {"Authorization": f"Bearer {api_key}"}
        }
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", "Eres un asistente útil, claro y breve. Responde en español salvo que el usuario pida otro idioma."),
        ("human", "{user_message}"),
    ])

    return prompt | llm | StrOutputParser()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy tu bot con LangChain. Escríbeme cualquier pregunta."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    chain = context.application.bot_data["chain"]
    try:
        response = await chain.ainvoke({"user_message": user_text})
        await update.message.reply_text(response)
    except AuthenticationError:
        logger.exception("Authentication error while invoking model.")
        await update.message.reply_text(
            "No pude autenticarme con el modelo. Revisa OLLAMA_API_KEY/OPENAI_API_KEY y OLLAMA_BASE_URL."
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Error while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Hubo un error procesando tu mensaje. Intenta de nuevo."
        )


def main():
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    api_key = os.getenv("OLLAMA_API_KEY")

    if not telegram_token:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN en variables de entorno.")
    if not api_key:
        raise ValueError("Falta OLLAMA_API_KEY en variables de entorno.")

    app = Application.builder().token(telegram_token).build()
    app.bot_data["chain"] = build_chain()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    logger.info("Bot iniciado. Presiona Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
