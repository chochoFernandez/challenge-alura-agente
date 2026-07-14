"""Construye (o reconstruye) el índice vectorial a partir de los documentos.

Es idempotente: se puede correr las veces que haga falta. Reconstruye siempre desde cero y
reemplaza el índice anterior de forma atómica, así que nunca duplica chunks ni deja el índice
a medio escribir.

Uso:
    python scripts/build_index.py
"""

import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import index
from app.config import get_settings
from app.ingest import cargar_documentos

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


def main() -> None:
    settings = get_settings()
    arranque = time.perf_counter()

    print(f"Leyendo documentos de {settings.docs_dir}")
    chunks = cargar_documentos()

    por_archivo = Counter(c.source_file for c in chunks)
    for archivo, cantidad in sorted(por_archivo.items()):
        print(f"  {archivo:28} {cantidad:>3} chunks")
    print(f"\nTotal: {len(chunks)} chunks de {len(por_archivo)} documentos")

    indice_vectorial = index.construir(chunks)
    destino = index.guardar(indice_vectorial)

    tamanio = sum(f.stat().st_size for f in destino.iterdir()) / 1024
    print(f"\nÍndice listo en {destino} ({tamanio:.0f} KB)")
    print(f"Tardó {time.perf_counter() - arranque:.1f}s")


if __name__ == "__main__":
    main()
