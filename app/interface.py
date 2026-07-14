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

for entrada in st.session_state.historial:
    with st.chat_message("user"):
        st.write(entrada["pregunta"])
    with st.chat_message("assistant"):
        st.write(entrada["respuesta"])
        if entrada["fuentes"]:
            with st.expander(f"Fuentes ({len(entrada['fuentes'])})"):
                for fuente in entrada["fuentes"]:
                    st.markdown(f"**{fuente['cita']}** · similitud {fuente['score']:.3f}")
                    st.caption(fuente["texto"])
        st.caption(f"{entrada['latencia_ms']} ms")

if pregunta := st.chat_input("Preguntá algo sobre NovaPay..."):
    with st.chat_message("user"):
        st.write(pregunta)

    with st.chat_message("assistant"):
        with st.spinner("Buscando en los documentos..."):
            try:
                respuesta = agente.responder(pregunta)
            except Exception as e:
                # No se silencia: el usuario ve que algo falló y qué fue, en vez de una
                # respuesta vacía o una pantalla en blanco.
                st.error(f"Falló la consulta: {e}")
                st.stop()

        st.write(respuesta.texto)

        if respuesta.fuentes:
            with st.expander(f"Fuentes ({len(respuesta.citas)})"):
                for f in respuesta.fuentes:
                    st.markdown(f"**{f.cita}** · similitud {f.score:.3f}")
                    st.caption(f.chunk.texto)
        st.caption(f"{respuesta.latencia_ms} ms")

    st.session_state.historial.append(
        {
            "pregunta": pregunta,
            "respuesta": respuesta.texto,
            "latencia_ms": respuesta.latencia_ms,
            "fuentes": [
                {"cita": f.cita, "score": f.score, "texto": f.chunk.texto}
                for f in respuesta.fuentes
            ],
        }
    )
