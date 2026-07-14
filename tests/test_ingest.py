"""Tests de la ingesta: chunking, limpieza y metadatos.

No tocan la API: la ingesta es puro procesamiento de texto y tiene que poder testearse sin
credenciales ni conexión.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingest import (
    Chunk,
    _partir_por_titulos,
    cargar_documentos,
    detectar_lineas_repetidas,
    limpiar_texto,
    partir_en_chunks,
)

DOCS = Path(__file__).resolve().parent.parent / "data" / "docs"


class TestChunking:
    def test_texto_corto_queda_en_un_solo_chunk(self):
        assert partir_en_chunks("Un texto breve.", tam=800, overlap=100) == ["Un texto breve."]

    def test_texto_vacio_no_genera_chunks(self):
        assert partir_en_chunks("   \n\n  ", tam=800, overlap=100) == []

    def test_respeta_el_tamanio_maximo(self):
        texto = "\n\n".join(f"Párrafo número {i} con contenido suficiente." for i in range(60))
        chunks = partir_en_chunks(texto, tam=300, overlap=50)

        assert len(chunks) > 1
        # Se permite un pequeño exceso: el overlap se suma a un chunk que ya estaba al límite.
        assert all(len(c) <= 300 + 50 for c in chunks)

    def test_el_overlap_no_corta_palabras(self):
        texto = "\n\n".join(f"Oración {i} de prueba con varias palabras." for i in range(40))
        chunks = partir_en_chunks(texto, tam=200, overlap=40)

        # Todo chunk que arrastra overlap tiene que empezar en una palabra entera.
        for chunk in chunks[1:]:
            primera = chunk.split()[0]
            assert primera.isalnum() or primera[0].isalpha(), f"empieza cortado: {chunk[:40]!r}"


class TestLimpieza:
    def test_colapsa_espacios_y_saltos(self):
        assert limpiar_texto("hola    mundo\n\n\n\nchau") == "hola mundo\n\nchau"

    def test_une_palabras_cortadas_con_guion(self):
        assert "transferencia" in limpiar_texto("una transfe-\nrencia inmediata")

    def test_detecta_encabezados_repetidos(self):
        paginas = [
            "NovaPay confidencial\nContenido de la página 1\nPágina 1",
            "NovaPay confidencial\nContenido de la página 2\nPágina 2",
            "NovaPay confidencial\nContenido de la página 3\nPágina 3",
        ]
        repetidas = detectar_lineas_repetidas(paginas)

        assert "NovaPay confidencial" in repetidas
        # "Página 1" y "Página 2" son la misma línea a estos efectos: el número se normaliza.
        assert "Página #" in repetidas
        assert "Contenido de la página #" not in repetidas or len(repetidas) >= 2

    def test_una_sola_pagina_no_tiene_encabezados_repetidos(self):
        # Sin repetición no hay forma de distinguir un encabezado del contenido real.
        assert detectar_lineas_repetidas(["una sola página"]) == set()


class TestSecciones:
    def test_corta_por_titulo_numerado(self):
        texto = "1. Primera\ncontenido uno\n2. Segunda\ncontenido dos"
        secciones = _partir_por_titulos(texto)

        assert [t for t, _ in secciones] == ["1. Primera", "2. Segunda"]

    def test_el_titulo_viaja_dentro_del_cuerpo(self):
        # El título le da contexto al embedding: sin él, el fragmento pierde de qué habla.
        _, cuerpo = _partir_por_titulos("3. Licencia\nTenés 22 días.")[0]
        assert "3. Licencia" in cuerpo and "22 días" in cuerpo

    def test_no_confunde_texto_que_empieza_con_numero(self):
        # "3 días por semana" no es un título: no tiene punto después del número.
        secciones = _partir_por_titulos("2. Jornada\n3 días por semana de trabajo remoto")
        assert len(secciones) == 1


class TestChunk:
    def test_el_id_es_determinista(self):
        a = Chunk(texto="mismo texto", source_file="a.pdf", category="RH", location="página 1")
        b = Chunk(texto="mismo texto", source_file="a.pdf", category="RH", location="página 1")
        assert a.chunk_id == b.chunk_id  # es la base de la idempotencia del índice

    def test_distinta_procedencia_da_distinto_id(self):
        a = Chunk(texto="mismo texto", source_file="a.pdf", category="RH", location="página 1")
        b = Chunk(texto="mismo texto", source_file="b.pdf", category="RH", location="página 1")
        assert a.chunk_id != b.chunk_id

    def test_la_cita_incluye_archivo_y_ubicacion(self):
        chunk = Chunk(texto="x", source_file="politica.pdf", category="RH", location="página 2")
        assert chunk.cita == "politica.pdf (página 2)"


@pytest.fixture(scope="module")
def chunks():
    """Los chunks de los 6 documentos reales, cargados una sola vez para todos los tests."""
    return cargar_documentos(DOCS)


@pytest.mark.skipif(not DOCS.exists(), reason="hay que correr scripts/generate_docs.py primero")
class TestDocumentosReales:
    """Sobre los 6 documentos de verdad. Sigue sin tocar la API: es solo lectura de archivos."""

    def test_lee_los_seis_formatos(self, chunks):
        extensiones = {Path(c.source_file).suffix for c in chunks}
        assert extensiones == {".pdf", ".docx", ".csv", ".json", ".md", ".xlsx"}

    def test_no_hay_ids_repetidos(self, chunks):
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_todo_chunk_tiene_procedencia(self, chunks):
        for c in chunks:
            assert c.source_file and c.location and c.category
            assert c.texto.strip()

    def test_el_pdf_queda_sin_encabezados_ni_pies(self, chunks):
        pdf = [c for c in chunks if c.source_file.endswith(".pdf")]
        assert pdf
        for c in pdf:
            assert "confidencial" not in c.texto.lower()
            assert "Página 1" not in c.texto

    def test_el_dato_clave_queda_en_su_propia_seccion(self, chunks):
        # Regresión: si "22 días hábiles" cae en el chunk del home office, su embedding queda
        # dominado por otro tema y la pregunta sobre licencias no lo recupera nunca.
        con_el_dato = [c for c in chunks if "22 días hábiles" in c.texto]
        assert len(con_el_dato) == 1
        assert "Licencia Anual Remunerada" in con_el_dato[0].location

    def test_las_filas_tabulares_repiten_los_encabezados(self, chunks):
        # Una fila sin sus columnas es basura semántica: "850" no significa nada suelto.
        csv = [c for c in chunks if c.source_file.endswith(".csv")]
        assert csv
        assert all("concepto:" in c.texto for c in csv)
