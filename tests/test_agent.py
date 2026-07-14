"""Tests del agente: que cite las fuentes y que no invente.

El LLM y el retriever van mockeados. Dos razones: los tests tienen que correr sin API key ni
red, y una respuesta real del modelo cambia entre corridas, así que no sirve para afirmar nada.

Lo que se testea acá es la lógica del agente, que es lo nuestro: cuándo llama al LLM, cuándo no,
y qué hace con lo que recibe.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import RESPUESTA_SIN_CONTEXTO, Agente
from app.ingest import Chunk
from app.retrieve import Recuperado


class RecuperadorFalso:
    """Devuelve lo que se le diga, sin índice ni embeddings."""

    def __init__(self, resultados: list[Recuperado]) -> None:
        self.resultados = resultados
        self.llamadas = 0
        self.categorias_recibidas: list[str] | None = None

    def buscar(self, pregunta: str, top_k=None, umbral=None, categorias=None) -> list[Recuperado]:
        self.llamadas += 1
        self.categorias_recibidas = categorias
        return self.resultados


def chunk_de_prueba(texto="Tenés 22 días hábiles de licencia.") -> Chunk:
    return Chunk(
        texto=texto,
        source_file="politica_rrhh.pdf",
        category="Recursos Humanos",
        location="página 1, sección «3. Licencia Anual Remunerada»",
    )


@pytest.fixture
def agente_sin_contexto():
    # loguear=False: un test no tiene por qué ensuciar el log real de ejecuciones.
    return Agente(RecuperadorFalso([]), loguear=False)


@pytest.fixture
def agente_con_contexto():
    return Agente(RecuperadorFalso([Recuperado(chunk_de_prueba(), score=0.78)]), loguear=False)


class TestSinContexto:
    """La capa dura: sin fragmentos relevantes, el LLM no se llama."""

    def test_no_llama_al_llm(self, agente_sin_contexto):
        # Es LA garantía anti-alucinación: un modelo al que no se le pregunta nada no puede
        # inventar nada. No depende de que el prompt sea bueno ni de que el modelo obedezca.
        with patch("app.agent.generar") as llm:
            agente_sin_contexto.responder("¿Cuál es la política de estacionamiento?")
            llm.assert_not_called()

    def test_responde_que_no_encontro_la_informacion(self, agente_sin_contexto):
        respuesta = agente_sin_contexto.responder("¿A cuánto está el dólar?")

        assert respuesta.texto == RESPUESTA_SIN_CONTEXTO
        assert "no encontré esta información" in respuesta.texto.lower()

    def test_queda_marcada_como_no_respondida(self, agente_sin_contexto):
        respuesta = agente_sin_contexto.responder("¿A cuánto está el dólar?")

        assert respuesta.respondida is False
        assert respuesta.fuentes == []
        assert respuesta.citas == []


class TestConContexto:
    def test_llama_al_llm_con_el_contexto_recuperado(self, agente_con_contexto):
        with patch("app.agent.generar", return_value="Son 22 días.") as llm:
            agente_con_contexto.responder("¿Cuántos días de vacaciones tengo?")

            prompt = llm.call_args.kwargs["prompt"]
            assert "22 días hábiles" in prompt  # el fragmento llegó
            assert "politica_rrhh.pdf" in prompt  # y su procedencia también

    def test_el_system_prompt_prohibe_el_conocimiento_externo(self, agente_con_contexto):
        with patch("app.agent.generar", return_value="Son 22 días.") as llm:
            agente_con_contexto.responder("¿Cuántos días de vacaciones tengo?")

            system = llm.call_args.kwargs["system_prompt"].lower()
            assert "únicamente" in system or "exclusivamente" in system
            assert "no encontré esta información" in system

    def test_devuelve_las_fuentes(self, agente_con_contexto):
        with patch("app.agent.generar", return_value="Son 22 días hábiles."):
            respuesta = agente_con_contexto.responder("¿Cuántos días de vacaciones tengo?")

        assert respuesta.respondida is True
        assert respuesta.citas == [
            "politica_rrhh.pdf (página 1, sección «3. Licencia Anual Remunerada»)"
        ]

    def test_si_el_modelo_admite_no_saber_no_se_muestran_fuentes(self, agente_con_contexto):
        # La capa blanda: hubo contexto y se llamó al LLM, pero el modelo concluyó que no
        # alcanzaba. Mostrar fuentes ahí sería engañoso: no respaldan ninguna afirmación.
        with patch("app.agent.generar", return_value="No encontré esta información en los documentos."):
            respuesta = agente_con_contexto.responder("¿Cuántos empleados hay?")

        assert respuesta.respondida is False
        assert respuesta.fuentes == []

    def test_las_citas_no_se_repiten(self):
        # Dos fragmentos del mismo lugar tienen que citarse una sola vez.
        repetidos = [
            Recuperado(chunk_de_prueba("primer fragmento"), score=0.8),
            Recuperado(chunk_de_prueba("segundo fragmento"), score=0.7),
        ]
        agente = Agente(RecuperadorFalso(repetidos), loguear=False)

        with patch("app.agent.generar", return_value="Respuesta."):
            respuesta = agente.responder("¿Cuántos días de licencia?")

        assert len(respuesta.citas) == 1


class TestFiltroPorCategoria:
    def test_el_filtro_llega_al_recuperador(self):
        recuperador = RecuperadorFalso([Recuperado(chunk_de_prueba(), score=0.78)])
        agente = Agente(recuperador, loguear=False)

        with patch("app.agent.generar", return_value="Son 22 días."):
            agente.responder("¿Cuántos días?", categorias=["Recursos Humanos"])

        assert recuperador.categorias_recibidas == ["Recursos Humanos"]

    def test_sin_filtro_no_se_acota_la_busqueda(self, agente_con_contexto):
        with patch("app.agent.generar", return_value="Son 22 días."):
            agente_con_contexto.responder("¿Cuántos días?")

        assert agente_con_contexto.recuperador.categorias_recibidas is None


class TestIdDeConsulta:
    def test_cada_respuesta_tiene_un_id_unico(self, agente_sin_contexto):
        # El id es lo que después enlaza el feedback 👍/👎 con la respuesta que lo motivó.
        a = agente_sin_contexto.responder("¿Algo?")
        b = agente_sin_contexto.responder("¿Otra cosa?")

        assert a.id and b.id
        assert a.id != b.id


class TestValidaciones:
    def test_una_pregunta_vacia_es_un_error(self, agente_con_contexto):
        with pytest.raises(ValueError, match="vacía"):
            agente_con_contexto.responder("   ")
