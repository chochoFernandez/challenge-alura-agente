"""Cliente de Gemini: embeddings y generación de texto.

Único módulo que conoce al proveedor. Si mañana hay que cambiar Gemini por otro LLM, se
reescribe este archivo y nada más: ni el índice, ni el retrieval, ni el agente saben qué
modelo hay detrás.
"""

import logging
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

# El free tier de Gemini limita las requests por minuto. Embebemos de a lotes y reintentamos
# con espera creciente si nos frena.
TAMANIO_LOTE = 32
REINTENTOS = 5


_cliente: genai.Client | None = None


def get_cliente() -> genai.Client:
    """Devuelve el cliente de Gemini, creándolo una sola vez por proceso."""
    global _cliente
    if _cliente is None:
        _cliente = genai.Client(api_key=require_google_api_key())
    return _cliente


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

    for inicio in range(0, len(textos), TAMANIO_LOTE):
        lote = textos[inicio : inicio + TAMANIO_LOTE]

        for intento in range(1, REINTENTOS + 1):
            try:
                respuesta = cliente.models.embed_content(
                    model=settings.embedding_model,
                    contents=lote,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=settings.embedding_dim,
                    ),
                )
                vectores.extend(e.values for e in respuesta.embeddings)
                break

            except ClientError as e:
                # 429 = nos pasamos de la cuota por minuto del free tier. Es esperable al
                # indexar y se resuelve esperando; cualquier otro error del cliente (401 por
                # key inválida, 400 por request mal armada) no se arregla reintentando.
                if e.code != 429 or intento == REINTENTOS:
                    raise
                espera = 2**intento
                logger.warning(
                    "Límite de cuota alcanzado (429). Reintento %d/%d en %ds...",
                    intento, REINTENTOS, espera,
                )
                time.sleep(espera)

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
    respuesta = get_cliente().models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,
        ),
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
