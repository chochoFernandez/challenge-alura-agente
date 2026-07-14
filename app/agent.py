"""El agente: arma el prompt, llama al LLM y garantiza que no invente.

El anti-alucinación tiene dos capas, y son distintas a propósito:

1. Capa dura (código): si el retrieval no devuelve ningún fragmento por encima del umbral,
   el LLM NI SIQUIERA SE LLAMA. No hay forma de que invente algo si nunca se lo consulta.
   Esta capa no depende de que el modelo obedezca.

2. Capa blanda (prompt): cuando sí hay contexto, se le instruye al modelo que responda solo
   con base en él y que admita cuando no alcanza. Esta capa sí depende de que obedezca, y
   por eso no puede ser la única.

La capa dura es la que hace que la garantía sea una garantía y no una expectativa.
"""

import logging
import time
from dataclasses import dataclass, field

from app.config import get_settings
from app.llm import generar
from app.logging_utils import registrar
from app.retrieve import Recuperado, Recuperador, armar_contexto

logger = logging.getLogger(__name__)

RESPUESTA_SIN_CONTEXTO = (
    "No encontré esta información en los documentos disponibles.\n\n"
    "Puedo responder sobre políticas de RH, privacidad y protección de datos, tarifas y "
    "comisiones, planes y productos, preguntas frecuentes de transacciones, y datos de ventas "
    "de NovaPay. Si tu consulta es sobre otro tema, escribile directamente al área responsable."
)

SYSTEM_PROMPT = """\
Sos el asistente interno de NovaPay, una fintech. Respondés preguntas de los colaboradores \
basándote ÚNICAMENTE en los fragmentos de documentos internos que se te entregan como contexto.

REGLAS QUE NO PODÉS ROMPER:

1. Respondé exclusivamente con información que esté en el CONTEXTO. No uses conocimiento propio \
ni información externa, aunque estés seguro de que es correcta.

2. Si el contexto no alcanza para responder la pregunta, decilo con estas palabras exactas: \
"No encontré esta información en los documentos disponibles." No completes con suposiciones ni \
con lo que te parezca razonable. Es preferible admitir que no sabés antes que arriesgar un dato \
que el colaborador podría usar para tomar una decisión.

3. Citá SIEMPRE la fuente de cada dato que des, con el nombre del documento y su ubicación, \
tal como aparecen en el contexto. Formato: (politica_rrhh.pdf, página 1).

4. Si el contexto se contradice o hay varias versiones de un dato, decilo explícitamente en vez \
de elegir una por tu cuenta.

5. Si el contexto menciona el área responsable o su correo de contacto, incluilo cuando sea útil \
para que la persona pueda seguir la consulta por su cuenta.

6. Respondé en español rioplatense, de forma directa y breve. Primero la respuesta concreta, \
después el detalle si hace falta. Nada de preámbulos.\
"""

PROMPT_USUARIO = """\
CONTEXTO (fragmentos de documentos internos de NovaPay):

{contexto}

---

PREGUNTA DEL COLABORADOR: {pregunta}

Respondé basándote solo en el contexto de arriba, citando el documento y la ubicación de cada \
dato. Si el contexto no alcanza, decí que no encontraste la información.\
"""


@dataclass
class Respuesta:
    """La respuesta del agente, con todo lo necesario para auditarla."""

    pregunta: str
    texto: str
    fuentes: list[Recuperado] = field(default_factory=list)
    latencia_ms: int = 0
    modelo: str = ""
    # False cuando el agente no pudo responder por falta de contexto. Permite medir después
    # qué porcentaje de preguntas queda sin cubrir, que es la señal de qué documento falta.
    respondida: bool = True

    @property
    def citas(self) -> list[str]:
        """Las fuentes citadas, sin repetir, en orden de relevancia."""
        vistas: list[str] = []
        for f in self.fuentes:
            if f.cita not in vistas:
                vistas.append(f.cita)
        return vistas


class Agente:
    """Responde preguntas sobre los documentos internos, citando la fuente."""

    def __init__(self, recuperador: Recuperador | None = None, loguear: bool = True) -> None:
        self.recuperador = recuperador if recuperador is not None else Recuperador()
        # Los tests apagan el logueo para no ensuciar el archivo real de ejecuciones.
        self.loguear = loguear

    def responder(self, pregunta: str) -> Respuesta:
        respuesta = self._responder(pregunta)

        # Se loguea acá y no en cada interfaz: así la CLI, Streamlit y cualquier cosa que se
        # agregue después quedan trazadas sin tener que acordarse de hacerlo.
        if self.loguear:
            registrar(respuesta)

        return respuesta

    def _responder(self, pregunta: str) -> Respuesta:
        settings = get_settings()
        arranque = time.perf_counter()

        if not pregunta.strip():
            raise ValueError("La pregunta está vacía")

        recuperados = self.recuperador.buscar(pregunta)

        # Capa dura: sin contexto relevante, no hay llamada al LLM. Un modelo al que no se le
        # pregunta nada no puede inventar nada.
        if not recuperados:
            logger.info("Sin fragmentos sobre el umbral para: %r. No se llama al LLM.", pregunta)
            return Respuesta(
                pregunta=pregunta,
                texto=RESPUESTA_SIN_CONTEXTO,
                fuentes=[],
                latencia_ms=int((time.perf_counter() - arranque) * 1000),
                modelo=settings.gemini_model,
                respondida=False,
            )

        contexto = armar_contexto(recuperados)
        texto = generar(
            prompt=PROMPT_USUARIO.format(contexto=contexto, pregunta=pregunta),
            system_prompt=SYSTEM_PROMPT,
        )

        # El modelo puede tener contexto y aun así concluir que no alcanza para responder.
        # Ese caso también cuenta como "no respondida": es la capa blanda haciendo su trabajo.
        admitio_no_saber = "no encontré esta información" in texto.lower()

        return Respuesta(
            pregunta=pregunta,
            texto=texto,
            # Si el modelo admitió no saber, los fragmentos recuperados no respaldan ninguna
            # afirmación: mostrarlos como "fuentes" sería engañoso.
            fuentes=[] if admitio_no_saber else recuperados,
            latencia_ms=int((time.perf_counter() - arranque) * 1000),
            modelo=settings.gemini_model,
            respondida=not admitio_no_saber,
        )
