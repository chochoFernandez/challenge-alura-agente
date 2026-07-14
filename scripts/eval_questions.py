"""Batería de evaluación del agente.

Hace dos cosas a la vez:

1. Verifica que el agente responda bien: acierta el documento, cita la fuente, y dice "no lo
   sé" cuando la pregunta está fuera de alcance. Es el criterio de "funciona localmente".

2. Calibra el umbral de similitud. Para elegir el corte hay que ver los scores de las
   preguntas que SÍ tienen respuesta contra los de las que no. Con el umbral ya aplicado esa
   información no se ve, así que la calibración mira los scores crudos.

La salida en markdown es la tabla de preguntas y respuestas que va al README: no hay que
rehacer el trabajo a mano después.

Uso:
    python scripts/eval_questions.py              # evalúa y calibra
    python scripts/eval_questions.py --md         # además vuelca el markdown al README
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import Agente
from app.config import get_settings
from app.retrieve import Recuperador

logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

SALIDA_MD = Path(__file__).resolve().parent.parent / "docs" / "ejemplos_qa.md"


@dataclass
class Caso:
    pregunta: str
    fuente_esperada: str | None  # None = fuera de alcance, el agente debe decir "no lo sé"
    dato_esperado: str | None = None  # texto que debería aparecer en la respuesta


# 8 preguntas respondibles (una por documento y dominio) + 2 deliberadamente fuera de alcance.
# Las fuera de alcance son tan importantes como las otras: el challenge pide probar
# explícitamente que el agente admite no saber.
CASOS = [
    Caso("¿Cuántos días de vacaciones tengo?", "politica_rrhh.pdf", "22"),
    Caso("¿Cuántos días por semana puedo trabajar desde casa?", "politica_rrhh.pdf", "3"),
    Caso("¿Cuánto cuesta extraer efectivo en un cajero de la red?", "tarifas_comisiones.csv", "850"),
    Caso("¿Cuál es el límite diario de transferencia del plan Business?", "planes_productos.json", "10.000.000"),
    Caso("¿Cuánto puede demorar una transferencia de más de dos millones de pesos?", "faq_transacciones.md", "24"),
    Caso("¿Por cuánto tiempo se conservan los datos de mis transacciones?", "politica_privacidad.docx", "10 años"),
    Caso("¿Qué plan tuvo más altas en diciembre de 2025?", "ventas_2025.xlsx", "Plus"),
    Caso("¿Cuánto presupuesto tengo para capacitarme?", "politica_rrhh.pdf", "800"),
    # --- Fuera de alcance: no hay ningún documento que las cubra ---
    Caso("¿Cuál es la política de estacionamiento de la oficina?", None),
    Caso("¿A cuánto cotiza el dólar hoy?", None),
]


def calibrar_umbral(recuperador: Recuperador) -> None:
    """Muestra los scores crudos para poder elegir un umbral con criterio."""
    print("=" * 78)
    print("CALIBRACIÓN DEL UMBRAL — mejor score de cada pregunta, sin filtrar")
    print("=" * 78)

    con_respuesta: list[float] = []
    sin_respuesta: list[float] = []

    for caso in CASOS:
        resultados = recuperador.buscar_sin_umbral(caso.pregunta, top_k=1)
        mejor = resultados[0].score if resultados else 0.0
        (con_respuesta if caso.fuente_esperada else sin_respuesta).append(mejor)

        etiqueta = "EN ALCANCE " if caso.fuente_esperada else "FUERA      "
        print(f"  {etiqueta} {mejor:.3f}  {caso.pregunta}")

    piso = min(con_respuesta)
    techo = max(sin_respuesta)

    print()
    print(f"  Peor score de una pregunta CON respuesta : {piso:.3f}")
    print(f"  Mejor score de una pregunta SIN respuesta: {techo:.3f}")
    print()

    if techo < piso:
        sugerido = (piso + techo) / 2
        print(f"  Las dos poblaciones se separan limpio. Umbral sugerido: {sugerido:.2f}")
        print(f"  (margen de {piso - techo:.3f} entre ambas)")
    else:
        print("  ⚠ Las poblaciones SE SOLAPAN: no hay un umbral que separe las dos.")
        print("    Ningún corte deja pasar todas las buenas y frena todas las malas.")
        print("    El umbral solo puede ser una primera barrera; el prompt tiene que hacer el resto.")

    print(f"\n  Umbral configurado actualmente: {get_settings().similarity_threshold}")
    print()


def evaluar(agente: Agente) -> tuple[int, int, list[str]]:
    """Corre los casos y devuelve (aciertos, total, líneas del markdown)."""
    print("=" * 78)
    print("EVALUACIÓN DEL AGENTE")
    print("=" * 78)

    aciertos = 0
    md: list[str] = [
        "# Ejemplos reales de preguntas y respuestas",
        "",
        "Salida de `python scripts/eval_questions.py`, sin editar a mano.",
        "",
    ]

    for i, caso in enumerate(CASOS, start=1):
        respuesta = agente.responder(caso.pregunta)

        if caso.fuente_esperada is None:
            # Fuera de alcance: acierta si NO respondió.
            ok = not respuesta.respondida
            detalle = "dijo «no lo sé»" if ok else "¡INVENTÓ UNA RESPUESTA!"
        else:
            cito_bien = any(caso.fuente_esperada in c for c in respuesta.citas)
            trajo_dato = caso.dato_esperado is None or caso.dato_esperado.lower() in respuesta.texto.lower()
            ok = respuesta.respondida and cito_bien and trajo_dato
            if ok:
                detalle = "OK"
            elif not respuesta.respondida:
                detalle = "dijo «no lo sé» pero SÍ estaba en los documentos"
            elif not cito_bien:
                detalle = f"citó {respuesta.citas or '[nada]'}, se esperaba {caso.fuente_esperada}"
            else:
                detalle = f"no aparece el dato esperado ({caso.dato_esperado})"

        aciertos += ok
        marca = "\033[92m✓\033[0m" if ok else "\033[91m✗\033[0m"
        alcance = "" if caso.fuente_esperada else "  [FUERA DE ALCANCE]"

        print(f"\n{marca} {i}. {caso.pregunta}{alcance}")
        print(f"   {respuesta.texto.strip()[:300]}")
        if respuesta.citas:
            print(f"   Fuentes: {', '.join(respuesta.citas)}")
        print(f"   -> {detalle}  ({respuesta.latencia_ms} ms)")

        md.append(f"### {i}. {caso.pregunta}")
        md.append("")
        if caso.fuente_esperada is None:
            md.append("> **Pregunta fuera de alcance a propósito:** ningún documento la cubre.")
            md.append("")
        md.append(respuesta.texto.strip())
        md.append("")
        if respuesta.citas:
            md.append("**Fuentes citadas:** " + ", ".join(f"`{c}`" for c in respuesta.citas))
            md.append("")

    return aciertos, len(CASOS), md


def main() -> None:
    settings = get_settings()
    recuperador = Recuperador()
    agente = Agente(recuperador)

    calibrar_umbral(recuperador)
    aciertos, total, md = evaluar(agente)

    print("\n" + "=" * 78)
    print(f"RESULTADO: {aciertos}/{total} casos correctos")
    print(f"  modelo: {settings.gemini_model} | umbral: {settings.similarity_threshold} | top_k: {settings.top_k}")
    print("=" * 78)

    if "--md" in sys.argv:
        SALIDA_MD.parent.mkdir(parents=True, exist_ok=True)
        SALIDA_MD.write_text("\n".join(md), encoding="utf-8")
        print(f"\nMarkdown escrito en {SALIDA_MD}")

    sys.exit(0 if aciertos == total else 1)


if __name__ == "__main__":
    main()
