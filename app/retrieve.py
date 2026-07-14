"""Capa de recuperación: dada una pregunta, devuelve los fragmentos relevantes.

Es el corazón del RAG y también la primera línea de defensa contra la alucinación: si acá no
sobrevive ningún fragmento, el agente no le pregunta nada al LLM y responde "no lo sé".
"""

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.index import IndiceVectorial
from app.index import cargar as cargar_indice
from app.ingest import Chunk
from app.llm import embed_pregunta

logger = logging.getLogger(__name__)


@dataclass
class Recuperado:
    """Un fragmento recuperado, con qué tan parecido es a la pregunta."""

    chunk: Chunk
    score: float  # similitud coseno [-1, 1]; en la práctica [0, 1] para texto

    @property
    def cita(self) -> str:
        return self.chunk.cita


class Recuperador:
    """Busca fragmentos relevantes en el índice.

    Se construye una vez (cargar el índice cuesta) y se reutiliza para todas las preguntas.
    """

    def __init__(self, indice: IndiceVectorial | None = None) -> None:
        self.indice = indice if indice is not None else cargar_indice()

    def categorias(self) -> list[str]:
        """Las categorías de negocio presentes en el índice, para ofrecerlas como filtro."""
        return sorted({c.category for c in self.indice.chunks})

    def buscar(
        self,
        pregunta: str,
        top_k: int | None = None,
        umbral: float | None = None,
        categorias: list[str] | None = None,
    ) -> list[Recuperado]:
        """Devuelve los fragmentos más relevantes que superen el umbral de similitud.

        `categorias` acota la búsqueda a ciertos dominios (RH, Financiero, etc.). Sirve cuando
        el colaborador ya sabe de qué área es su duda y no quiere ruido de las otras.

        Devolver una lista vacía es un resultado válido y esperado: significa "la respuesta a
        esto no está en los documentos". Es exactamente lo que dispara el "no lo sé".
        """
        settings = get_settings()
        top_k = top_k if top_k is not None else settings.top_k
        umbral = umbral if umbral is not None else settings.similarity_threshold

        if not pregunta.strip():
            raise ValueError("La pregunta está vacía")

        vector = embed_pregunta(pregunta)

        if categorias:
            # Se busca sobre TODO el índice y recién después se filtra. Al revés —filtrar el
            # top-4 ya calculado— daría cero resultados cada vez que los 4 fragmentos más
            # parecidos fueran de otra categoría, aunque la categoría pedida sí tuviera algo
            # relevante. Sería un filtro que además rompe la búsqueda.
            # Con ~100 chunks, recorrer el índice entero es instantáneo.
            todos = self.indice.buscar(vector, len(self.indice))
            candidatos = [(c, s) for c, s in todos if c.category in categorias][:top_k]
        else:
            candidatos = self.indice.buscar(vector, top_k)

        recuperados = [Recuperado(chunk, score) for chunk, score in candidatos if score >= umbral]

        descartados = len(candidatos) - len(recuperados)
        if descartados:
            mejor = max((s for _, s in candidatos), default=0.0)
            logger.info(
                "Descartados %d/%d fragmentos por debajo del umbral %.2f (mejor score: %.3f)",
                descartados, len(candidatos), umbral, mejor,
            )

        return recuperados

    def buscar_sin_umbral(self, pregunta: str, top_k: int | None = None) -> list[Recuperado]:
        """Igual que buscar(), pero sin filtrar por umbral.

        Sirve para calibrar: para elegir un umbral hay que poder ver los scores de las
        preguntas que SÍ tienen respuesta y los de las que no, y buscar el corte que las
        separa. Con el umbral ya aplicado, esa información no se ve.
        """
        settings = get_settings()
        top_k = top_k if top_k is not None else settings.top_k

        vector = embed_pregunta(pregunta)
        return [Recuperado(chunk, score) for chunk, score in self.indice.buscar(vector, top_k)]


def armar_contexto(recuperados: list[Recuperado]) -> str:
    """Ensambla los fragmentos en el bloque de texto que se le pasa al LLM.

    Cada fragmento va rotulado con su procedencia. El modelo necesita ver de qué documento
    sale cada dato para poder citarlo: si el contexto llegara como un texto plano sin
    etiquetas, no tendría forma de atribuir nada.
    """
    bloques = []
    for i, r in enumerate(recuperados, start=1):
        bloques.append(
            f"[Fragmento {i}]\n"
            f"Documento: {r.chunk.source_file}\n"
            f"Ubicación: {r.chunk.location}\n"
            f"Categoría: {r.chunk.category}\n"
            f"Contenido:\n{r.chunk.texto}"
        )
    return "\n\n---\n\n".join(bloques)
