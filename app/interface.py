"""Interfaz web del agente (Streamlit).

Deliberadamente simple: el enunciado del challenge dice explícitamente que la interfaz no
necesita diseño ni front-end profesional. Lo que sí tiene que estar, y está:

  - aviso claro de que se habla con una IA y no con una persona
  - las fuentes citadas visibles en cada respuesta
  - historial de la conversación dentro de la sesión

Uso:
    streamlit run app/interface.py
"""

import logging
import sys
from pathlib import Path

import streamlit as st

# Streamlit ejecuta este archivo como script suelto, así que la raíz del proyecto no está en
# el path y los "from app...." fallarían.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import Agente
from app.config import get_settings
from app.logging_utils import registrar_feedback

logging.getLogger("httpx").setLevel(logging.WARNING)

st.set_page_config(page_title="Agente NovaPay", page_icon="🤖", layout="centered")


@st.cache_resource
def cargar_agente() -> Agente:
    """Carga el agente una sola vez y lo comparte entre sesiones.

    Sin el cache, Streamlit reconstruiría el índice en cada interacción del usuario.
    """
    return Agente()


st.title("🤖 Agente NovaPay")
st.caption(
    "Asistente de IA sobre los documentos internos de NovaPay. "
    "**No estás hablando con una persona.** Las respuestas salen únicamente de los documentos "
    "indexados y siempre citan su fuente. Si algo no está en ellos, el agente lo dice."
)

try:
    agente = cargar_agente()
except FileNotFoundError as e:
    st.error(f"{e}")
    st.stop()
except RuntimeError as e:  # típicamente: falta la GOOGLE_API_KEY
    st.error(f"{e}")
    st.stop()

with st.sidebar:
    settings = get_settings()

    st.subheader("Filtrar por área")
    filtro = st.multiselect(
        "Acotar la búsqueda a estas áreas",
        options=agente.recuperador.categorias(),
        default=[],
        help="Vacío = busca en todos los documentos. Útil cuando ya sabés de qué área es tu duda.",
        label_visibility="collapsed",
    )
    if filtro:
        st.caption(f"Buscando solo en: {', '.join(filtro)}")

    st.divider()
    st.subheader("Base de conocimiento")

    documentos = sorted({c.source_file for c in agente.recuperador.indice.chunks})
    for doc in documentos:
        categoria = next(
            c.category for c in agente.recuperador.indice.chunks if c.source_file == doc
        )
        st.markdown(f"- `{doc}`  \n  _{categoria}_")

    st.divider()
    st.caption(
        f"{len(agente.recuperador.indice)} fragmentos indexados  \n"
        f"Modelo: `{settings.gemini_model}`  \n"
        f"Umbral de similitud: `{settings.similarity_threshold}`"
    )

    if st.button("Limpiar conversación"):
        st.session_state.historial = []
        st.rerun()

if "historial" not in st.session_state:
    st.session_state.historial = []
if "feedback_dado" not in st.session_state:
    st.session_state.feedback_dado = {}


def mostrar_fuentes(fuentes: list[dict]) -> None:
    if not fuentes:
        return
    with st.expander(f"Fuentes ({len(fuentes)})"):
        for fuente in fuentes:
            st.markdown(f"**{fuente['cita']}** · similitud {fuente['score']:.3f}")
            st.caption(fuente["texto"])


def mostrar_feedback(entrada: dict) -> None:
    """Botones 👍/👎. El voto va al log, no a la pantalla.

    El feedback negativo es la señal más útil del sistema: marca las preguntas donde el agente
    respondió mal o donde falta un documento en la base.
    """
    consulta_id = entrada["id"]
    ya_voto = st.session_state.feedback_dado.get(consulta_id)

    if ya_voto:
        st.caption("👍 Gracias por el feedback." if ya_voto == "positivo" else "👎 Anotado, gracias.")
        return

    izq, der, _ = st.columns([1, 1, 8])
    if izq.button("👍", key=f"si_{consulta_id}", help="La respuesta fue útil"):
        registrar_feedback(consulta_id, positivo=True)
        st.session_state.feedback_dado[consulta_id] = "positivo"
        st.rerun()
    if der.button("👎", key=f"no_{consulta_id}", help="La respuesta fue incorrecta o no sirvió"):
        registrar_feedback(consulta_id, positivo=False)
        st.session_state.feedback_dado[consulta_id] = "negativo"
        st.rerun()


for entrada in st.session_state.historial:
    with st.chat_message("user"):
        st.write(entrada["pregunta"])
    with st.chat_message("assistant"):
        st.write(entrada["respuesta"])
        mostrar_fuentes(entrada["fuentes"])
        st.caption(f"{entrada['latencia_ms']} ms")
        mostrar_feedback(entrada)

if pregunta := st.chat_input("Preguntá algo sobre NovaPay..."):
    with st.chat_message("user"):
        st.write(pregunta)

    with st.chat_message("assistant"):
        with st.spinner("Buscando en los documentos..."):
            try:
                respuesta = agente.responder(pregunta, categorias=filtro or None)
            except Exception as e:
                # No se silencia: el usuario ve que algo falló y qué fue, en vez de una
                # respuesta vacía o una pantalla en blanco.
                st.error(f"Falló la consulta: {e}")
                st.stop()

    st.session_state.historial.append(
        {
            "id": respuesta.id,
            "pregunta": pregunta,
            "respuesta": respuesta.texto,
            "latencia_ms": respuesta.latencia_ms,
            "fuentes": [
                {"cita": f.cita, "score": f.score, "texto": f.chunk.texto}
                for f in respuesta.fuentes
            ],
        }
    )
    # Se re-renderiza desde el historial, así la respuesta nueva se dibuja por el mismo camino
    # que las viejas y sus botones de feedback funcionan igual. Sin esto habría que duplicar
    # el bloque de arriba y los botones quedarían fuera de sincronía con el estado.
    st.rerun()
