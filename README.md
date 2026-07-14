# 🤖 Agente RAG Corporativo — NovaPay

Agente conversacional que responde preguntas de los colaboradores sobre los documentos internos
de una fintech ficticia (**NovaPay**), **citando siempre la fuente** y **admitiendo explícitamente
cuando la información no está** en la base documental.

Challenge final de **Alura / Oracle Next Education (ONE)**.

```
Vos: ¿Cuántos días de vacaciones tengo?

Agente: Tenés derecho a 22 días hábiles de licencia anual remunerada por año
        calendario trabajado (politica_rrhh.pdf, página 1).
        Fuentes: politica_rrhh.pdf (página 1, sección «3. Licencia Anual Remunerada»)

Vos: ¿Cuál es la política de estacionamiento de la oficina?

Agente: No encontré esta información en los documentos disponibles.
```

La segunda respuesta importa tanto como la primera: **el agente no inventa.**

Y la primera tiene truco. La palabra "vacaciones" **no aparece en ningún documento** — la política
habla de "licencia anual remunerada". El agente la encuentra igual porque busca por *significado*,
no por coincidencia de palabras.

---

## Índice

- [Arquitectura](#arquitectura)
- [Cómo se evita la alucinación](#cómo-se-evita-la-alucinación)
- [Base de conocimiento](#base-de-conocimiento)
- [Ejemplos reales de preguntas y respuestas](#ejemplos-reales-de-preguntas-y-respuestas)
- [Ejecución local](#ejecución-local)
- [Deploy en OCI](#deploy-en-oci)
- [Registro de ejecución](#registro-de-ejecución)
- [Tests](#tests)
- [Decisiones y limitaciones](#decisiones-y-limitaciones)

---

## Arquitectura

```
                    ┌──────────────────────────────────────────┐
   6 documentos     │  INGESTA          app/ingest.py          │
   PDF · DOCX       │  · un extractor por formato              │
   XLSX · CSV       │  · limpieza (encabezados, pies, ruido)   │
   JSON · MD        │  · chunking por sección + metadatos      │
        │           └──────────────────────────────────────────┘
        │                             │  106 chunks
        ▼                             ▼
                    ┌──────────────────────────────────────────┐
                    │  INDEXACIÓN       app/index.py           │
                    │  · embeddings (gemini-embedding-001)     │
                    │  · FAISS IndexFlatIP, 768 dims           │
                    └──────────────────────────────────────────┘
                                      │  índice en disco
   ┌──────────┐                       ▼
   │ Pregunta │──────▶ ┌──────────────────────────────────────────┐
   └──────────┘        │  RECUPERACIÓN     app/retrieve.py        │
                       │  · embedding de la pregunta              │
                       │  · top-4 por similitud coseno            │
                       │  · descarta lo que no supere el umbral   │
                       └──────────────────────────────────────────┘
                                      │
                        ¿quedó algún fragmento?
                         │                    │
                        NO                   SÍ
                         │                    │
                         ▼                    ▼
              ┌────────────────┐   ┌──────────────────────────────┐
              │ "No encontré   │   │  GENERACIÓN    app/agent.py  │
              │  esta info."   │   │  · prompt anti-alucinación   │
              │                │   │  · gemini-3.1-flash-lite     │
              │ EL LLM NI SE   │   │  · cita documento + sección  │
              │ LLAMA          │   └──────────────────────────────┘
              └────────────────┘                │
                         │                      │
                         └──────────┬───────────┘
                                    ▼
                       ┌─────────────────────────┐
                       │ Streamlit  app/interface│
                       │ Log JSONL  logs/        │
                       └─────────────────────────┘
```

### Stack

| Componente | Elección | Por qué |
|---|---|---|
| LLM | Gemini `gemini-3.1-flash-lite` | Free tier sin tarjeta. Estable, no preview. Con `temperature=0` y el contexto ya recuperado, la tarea es leer y citar, no razonar de cero. |
| Embeddings | Gemini `gemini-embedding-001` | La misma API key cubre todo el pipeline. |
| Vector store | FAISS local (`IndexFlatIP`) | Con ~100 chunks la búsqueda exhaustiva es instantánea y **exacta**. Un índice aproximado (HNSW) o una vector DB gestionada recién se justifican con cientos de miles de vectores. |
| Interfaz | Streamlit | El enunciado dice explícitamente que la interfaz no se evalúa. |
| Deploy | Docker → OCI Compute | La ruta más corta a un deploy real. |

---

## Cómo se evita la alucinación

Hay **dos capas, y son distintas a propósito**:

### 1. Capa dura (código) — `app/agent.py`

Si el retrieval no devuelve ningún fragmento por encima del umbral de similitud, **al LLM ni se lo
llama**. Se devuelve el "no lo sé" directamente.

Un modelo al que no se le pregunta nada no puede inventar nada. Esta capa **no depende de que el
modelo obedezca**, y por eso es una garantía y no una expectativa.

### 2. Capa blanda (prompt) — `app/agent.py`

Cuando sí hay contexto, el prompt de sistema restringe al modelo a responder *exclusivamente* con
base en los fragmentos recibidos, citando documento y ubicación, y a admitir cuando no alcanzan.

### Por qué hacen falta las dos

El umbral se calibró con datos, no a ojo (`scripts/eval_questions.py`):

| Población | Mejor score |
|---|---|
| Peor pregunta **con** respuesta | 0.699 |
| Mejor pregunta **sin** respuesta | 0.647 |

Se separan por apenas **0.05**. Los embeddings comprimen todo el texto en español en una banda alta
y estrecha: dos textos sin nada que ver rara vez bajan de 0.6. Con un margen así, **apoyarse solo en
el umbral sería frágil**.

Cada capa se verificó por separado. Desactivando el umbral —o sea, forzando al LLM a recibir
contexto irrelevante— el prompt igual frena las preguntas fuera de alcance:

```
[FRENÓ] ¿Cuál es la política de estacionamiento de la oficina?
[FRENÓ] ¿A cuánto cotiza el dólar hoy?
[FRENÓ] ¿Cuántos empleados tiene NovaPay?
```

---

## Base de conocimiento

Seis documentos ficticios de NovaPay, **uno por formato y por dominio de negocio**. Se generan con
`scripts/generate_docs.py`, así que la base es reproducible desde cero.

| Documento | Formato | Categoría | Qué contiene |
|---|---|---|---|
| `politica_rrhh.pdf` | PDF | Recursos Humanos | Licencias, home office, beneficios, onboarding |
| `politica_privacidad.docx` | DOCX | Legal y Compliance | Datos, retención, derechos del titular |
| `tarifas_comisiones.csv` | CSV | Financiero | Comisiones por operación |
| `planes_productos.json` | JSON | Comercial | Planes, precios, límites |
| `faq_transacciones.md` | Markdown | Operacional | Plazos, límites, reclamos |
| `ventas_2025.xlsx` | XLSX | Datos y Sistemas | Altas por mes y plan |

Cada formato se extrae distinto, y eso importa:

- **PDF** → texto por página, **descartando encabezados y pies repetidos**. No están hardcodeados:
  se detectan por frecuencia (líneas que aparecen en casi todas las páginas), así funciona con
  cualquier PDF. El corte es **por sección**, no por tamaño (ver [limitaciones](#decisiones-y-limitaciones)).
- **CSV / XLSX** → fila por fila, **repitiendo los encabezados de columna en cada fila**. Una fila
  suelta (`Extracción en cajero | 850 | ARS`) no significa nada sin sus columnas: su embedding sería
  inútil. Repetir el encabezado es lo que permite que *"¿cuánto cuesta sacar plata del cajero?"*
  recupere esa fila.
- **JSON** → un chunk por plan, así preguntar por el plan Business no arrastra los otros tres al
  contexto.
- **DOCX / Markdown** → se corta por título, agrupando cada encabezado con su contenido.

---

## Ejemplos reales de preguntas y respuestas

**10 de 10 casos correctos.** La salida completa y sin editar está en
[`docs/ejemplos_qa.md`](docs/ejemplos_qa.md), generada por `scripts/eval_questions.py`.

| # | Pregunta | Respuesta | Fuente citada |
|---|---|---|---|
| 1 | ¿Cuántos días de vacaciones tengo? | 22 días hábiles de licencia anual remunerada | `politica_rrhh.pdf` (pág. 1, §3) |
| 2 | ¿Cuántos días por semana puedo trabajar desde casa? | Hasta 3 días por semana | `politica_rrhh.pdf` (pág. 1, §2) |
| 3 | ¿Cuánto cuesta extraer efectivo en un cajero de la red? | 850 ARS por operación | `tarifas_comisiones.csv` (fila 4) |
| 4 | ¿Cuál es el límite diario de transferencia del plan Business? | 10.000.000 | `planes_productos.json` (plan Business) |
| 5 | ¿Cuánto puede demorar una transferencia de más de 2 millones? | Hasta 24 horas hábiles (revisión antifraude) | `faq_transacciones.md` |
| 6 | ¿Por cuánto tiempo se conservan los datos de mis transacciones? | 10 años | `politica_privacidad.docx` (§3) |
| 7 | ¿Qué plan tuvo más altas en diciembre de 2025? | Cuenta Plus | `ventas_2025.xlsx` (Métricas Clave) |
| 8 | ¿Cuánto presupuesto tengo para capacitarme? | USD 800 anuales | `politica_rrhh.pdf` (pág. 2, §5) |
| 9 | **¿Cuál es la política de estacionamiento?** | *No encontré esta información* ✅ | — |
| 10 | **¿A cuánto cotiza el dólar hoy?** | *No encontré esta información* ✅ | — |

Las preguntas 9 y 10 están **deliberadamente fuera de alcance**: ningún documento las cubre. Que el
agente las rechace es tan importante como que responda bien las otras ocho.

---

## Ejecución local

**Requisitos:** Python 3.12+ y una API key de Google AI Studio (gratis, sin tarjeta:
https://aistudio.google.com/apikey).

```bash
# 1. Clonar e instalar
git clone https://github.com/chochoFernandez/challenge-alura-agente.git
cd challenge-alura-agente
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configurar la API key
cp .env.example .env
#    abrí .env y completá GOOGLE_API_KEY=...

# 3. Generar los documentos ficticios
python scripts/generate_docs.py

# 4. Construir el índice vectorial (tarda ~1-2 min: va a ritmo para no chocar la cuota)
python scripts/build_index.py

# 5. Usarlo
streamlit run app/interface.py            # interfaz web  → http://localhost:8501
python scripts/ask.py "¿Cuántos días de vacaciones tengo?"   # o por CLI
python scripts/eval_questions.py          # o la batería completa de 10 preguntas
```

El `.env` está en `.gitignore` y **nunca se sube al repo**. El `.env.example` documenta todas las
variables sin valores reales.

---

## Deploy en OCI

> 🚧 **Pendiente.** Esta sección se completa con la captura del agente corriendo en OCI.

### Servicios de OCI utilizados

- **OCI Compute** — VM Ampere A1 (ARM, free tier) corriendo el contenedor.

### Pasos

```bash
# En la VM de OCI (Ubuntu 22.04, forma VM.Standard.A1.Flex)
sudo apt update && sudo apt install -y docker.io git
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/chochoFernandez/challenge-alura-agente.git
cd challenge-alura-agente

docker build -t agente-novapay .

# La API key entra en RUNTIME, nunca en la imagen:
docker run -d --name agente -p 8501:8501 \
  -e GOOGLE_API_KEY="tu-key" \
  --restart unless-stopped \
  agente-novapay
```

Después hay que abrir el puerto 8501 en la **Security List de la VCN** (Ingress rule: `0.0.0.0/0`,
TCP, puerto 8501) y en el firewall de la VM (`sudo iptables -I INPUT -p tcp --dport 8501 -j ACCEPT`).

### Evidencia

<!-- Acá va la captura del agente corriendo en la IP pública de OCI -->

---

## Filtro por área

La búsqueda se puede acotar a un dominio de negocio (RH, Financiero, Legal, Comercial,
Operacional, Datos) desde el selector de la barra lateral. Los metadatos de categoría ya vienen
en cada chunk desde la ingesta.

Cuando hay filtro activo se busca sobre **todo** el índice y recién después se filtra. Hacerlo al
revés —filtrar el top-4 ya calculado— devolvería cero resultados cada vez que los 4 fragmentos más
parecidos fueran de otra categoría, aunque la categoría pedida sí tuviera algo relevante: sería un
filtro que además rompe la búsqueda.

Compone bien con el umbral: filtrar una pregunta financiera a "Recursos Humanos" devuelve cero
fragmentos, y el agente dice "no lo sé" en vez de forzar una respuesta con documentos que no vienen
al caso.

---

## Registro de ejecución

Cada consulta deja una línea en `logs/executions.jsonl` (formato JSON Lines):

```json
{
  "tipo": "consulta",
  "id": "6ccb5671dadf",
  "timestamp": "2026-07-14T18:15:56.832228+00:00",
  "pregunta": "¿Cuántos días de vacaciones tengo?",
  "respuesta": "Tenés derecho a 22 días hábiles de licencia anual remunerada...",
  "respondida": true,
  "modelo": "gemini-3.1-flash-lite",
  "latencia_ms": 2287,
  "contexto_recuperado": [
    {
      "chunk_id": "a3f8c91e2b7d4056",
      "documento": "politica_rrhh.pdf",
      "ubicacion": "página 1, sección «3. Licencia Anual Remunerada»",
      "categoria": "Recursos Humanos",
      "score": 0.7284
    }
  ]
}
```

Se guarda el **contexto recuperado con su score**, no solo las citas: para auditar *por qué* el
agente respondió lo que respondió hay que poder ver qué fragmentos tuvo delante.

El campo `respondida` permite medir la tasa de preguntas sin cubrir, que es la señal de qué
documento falta agregar a la base.

### Feedback

Cada respuesta tiene botones 👍/👎. El voto se guarda como una **línea nueva**, no modificando la
línea de la consulta:

```json
{"tipo": "feedback", "consulta_id": "6ccb5671dadf", "valor": "negativo", "timestamp": "..."}
```

Es a propósito: el log queda *append-only*, se escribe sin releer ni reescribir el archivo, y no hay
forma de corromper lo ya registrado. Se cruzan por el `id` de la consulta.

El feedback negativo es la señal más valiosa del sistema: marca las preguntas donde el agente
respondió mal o donde falta un documento en la base.

Hay un ejemplo real en [`logs/executions.sample.jsonl`](logs/executions.sample.jsonl).

---

## Tests

```bash
pytest tests/ -q     # 32 tests, ~3 segundos
```

Corren **sin API key y sin red**: el LLM y el retriever van mockeados. Un test que depende de la
respuesta real de un LLM no puede afirmar nada, porque cambia entre corridas.

Lo que se testea es la lógica propia. En particular:

- **La garantía anti-alucinación**: que sin contexto el LLM **no se llame** (`assert_not_called`).
- **La idempotencia**: que el `chunk_id` sea determinista, así reconstruir el índice nunca duplica.
- **La regresión del chunking**: que `"22 días hábiles"` quede en la sección de licencias y no
  ahogado en el chunk del home office. Ese bug ya costó una pregunta y no vuelve a pasar sin que un
  test lo grite.

---

## Decisiones y limitaciones

**El RAG no hace agregaciones.** Recupera fragmentos de texto; no puede sumar 5.000 filas para
calcular "el plan más vendido". Por eso `ventas_2025.xlsx` incluye una hoja de **resumen mensual ya
agregado**: así el fragmento recuperado *contiene literalmente* la respuesta. Sin esa hoja, la
pregunta 7 sería incontestable. Resolverlo de verdad requeriría un agente con acceso a una
herramienta de consulta SQL/pandas, que está fuera del alcance de este challenge.

**Sin reranking.** El enunciado lo marca como no evaluado. Con 106 chunks y una búsqueda exhaustiva
exacta, un reranker agregaría latencia y una dependencia pesada a cambio de una mejora marginal.

**El umbral es frágil por naturaleza.** Separa las dos poblaciones por 0.05 (ver
[anti-alucinación](#cómo-se-evita-la-alucinación)). Es una primera barrera, no la única, y está
documentado como tal en el código.

**El chunking por sección depende de que el documento tenga secciones.** El PDF se corta por títulos
numerados (`3. Licencia Anual Remunerada`). Un PDF sin estructura de títulos volvería a cortarse solo
por tamaño, con el riesgo de orfandad que eso implica.

**Free tier de Gemini: 100 embeddings por minuto**, contados por texto y no por request. Con 106
chunks, un build completo se pasa del límite por diseño: por eso `build_index.py` va a ritmo y tarda
~77 segundos. En runtime es un solo embedding por pregunta, así que no se nota.

---

## Estructura del proyecto

```
app/
├── config.py          Settings tipados (pydantic). Único lugar que lee el .env
├── llm.py             Cliente de Gemini. Único módulo que conoce al proveedor
├── ingest.py          Extracción, limpieza y chunking por formato
├── index.py           Embeddings + FAISS, con escritura atómica
├── retrieve.py        Búsqueda semántica top-k + umbral
├── agent.py           Prompt anti-alucinación, citación, fallback
├── logging_utils.py   Registro JSONL
└── interface.py       Streamlit
scripts/
├── generate_docs.py   Genera los 6 documentos ficticios
├── build_index.py     (Re)construye el índice — idempotente
├── ask.py             CLI de preguntas
└── eval_questions.py  Batería de 10 preguntas + calibración del umbral
tests/                 29 tests, sin API ni red
data/docs/             Los 6 documentos
logs/                  Registro de ejecución (JSONL)
```
