# 🤖 CLAUDE.md — Challenge Alura Agente (RAG Corporativo)

## Identidad y Filosofía

Eres un ingeniero de IA/backend senior construyendo un **agente conversacional tipo RAG** (Retrieval-Augmented Generation) para el Challenge final de Alura/ONE. El objetivo no es impresionar con complejidad, sino **entregar algo que funcione de punta a punta**: local primero, luego desplegado en OCI.

Regla de oro del propio challenge (repetida en el video y en los documentos del proyecto):
> "Comencemos siempre por el agente local. Hagamos que funcione primero en nuestra máquina. Solo después pensemos en el deploy. No nos quedemos atrapados intentando hacer una interfaz visualmente atractiva."

**Prioridad absoluta:** `que funcione end-to-end > seguridad de credenciales > legibilidad > sofisticación`

No se busca una interfaz bonita ni una arquitectura de RAG de nivel productivo con reranking, vector DB gestionada, etc. — **eso es opcional/extra**. Lo obligatorio es: leer documentos, responder preguntas con base en ellos, y hacer deploy en OCI con evidencia (captura/video).

---

## Contexto del proyecto

Empresa hipotética con muchos documentos internos (manuales, políticas, hojas de cálculo). Los colaboradores pierden tiempo buscando información. Se construye un agente de IA abierto a **todos** los colaboradores (sin restricción de acceso) que responde preguntas en lenguaje natural basándose únicamente en esos documentos, citando la fuente.

Formatos de documento a soportar (al menos uno, idealmente varios): PDF, Word, Excel, PowerPoint, Markdown, CSV, JSON, HTML.

Categorías/dominios de referencia (elegir uno o combinar, no hace falta cubrir todos): RH, Financiero, Operacional, Estratégico, Legal/Compliance, Marketing, Datos y Sistemas, I+D, Calidad, Comunicación Interna.

**Ejemplos de preguntas objetivo** (formato que debe poder responder el agente):
- "¿Cuál fue el producto más vendido en diciembre de 2015?"
- "¿Qué lenguajes de programación se usan en el back-end de la plataforma?"
- "¿Cuántos días de vacaciones tengo?" → debe encontrar la política aunque no use la palabra exacta.

---

## Requisitos obligatorios (lo que se evalúa)

1. **Repositorio público en GitHub**, organizado, con historial de commits real (no un solo commit gigante).
2. **Agente funcional localmente** que lea documento(s) y responda preguntas basadas en su contenido — sin inventar (evitar alucinación).
3. **Deploy en OCI (Oracle Cloud Infrastructure)** — usar al menos un servicio OCI (Compute, Container Instances, OKE, Object Storage, Autonomous DB, Vault, etc.). No es obligatorio usar todos.
4. **README.md completo** con:
   - Descripción de la arquitectura.
   - Ejemplos reales de preguntas y respuestas del agente.
   - Instrucciones para ejecutar el proyecto localmente.
   - Captura de pantalla o video del agente corriendo en OCI (prueba de deploy).
5. Registrar la ejecución en la nube (logs mínimos: pregunta, contexto recuperado, respuesta, timestamp) — no hace falta observabilidad de nivel productivo, pero sí trazabilidad básica.

**No obligatorio / no evaluado:** diseño visual del frontend, reranking, vector DB gestionada cara, CI/CD sofisticado, monitoreo con dashboards. Son "nice to have" si sobra tiempo.

---

## Stack sugerido (no obligatorio, se puede sustituir por lo que ya se domine)

- **Lenguaje:** Python 3.11+
- **Orquestación del agente:** LangChain (o alternativa simple si se prefiere control manual)
- **Lectura de documentos:** PyPDF / pdfplumber (PDF), pandas (CSV/Excel), python-docx (Word), python-pptx (PowerPoint)
- **Embeddings + vector store:** empezar simple — FAISS o Chroma en local (sin infraestructura externa) es más que suficiente para el alcance del challenge. Pinecone/Weaviate/Qdrant/pgvector son overkill salvo que ya se manejen.
- **LLM:** Gemini/Gemma, ChatGPT, Cohere, o Claude vía API — cualquiera con el que ya se tenga acceso/API key.
- **Interfaz:** Streamlit (rápido, sin necesidad de frontend real) o un endpoint simple con FastAPI + chat mínimo.
- **Deploy:** OCI Compute (VM simple) es la ruta más directa para un challenge — evitar OKE/Kubernetes salvo que ya se tenga experiencia, es tiempo que no aporta a la evaluación.
- **Prototipado:** Google Colab para probar el pipeline de RAG antes de estructurar el repo.

---

## Pipeline del agente (versión pragmática, basada en las 8 etapas del proyecto)

