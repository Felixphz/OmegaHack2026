# OmegaHack - Plataforma de agentes para gestionar PQRSD

Este proyecto nace con una idea simple: **que una PQRSD no se quede solo en “recibida”**, sino que recorra un proceso completo de análisis, entendimiento y preparación para gestión institucional.

En lugar de un único modelo haciendo todo, aquí se usa un enfoque de **múltiples agentes especializados**, cada uno con una responsabilidad concreta.

---

## ¿Qué problema resuelve?

Cuando una entidad recibe muchas PQRSD, aparecen problemas comunes:

- mensajes incompletos o poco claros,
- clasificación inconsistente,
- asignación manual de secretaría,
- falta de trazabilidad del estado del caso.

Este sistema organiza ese flujo para que, desde que entra una PQRSD por Telegram, termine en una tabla procesada con contexto útil para atención.

---

## La idea detrás de los agentes

La arquitectura separa funciones para que cada agente haga “una cosa bien”:

1. **Agente de enrutamiento (`routing`)**  
   Lee el texto de la PQRSD y, apoyado en la documentación interna (RAG), propone a qué **secretaría** debe remitirse.

2. **Agente de resumen (`resumidor`)**  
   Convierte el texto en una versión más operativa para trabajo interno:
   - `titulo_ia` (encabezado corto)
   - `resumen_ia` (síntesis)

3. **Agente de clasificación (`classification`)**  
   Usa el contexto legal/documental para:
   - asignar `clasificacion`,
   - calcular `fecha_limite`,
   - marcar si el lenguaje es `irrespetuosa`,
   - dejar `resuelta = false` por defecto.

4. **Orquestador**  
   Es quien coordina todo el ciclo, evita reprocesos por `radicado` y guarda el resultado final.

---

## Paso a paso del flujo completo

### Paso 1: entra una PQRSD por Telegram
El bot recibe el mensaje ciudadano y lo registra en `pqrs` con su `radicado`.

### Paso 2: el orquestador detecta nuevos casos
Busca en `pqrs` solo los radicados que aún **no están** en `pqrs_procesada`.

### Paso 3: enrutamiento
El primer agente propone la secretaría de destino usando RAG.

### Paso 4: resumen
El segundo agente crea título y resumen para facilitar lectura operativa.

### Paso 5: clasificación y tiempos
El tercer agente clasifica y calcula fecha límite según contexto normativo recuperado.

### Paso 6: persistencia final
Se guarda el resultado en `pqrs_procesada`.  
Si el radicado ya existe, no lo duplica.

---

## ¿Qué hace robusto este diseño?

- **Idempotencia por `radicado`**: evita procesar el mismo caso dos veces.
- **Orquestación continua (`--watch`)**: soporta llegada constante de nuevas PQRSD.
- **RAG sobre documentación interna**: las decisiones se apoyan en tus documentos, no solo en intuición del modelo.
- **Separación de responsabilidades**: más fácil ajustar un agente sin romper todo.

---

## Estructura del proyecto

```text
app/
  bot/
    telegram_chatbot.py
    pqrs_memory.py
  agents/
    pqrs_routing_agent.py
    pqrs_resumidor_agent.py
    pqrs_classification_agent.py
  storage/
    postgres_pqrs_store.py
  pipelines/
    pqrs_orchestrator.py
  ingestion/
    pdf_supabase_ingest.py
```

---

## Repositorios relacionados

- **Frontend (Agorapp):** https://github.com/sammirBolanos/agorapp  
  Interfaz web de la plataforma donde los usuarios interactúan con el sistema.
- **Backend de notificaciones:** https://github.com/sammirBolanos/notificationAgorapp  
  Servicio encargado de enviar notificaciones asociadas al flujo de PQRSD.

---

## Cómo ponerlo a funcionar (rápido)

### 1) Instalar dependencias
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Cargar documentos al RAG (si aún no lo hiciste)
```bash
python -m app.ingestion.pdf_supabase_ingest Documents/
```

### 3) Levantar el bot de Telegram
```bash
python -m app.bot.telegram_chatbot
```

### 4) Levantar el orquestador en continuo
```bash
python -m app.pipelines.pqrs_orchestrator --watch --batch-size 20 --poll-interval 15
```

Con eso, cada nueva PQRSD que llegue por Telegram debería entrar al ciclo completo.

---

## Variables de entorno (visión práctica)

En tu `.env` necesitas, en esencia:

- credenciales de **Telegram**,
- conexión a **PostgreSQL** (tabla `pqrs`),
- conexión a **Supabase** (tabla vectorial `documents`),
- claves de **Cohere** (embeddings),
- claves/config de **Ollama Cloud** (LLM),
- opcionalmente nombre de tabla destino (`PQRS_PROCESSED_TABLE`).

> Recomendación: mantén `.env` fuera de control de versiones (ya está ignorado).

---

## Tablas principales (explicadas)

- **`pqrs`**: bandeja de entrada “cruda” desde Telegram.  
- **`documents`**: base vectorial con tu conocimiento interno.  
- **`pqrs_procesada`**: salida lista para operación, con enrutamiento, resumen, clasificación y estado.

---

## Nota operativa final

Si quieres que todo sea realmente automático, debes tener **dos procesos vivos**:

1. Bot Telegram
2. Orquestador en `--watch`

Si el bot está arriba pero el orquestador no, las PQRSD se guardan, pero no avanzan al ciclo multiagente.
