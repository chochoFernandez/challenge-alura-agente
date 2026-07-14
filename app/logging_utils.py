"""Registro de ejecución en JSON Lines.

Requisito explícito del challenge (etapa 8): dejar trazabilidad de qué se preguntó, qué
contexto se recuperó, qué se respondió y cuánto tardó — tanto en local como en la nube.

Se usa JSON Lines (una línea = un JSON = una consulta) porque se puede ir agregando al final
del archivo sin reescribirlo, sobrevive a que el proceso muera a mitad de escritura, y se lee
con herramientas comunes. Un JSON "normal" con un array habría que reescribirlo entero en cada
consulta.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:  # evita un import circular: agent ya importa este módulo
    from app.agent import Respuesta

logger = logging.getLogger(__name__)


def registrar(respuesta: "Respuesta", ruta: Path | None = None) -> None:
    """Registra una consulta: qué se preguntó, qué contexto se usó y qué se respondió."""
    registro = {
        "tipo": "consulta",
        "id": respuesta.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pregunta": respuesta.pregunta,
        "respuesta": respuesta.texto,
        "respondida": respuesta.respondida,
        "modelo": respuesta.modelo,
        "latencia_ms": respuesta.latencia_ms,
        # El contexto recuperado, no solo las citas: para auditar por qué el agente respondió
        # lo que respondió hace falta ver qué fragmentos tuvo delante y con qué score.
        "contexto_recuperado": [
            {
                "chunk_id": f.chunk.chunk_id,
                "documento": f.chunk.source_file,
                "ubicacion": f.chunk.location,
                "categoria": f.chunk.category,
                "score": round(f.score, 4),
            }
            for f in respuesta.fuentes
        ],
    }

    _escribir(registro, ruta)


def registrar_feedback(consulta_id: str, positivo: bool, ruta: Path | None = None) -> None:
    """Registra el 👍/👎 que el colaborador le dio a una respuesta.

    Se agrega como una línea NUEVA en vez de modificar la línea de la consulta original. Es a
    propósito: el log es append-only, así se puede escribir sin releer ni reescribir el
    archivo, y no hay forma de corromper lo ya registrado. Para cruzarlo, se usa el
    consulta_id.

    El feedback negativo es la señal más valiosa que puede dar el sistema: marca las preguntas
    donde el agente respondió mal o donde falta un documento en la base.
    """
    _escribir(
        {
            "tipo": "feedback",
            "consulta_id": consulta_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "valor": "positivo" if positivo else "negativo",
        },
        ruta,
    )


def _escribir(registro: dict, ruta: Path | None = None) -> None:
    """Agrega una línea al log.

    Que falle el logueo no puede tumbar la respuesta al usuario: si el disco está lleno o el
    path es de solo lectura (pasa dentro de un contenedor), se deja constancia del problema y
    se sigue. Lo que NO se hace es silenciar el error.
    """
    ruta = ruta or get_settings().log_path

    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("No se pudo escribir en el log de ejecución %s: %s", ruta, e)


def leer(ruta: Path | None = None) -> list[dict]:
    """Lee el log completo. Sirve para inspeccionarlo o para un dashboard simple."""
    settings = get_settings()
    ruta = ruta or settings.log_path

    if not ruta.exists():
        return []

    registros = []
    for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), start=1):
        linea = linea.strip()
        if not linea:
            continue
        try:
            registros.append(json.loads(linea))
        except json.JSONDecodeError as e:
            # Una línea corrupta (por ejemplo, si el proceso murió a mitad de escritura) no
            # tiene por qué invalidar todo el archivo. Se avisa y se sigue con el resto.
            logger.warning("Línea %d del log ilegible, se ignora: %s", numero, e)

    return registros
