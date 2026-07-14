#!/bin/sh
# Arranque del contenedor: asegura que exista el índice y levanta la app.
#
# El índice no viene dentro de la imagen (ver el comentario del Dockerfile: construirlo en el
# build obligaría a pasar la API key en build time, y quedaría grabada en las capas). Así que
# se construye en el primer arranque, con la key que llega como variable de entorno.
#
# Si el índice se monta como volumen, este paso se saltea y el arranque es instantáneo.

set -e

INDICE="/app/data/index/faiss.index"

if [ -f "$INDICE" ]; then
    echo "Índice encontrado en $INDICE, se reutiliza."
else
    echo "No hay índice. Construyéndolo desde los documentos (tarda ~1-2 minutos)..."
    python scripts/build_index.py
fi

# exec: streamlit reemplaza al shell y queda como PID 1, así recibe las señales de Docker y el
# contenedor puede frenarse limpio en vez de a los golpes.
exec streamlit run app/interface.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
