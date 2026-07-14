"""Cliente de Gemini: embeddings y generación de texto.

Único módulo que conoce al proveedor. Si mañana hay que cambiar Gemini por otro LLM, se
reescribe este archivo y nada más: ni el índice, ni el retrieval, ni el agente saben qué
modelo hay detrás.
"""

import logging
import re
import time

import numpy as np
from google import genai
from google.genai import types
from google.genai.errors import ClientError

from app.config import get_settings, require_google_api_key

logger = logging.getLogger(__name__)

# Los embeddings de una pregunta y los de un documento NO se generan igual: Gemini optimiza
# el vector según para qué se lo va a usar. Usar el mismo task_type para ambos lados degrada
# el recall de forma notable, y es un error silencioso (todo "funciona", solo que peor).
TASK_DOCUMENTO = "RETRIEVAL_DOCUMENT"
TASK_PREGUNTA = "RETRIEVAL_QUERY"

# El free tier de Gemini permite 100 embeddings por minuto, y los cuenta por texto, no por
# request HTTP: un lote de 32 textos gasta 32. Con ~106 chunks, un build completo se pasa del
# límite sí o sí, así que hay que ir a ritmo en vez de mandar todo de una y comerse el 429.
TAMANIO_LOTE = 25
CUOTA_POR_MINUTO = 100
REINTENTOS = 6

# Margen de seguridad: apuntamos al 85% de la cuota, no al 100%. La ventana que mide Google no
# arranca cuando arrancamos nosotros, así que ir al límite exacto garantiza chocarlo.
PAUSA_ENTRE_LOTES = TAMANIO_LOTE / (CUOTA_POR_MINUTO * 0.85 / 60)


_cliente: genai.Client | None = None


def get_cliente() -> genai.Client:
    """Devuelve el cliente de Gemini, creándolo una sola vez por proceso."""
    global _cliente
    if _cliente is None:
        _cliente = genai.Client(api_key=require_google_api_key())
    return _cliente


def _espera_sugerida(error: ClientError, intento: int) -> float:
    """Cuánto esperar tras un 429.

    Google devuelve en el propio error cuántos segundos falta para que se libere la cuota
    ("Please retry in 52.3s"). Ignorarlo y usar un backoff exponencial propio es lo que hacía
    que los reintentos se agotaran antes de tiempo: esperábamos 30 segundos en total cuando la
    API estaba pidiendo 52. Le hacemos caso a la API y usamos el exponencial solo como respaldo.
    """
    match = re.search(r"retry in ([\d.]+)s", str(error))
    if match:
        return float(match.group(1)) + 1.0  # un segundo de gracia
    return float(2**intento)


def _con_reintento(operacion, descripcion: str):
    """Ejecuta una llamada a Gemini reintentando solo si es cuota agotada.

    El free tier tiene cuota tanto de embeddings como de generación, así que las dos llamadas
    necesitan lo mismo. Un 429 es transitorio y se arregla esperando; un 401 (key inválida) o
    un 404 (modelo inexistente) no se arreglan reintentando y tienen que explotar en el acto,
    con su mensaje original, en vez de quedar enmascarados detrás de seis reintentos inútiles.
    """
    for intento in range(1, REINTENTOS + 1):
        try:
            return operacion()
        except ClientError as e:
            if e.code != 429 or intento == REINTENTOS:
                raise
            espera = _espera_sugerida(e, intento)
            logger.warning(
                "Cuota agotada en %s (429). La API pide esperar %.0fs. Reintento %d/%d...",
                descripcion, espera, intento, REINTENTOS,
            )
            time.sleep(espera)

    raise RuntimeError(f"No se pudo completar {descripcion} tras {REINTENTOS} reintentos")


def _normalizar(vectores: np.ndarray) -> np.ndarray:
    """Lleva cada vector a norma 1.

    Imprescindible: gemini-embedding-001 solo devuelve el vector ya normalizado cuando se
    piden las 3072 dimensiones completas. Al truncar a 768 (que es lo que hacemos, para que
    el índice pese 4x menos) la salida viene SIN normalizar.

    Si no se normaliza, el producto interno que usa FAISS deja de ser la similitud coseno:
    los scores se van de escala y el umbral de "no lo sé" pierde todo significado. Es un bug
    silencioso, porque el sistema igual devuelve resultados: solo que los ordena mal y el
    umbral no filtra nada.
    """
    normas = np.linalg.norm(vectores, axis=1, keepdims=True)
    normas[normas == 0] = 1.0  # un vector nulo no se puede normalizar; lo dejamos como está
    return vectores / normas


def embed(textos: list[str], task_type: str) -> np.ndarray:
    """Convierte textos en vectores normalizados.

    Devuelve una matriz (len(textos), embedding_dim) de float32, lista para FAISS.
    """
    if not textos:
        raise ValueError("embed() recibió una lista vacía de textos")

    settings = get_settings()
    cliente = get_cliente()
    vectores: list[list[float]] = []

    lotes = [textos[i : i + TAMANIO_LOTE] for i in range(0, len(textos), TAMANIO_LOTE)]

    for numero_lote, lote in enumerate(lotes):
        # Solo hace falta ir a ritmo cuando hay varios lotes (o sea, al indexar). Una pregunta
        # suelta es un texto y no tiene por qué esperar nada.
        if numero_lote > 0:
            time.sleep(PAUSA_ENTRE_LOTES)

        respuesta = _con_reintento(
            lambda: cliente.models.embed_content(
                model=settings.embedding_model,
                contents=lote,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=settings.embedding_dim,
                ),
            ),
            descripcion=f"embeddings (lote {numero_lote + 1}/{len(lotes)})",
        )
        vectores.extend(e.values for e in respuesta.embeddings)

    matriz = np.array(vectores, dtype=np.float32)
    if matriz.shape[0] != len(textos):
        raise RuntimeError(
            f"Gemini devolvió {matriz.shape[0]} embeddings para {len(textos)} textos. "
            "El índice quedaría desalineado con los chunks."
        )

    return _normalizar(matriz)


def embed_documentos(textos: list[str]) -> np.ndarray:
    """Embeddings de los fragmentos que van al índice."""
    return embed(textos, TASK_DOCUMENTO)


def embed_pregunta(pregunta: str) -> np.ndarray:
    """Embedding de una pregunta del usuario. Devuelve un vector (1, dim)."""
    return embed([pregunta], TASK_PREGUNTA)


def generar(prompt: str, system_prompt: str) -> str:
    """Le pide una respuesta al LLM.

    temperature=0: ante la misma pregunta y el mismo contexto queremos la misma respuesta.
    En un agente que cita documentos internos, la creatividad es exactamente lo que no
    queremos — es el camino más corto a que invente.
    """
    settings = get_settings()
    cliente = get_cliente()

    respuesta = _con_reintento(
        lambda: cliente.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
            ),
        ),
        descripcion="generación de la respuesta",
    )

    texto = (respuesta.text or "").strip()
    if not texto:
        raise RuntimeError(
            "Gemini devolvió una respuesta vacía. "
            f"Motivo de corte: {getattr(respuesta.candidates[0], 'finish_reason', 'desconocido')}"
            if respuesta.candidates
            else "Gemini devolvió una respuesta vacía y sin candidatos."
        )
    return texto
