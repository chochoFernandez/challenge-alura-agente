"""Configuración central del agente.

Todo parámetro ajustable del sistema vive acá y se lee del .env. Ningún otro módulo
llama a os.getenv(): así hay un único lugar donde mirar qué se puede configurar, y la
app falla al arrancar (no a mitad de una consulta) si falta una credencial.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del proyecto: config.py está en app/, así que subimos un nivel.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Settings del agente, leídos de variables de entorno o del archivo .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Credenciales ---
    # Vacía por default a propósito: generar los documentos y correr los tests de ingesta
    # no tocan el LLM y no deberían exigir una key. La exigencia se hace en el momento de
    # construir el cliente de Gemini, vía require_google_api_key().
    google_api_key: str = Field(
        default="",
        description="API key de Google AI Studio (https://aistudio.google.com/apikey)",
    )

    # --- Modelos ---
    # gemini-2.5-flash y anteriores ya no se habilitan para keys nuevas (la API responde 404
    # "no longer available to new users"), así que no sirven como default.
    # gemini-3.1-flash-lite es estable (no preview) y alcanza de sobra: con temperature=0 y
    # el contexto ya recuperado, la tarea es leer y citar, no razonar de cero.
    gemini_model: str = "gemini-3.1-flash-lite"
    embedding_model: str = "gemini-embedding-001"

    # gemini-embedding-001 devuelve 3072 dimensiones por defecto. Truncamos a 768 para
    # achicar el índice: la calidad casi no baja y el archivo pesa 4x menos.
    # OJO: al truncar hay que re-normalizar el vector a mano (ver app/llm.py); solo la
    # salida de 3072 viene ya normalizada.
    embedding_dim: int = 768

    # --- Retrieval ---
    top_k: int = 4

    # Umbral de similitud coseno. Si ningún fragmento lo supera, el agente responde
    # "no encontré esta información" en vez de arriesgar una respuesta inventada.
    #
    # Calibrado con scripts/eval_questions.py, no elegido a ojo: la peor pregunta CON
    # respuesta puntúa 0.675 y la mejor pregunta SIN respuesta 0.641. 0.66 cae en el medio.
    #
    # El margen es de apenas 0.034: los embeddings de Gemini comprimen todo el texto en
    # español en una banda alta y estrecha, así que dos textos sin nada que ver rara vez
    # bajan de 0.6. Por eso este umbral no alcanza como única defensa y el prompt del agente
    # tiene que hacer su parte (ver app/agent.py).
    similarity_threshold: float = 0.66

    # --- Rutas (relativas a la raíz del proyecto) ---
    docs_dir: Path = Path("data/docs")
    index_dir: Path = Path("data/index")
    log_path: Path = Path("logs/executions.jsonl")

    # --- Chunking ---
    chunk_size: int = 800
    chunk_overlap: int = 100

    @field_validator("similarity_threshold")
    @classmethod
    def _umbral_en_rango(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"SIMILARITY_THRESHOLD debe estar entre 0 y 1, recibido: {v}")
        return v

    @field_validator("docs_dir", "index_dir", "log_path")
    @classmethod
    def _path_absoluta(cls, v: Path) -> Path:
        """Resuelve las rutas contra la raíz del proyecto.

        Así los scripts funcionan igual sin importar desde qué directorio se los invoque.
        """
        return v if v.is_absolute() else PROJECT_ROOT / v


@lru_cache
def get_settings() -> Settings:
    """Devuelve los settings (cacheados: el .env se lee una sola vez por proceso)."""
    return Settings()


def require_google_api_key() -> str:
    """Devuelve la API key, o falla con un mensaje accionable si no está configurada.

    Se llama al construir el cliente de Gemini: así el error aparece apenas se intenta usar
    el LLM (indexar o preguntar) y no a mitad de una consulta, pero sin bloquear las partes
    del proyecto que no necesitan credenciales.
    """
    key = get_settings().google_api_key.strip()
    if not key:
        raise RuntimeError(
            "GOOGLE_API_KEY no está configurada.\n"
            "  1. Copiá .env.example a .env\n"
            "  2. Conseguí tu key gratis en https://aistudio.google.com/apikey\n"
            "  3. Pegala en la línea GOOGLE_API_KEY= del .env"
        )
    return key