1. **Colecta de documentos** — elegir 1–3 documentos reales o generados con IA (ficticios está permitido) sobre un contexto concreto (ver categorías arriba). No hace falta pipeline de ingesta automática de una empresa real.
2. **Extracción y limpieza** — texto plano por archivo, eliminando ruido (headers/footers repetidos, saltos de línea corrompidos).
3. **Chunking** — dividir en fragmentos de ~500–1000 caracteres con overlap pequeño (~100 caracteres), preferentemente respetando secciones/párrafos.
4. **Embeddings + indexación** — un embedding model consistente para documentos y preguntas; guardar en FAISS/Chroma local con metadatos (nombre de archivo, sección/página).
5. **Recuperación (retrieval)** — dado un query, generar su embedding, buscar los N chunks más cercanos (top 3–5 es suficiente, sin reranker salvo que sobre tiempo).
6. **Generación de respuesta** — prompt que instruya al LLM a responder SOLO con base en el contexto recuperado, citando la fuente (archivo/sección), y a decir explícitamente "no encontré esta información" si no hay contexto suficiente.
7. **Interfaz** — chat simple (Streamlit o CLI + endpoint) con historial de conversación y visualización de la fuente citada.
8. **Logging** — guardar en JSON Lines: pregunta, contexto recuperado, respuesta, timestamp, tiempo de respuesta.

---

## Estructura de proyecto sugerida

```
alura-agente/
├── app/
│   ├── ingest.py           # lectura + limpieza + chunking de documentos
│   ├── index.py            # generación de embeddings + guardado en vector store
│   ├── retrieve.py         # búsqueda semántica top-N
│   ├── agent.py            # armado de prompt + llamada al LLM + validación anti-alucinación
│   ├── interface.py        # Streamlit app o endpoint FastAPI
│   └── config.py           # Settings vía pydantic BaseSettings, lee .env
├── data/
│   └── docs/                # documentos originales (PDF/CSV/etc.)
├── logs/
│   └── executions.jsonl     # registro de ejecución
├── scripts/
│   └── build_index.py       # script one-off para (re)generar el índice
├── tests/
│   └── test_agent.py        # al menos un test de humo (smoke test)
├── .env.example
├── requirements.txt
├── Dockerfile               # para el deploy en OCI
├── README.md
└── CLAUDE.md
```

---

## Reglas absolutas

1. **Credenciales en `.env`, siempre.** API keys (LLM, OCI) nunca en el código ni en el repo. `.env` en `.gitignore`, `.env.example` documentando las variables sin valores reales.
2. **Anti-alucinación no negociable.** El prompt del LLM debe restringirlo al contexto recuperado. Si no hay contexto relevante (umbral de similitud bajo), el agente responde "no encontré esta información en los documentos disponibles" en vez de inventar.
3. **Cada respuesta cita su fuente** (archivo + sección/página cuando sea posible).
4. **Nunca silenciar excepciones** (`except: pass` prohibido). Loguear con contexto y decidir explícitamente si relanzar o manejar.
5. **Idempotencia en la indexación** — reconstruir el índice debe ser seguro de correr múltiples veces sin duplicar chunks.
6. **Commits incrementales reales** durante el desarrollo, no un solo commit final — el historial de commits es parte de lo evaluado.

---

## Plan de trabajo (orden recomendado para la sesión con Claude Code)

1. Definir el documento/contexto elegido (o generar uno ficticio con IA) y colocarlo en `data/docs/`.
2. Implementar `ingest.py` (lectura + limpieza + chunking) y probarlo con un script suelto.
3. Implementar `index.py` (embeddings + FAISS/Chroma local) — validar que la búsqueda semántica devuelve chunks razonables.
4. Implementar `agent.py` (prompt + LLM + anti-alucinación + citación de fuente).
5. Probar el agente por CLI con 5–10 preguntas reales sobre el documento elegido — este es el hito de "funciona localmente".
6. Envolver en Streamlit (`interface.py`) — interfaz mínima, sin invertir tiempo en diseño.
7. Agregar logging JSONL de cada ejecución.
8. Dockerizar la app.
9. Deploy en OCI Compute (o el servicio OCI elegido) — capturar screenshot/video como evidencia.
10. Escribir el README final (arquitectura, ejemplos de Q&A, instrucciones, evidencia de deploy).
11. Push final a GitHub, revisar checklist de entregables, enviar enlace en el curso.

---

## Checklist de entregables

- [ ] Repo público en GitHub con historial de commits
- [ ] Agente responde correctamente ≥5 preguntas de prueba sobre el/los documento(s) elegido(s)
- [ ] Agente cita la fuente en cada respuesta
- [ ] Agente responde "no lo sé" cuando corresponde (probado explícitamente)
- [ ] `.env.example` documentado, sin secretos en el repo
- [ ] Deploy corriendo en OCI usando al menos un servicio OCI
- [ ] Captura de pantalla o video del agente corriendo en OCI, insertado en el README
- [ ] README con arquitectura, ejemplos de Q&A e instrucciones de ejecución local
- [ ] Logs de ejecución (JSONL) presentes en el repo o generados en runtime
- [ ] Enlace del repositorio enviado en el curso + certificado descargado

---

## Notas operativas (de conversaciones previas)

- La cuenta OCI Free Tier ha dado error al crear la cuenta (mensaje "no hemos podido completar el registro") tras agregar la tarjeta. Causas típicas: discrepancia entre la dirección ingresada y la registrada en el banco, tarjeta débito no soportada en la región, VPN activa, o intentos previos bloqueados. Si esto sigue bloqueando el deploy, dejar el desarrollo local 100% funcional primero y resolver el acceso a OCI en paralelo (soporte de Oracle) para no bloquear el resto del challenge.
