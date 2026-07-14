# 🤖 Agente RAG Corporativo — NovaPay

Agente conversacional que responde preguntas de los colaboradores sobre los documentos internos de
una fintech ficticia (**NovaPay**), citando siempre la fuente y admitiendo explícitamente cuando la
información no está en la base documental.

Proyecto del **Challenge final de Alura / Oracle Next Education (ONE)**.

> 🚧 **En construcción.** Este README se completa al final del proyecto con la arquitectura, los
> ejemplos reales de preguntas y respuestas, las instrucciones de ejecución y la evidencia del
> deploy en OCI.

---

## Estado

- [ ] Documentos ficticios (6 formatos: PDF, DOCX, XLSX, CSV, JSON, Markdown)
- [ ] Pipeline RAG (ingesta → chunking → embeddings → FAISS → retrieval)
- [ ] Agente con citación de fuentes y anti-alucinación
- [ ] Interfaz Streamlit
- [ ] Logging de ejecuciones (JSONL)
- [ ] Dockerfile
- [ ] Deploy en OCI + evidencia
- [ ] README final

## Stack

| Componente | Elección |
|---|---|
| Lenguaje | Python 3.12 |
| LLM | Gemini (`gemini-2.5-flash`) |
| Embeddings | Gemini (`gemini-embedding-001`) |
| Vector store | FAISS (local, persistido en disco) |
| Interfaz | Streamlit |
| Deploy | Docker sobre OCI Compute + OCI Object Storage |

## Ejecución local (borrador)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # completá GOOGLE_API_KEY
python scripts/generate_docs.py   # genera los documentos ficticios
python scripts/build_index.py     # construye el índice vectorial
streamlit run app/interface.py
```
