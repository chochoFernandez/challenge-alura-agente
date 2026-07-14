"""Preguntale algo al agente desde la terminal.

Uso:
    python scripts/ask.py "¿Cuántos días de vacaciones tengo?"
    python scripts/ask.py            # modo interactivo
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import Agente

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def mostrar(agente: Agente, pregunta: str) -> None:
    respuesta = agente.responder(pregunta)

    print(f"\n\033[1m{respuesta.texto}\033[0m\n")

    if respuesta.citas:
        print("Fuentes:")
        for cita in respuesta.citas:
            print(f"  - {cita}")
    print(f"\n({respuesta.latencia_ms} ms · {respuesta.modelo})\n")


def main() -> None:
    agente = Agente()

    if len(sys.argv) > 1:
        mostrar(agente, " ".join(sys.argv[1:]))
        return

    print("Agente NovaPay. Escribí tu pregunta (Ctrl+C para salir).\n")
    while True:
        try:
            pregunta = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nChau.")
            return
        if pregunta:
            mostrar(agente, pregunta)


if __name__ == "__main__":
    main()
