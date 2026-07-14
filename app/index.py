"""Índice vectorial FAISS: construcción, persistencia y carga.

El índice guarda los vectores; el .json de al lado guarda los chunks con sus metadatos. La
posición i del índice se corresponde con el chunk i del json — de esa correspondencia depende
que la cita de la fuente sea la correcta, así que se valida al cargar.
"""

import json
import logging
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

import faiss
import numpy as np

from app.config import get_settings
from app.ingest import Chunk, cargar_documentos
from app.llm import embed_documentos

logger = logging.getLogger(__name__)

ARCHIVO_INDICE = "faiss.index"
ARCHIVO_CHUNKS = "chunks.json"


class IndiceVectorial:
    """Un índice FAISS junto con los chunks que representa."""

    def __init__(self, indice: faiss.Index, chunks: list[Chunk]) -> None:
        if indice.ntotal != len(chunks):
            raise ValueError(
                f"El índice tiene {indice.ntotal} vectores pero hay {len(chunks)} chunks. "
                "Están desalineados: las citas de fuente apuntarían al documento equivocado."
            )
        self.indice = indice
        self.chunks = chunks

    def __len__(self) -> int:
        return len(self.chunks)

    def buscar(self, vector: np.ndarray, top_k: int) -> list[tuple[Chunk, float]]:
        """Devuelve los top_k chunks más parecidos, con su score de similitud coseno."""
        scores, posiciones = self.indice.search(vector, min(top_k, len(self.chunks)))

        resultados: list[tuple[Chunk, float]] = []
        for posicion, score in zip(posiciones[0], scores[0]):
            if posicion == -1:  # FAISS rellena con -1 si pide más vecinos de los que hay
                continue
            resultados.append((self.chunks[posicion], float(score)))
        return resultados


def construir(chunks: list[Chunk] | None = None) -> IndiceVectorial:
    """Genera los embeddings de todos los chunks y arma el índice."""
    settings = get_settings()
    chunks = chunks if chunks is not None else cargar_documentos()

    logger.info("Generando embeddings de %d chunks con %s...", len(chunks), settings.embedding_model)
    vectores = embed_documentos([c.texto for c in chunks])

    # IndexFlatIP = producto interno. Sobre vectores normalizados (app/llm.py se encarga),
    # el producto interno ES la similitud coseno, que es lo que queremos medir.
    # "Flat" significa comparación exhaustiva contra todos los vectores: con ~100 chunks es
    # instantáneo y da resultados exactos. Un índice aproximado tipo HNSW recién se justifica
    # con cientos de miles de vectores, y a cambio pierde exactitud.
    indice = faiss.IndexFlatIP(settings.embedding_dim)
    indice.add(vectores)

    logger.info("Índice construido: %d vectores de %d dimensiones", indice.ntotal, settings.embedding_dim)
    return IndiceVectorial(indice, chunks)


def guardar(indice_vectorial: IndiceVectorial, destino: Path | None = None) -> Path:
    """Persiste el índice en disco de forma atómica.

    Se escribe primero en un directorio temporal y recién al final se reemplaza el definitivo.
    Así, si algo falla a mitad de camino, el índice viejo sigue intacto y usable en vez de
    quedar medio escrito y corrupto. Es lo que hace seguro correr build_index.py mil veces.
    """
    settings = get_settings()
    destino = destino or settings.index_dir
    destino.parent.mkdir(parents=True, exist_ok=True)

    temporal = Path(tempfile.mkdtemp(prefix=".index_tmp_", dir=destino.parent))
    try:
        faiss.write_index(indice_vectorial.indice, str(temporal / ARCHIVO_INDICE))
        (temporal / ARCHIVO_CHUNKS).write_text(
            json.dumps([asdict(c) for c in indice_vectorial.chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if destino.exists():
            shutil.rmtree(destino)
        temporal.rename(destino)
    except Exception:
        shutil.rmtree(temporal, ignore_errors=True)
        raise

    logger.info("Índice guardado en %s", destino)
    return destino


def cargar(origen: Path | None = None) -> IndiceVectorial:
    """Carga el índice desde disco."""
    settings = get_settings()
    origen = origen or settings.index_dir

    archivo_indice = origen / ARCHIVO_INDICE
    archivo_chunks = origen / ARCHIVO_CHUNKS

    if not archivo_indice.exists() or not archivo_chunks.exists():
        raise FileNotFoundError(
            f"No hay un índice en {origen}.\n"
            "Construilo con: python scripts/build_index.py"
        )

    indice = faiss.read_index(str(archivo_indice))
    datos = json.loads(archivo_chunks.read_text(encoding="utf-8"))
    chunks = [Chunk(**d) for d in datos]

    if indice.d != settings.embedding_dim:
        raise ValueError(
            f"El índice guardado tiene {indice.d} dimensiones pero la configuración actual "
            f"espera {settings.embedding_dim} (EMBEDDING_DIM). Reconstruí el índice: "
            "python scripts/build_index.py"
        )

    # El constructor revalida que vectores y chunks estén alineados.
    return IndiceVectorial(indice, chunks)
