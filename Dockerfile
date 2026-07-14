# Imagen del agente RAG de NovaPay.
#
# El índice NO se construye acá dentro. Es tentador (el contenedor arrancaría listo), pero
# obligaría a pasar la API key durante el build, y una key inyectada en build time queda
# grabada en el historial de capas de la imagen: cualquiera que tenga la imagen la puede
# recuperar. La key entra solo en runtime, como variable de entorno.
#
# En vez de eso, el índice se construye una vez en el host y se monta como volumen, o se
# construye al arrancar el contenedor (ver el README).

FROM python:3.12-slim

# Streamlit escribe su config y su cache en el home del usuario.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/app

WORKDIR /app

# Las dependencias van primero y en su propia capa: mientras requirements.txt no cambie,
# Docker reusa esta capa y no reinstala torch/faiss/streamlit en cada rebuild del código.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/
COPY data/docs/ ./data/docs/
COPY docker-entrypoint.sh .

# Usuario sin privilegios: si alguien logra ejecutar código dentro del contenedor, que no lo
# haga como root. Necesita poder escribir en data/index (el índice) y en logs (el JSONL).
RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 1000 agente \
    && mkdir -p /app/data/index /app/logs \
    && chown -R agente:agente /app
USER agente

EXPOSE 8501

# El health check es lo que le permite a OCI (o a cualquier orquestador) saber si la app está
# viva de verdad, y no solo si el proceso existe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

# El entrypoint construye el índice si falta y después levanta Streamlit escuchando en
# 0.0.0.0 (por default escucha solo en localhost y sería inalcanzable desde fuera del
# contenedor).
ENTRYPOINT ["./docker-entrypoint.sh"]
