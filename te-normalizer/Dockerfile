# One image for the service and its tests (make start / test-unit / test-integration).
# Since NORM-9 it carries the same model as te-cluster (all-MiniLM-L6-v2 on CPU
# PyTorch, ~90 MB of weights) for relevance + category; same install recipe.
FROM python:3.12-slim

# Deps in their own venv, even inside the image.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf
RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt .
# CPU-only torch (see te-cluster/Dockerfile for why the extra index).
RUN pip install --no-cache-dir torch==2.5.1 --extra-index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# Bake the model into the image, then forbid any further downloads at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
COPY normalizer/ normalizer/
COPY tests/ tests/
COPY scripts/ scripts/

CMD ["python", "-m", "normalizer.main"]
