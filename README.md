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
Agorapp API

Backend desarrollado con Spring Boot para gestionar y consultar PQRS procesadas, incluyendo asignación de secretarías y resolución de casos.

## Objetivo del servicio

Este servicio expone una API REST para:

1. Consultar el listado de PQRS.
2. Consultar una PQRS por radicado.
3. Filtrar PQRS por secretaría (con normalización de texto).
4. Marcar una PQRS como resuelta.
5. Actualizar la secretaría asociada a una PQRS.
6. Listar secretarías de alcaldía.

Está pensado como una pieza de una arquitectura de microservicios, enfocada en la gestión de notificaciones y clasificación de PQRS.

## Stack tecnológico

1. Java 21
2. Spring Boot 3
3. Spring Web
4. Spring Data JPA
5. PostgreSQL
6. Maven
7. Docker y Docker Compose (para ejecución containerizada)

## Variables de entorno

Configura estas variables antes de ejecutar:

1. DB_URL
2. DB_USERNAME
3. DB_PASSWORD
4. CORS_ALLOWED_ORIGINS (opcional, por defecto *)

El puerto se resuelve con esta prioridad:

1. PORT
2. SERVER_PORT
3. 8082 (valor por defecto)

## Endpoints principales

### QPR / PQRS

1. GET /qprs
   Lista todas las PQRS.

2. GET /qprs/{id}
   Consulta una PQRS por radicado.

3. GET /qprs/por-secretaria?secretaria=Nombre Secretaria
   Filtra PQRS por nombre de secretaría (insensible a tildes y espacios).

4. PATCH /qprs/{id}/resolver
   Marca como resuelta una PQRS.

5. PATCH /qprs/{id}/secretaria
   Actualiza solo la secretaría de un radicado.

   Body JSON:
   {
     "secretaria": "Secretaría de Gobierno"
   }

6. PATCH /qprs/secretaria
   Actualiza secretaría enviando radicado y secretaría.

   Body JSON:
   {
     "radicado": "RAD-123",
     "secretaria": "Secretaría de Hacienda"
   }

7. PATCH /qprs/actualizar-secretaria
   Alias del endpoint anterior con el mismo body JSON.

### Secretarías

1. GET /secretarias
   Lista las secretarías de alcaldía disponibles.

## Cómo correr el proyecto (local)

## Opción 1: Maven

Requisitos:

1. Java 21
2. Maven 3.9+
3. Base de datos PostgreSQL accesible

En PowerShell:

    $env:DB_URL="jdbc:postgresql://TU_HOST/TU_DB?sslmode=require"
    $env:DB_USERNAME="TU_USUARIO"
    $env:DB_PASSWORD="TU_PASSWORD"
    $env:CORS_ALLOWED_ORIGINS="*"
    mvn spring-boot:run

La API quedará disponible en:

    http://localhost:8082

## Opción 2: Docker Compose

Define primero las variables en tu entorno o en un archivo .env y luego ejecuta:

    docker compose up --build

La API quedará disponible en:

    http://localhost:8082

## Estructura de datos esperada

El servicio trabaja principalmente con la tabla pqrs_procesada (radicado como identificador) y con la tabla secretarias_alcaldia.

## Pruebas

Para ejecutar pruebas:

    mvn test

## Nota de arquitectura y despliegue

Este backend forma parte de una arquitectura de microservicios.  
Por esa razón, el despliegue y la orquestación de ambientes se gestionan en otro repositorio, separado de este código fuente.

En este repositorio se mantiene principalmente:

1. Lógica del microservicio.
2. Exposición de endpoints.
3. Integración con base de datos.
4. Configuración para ejecución local.
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

## Link del despliegue 

https://agorappp2026.vercel.app/

## link del bot de telegram

https://t.me/PQRSIAbot 