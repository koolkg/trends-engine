# One image for the service and its tests. Heavier than the other services:
# CPU PyTorch + sentence-transformers + the all-MiniLM-L6-v2 weights (~90 MB),
# downloaded at build time so the container runs offline.
FROM python:3.12-slim

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hf
RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt .
# CPU-only torch. On arm64 (your Mac's colima VM) PyPI's torch is already CPU-only;
# the extra index supplies the CPU build on x86_64 and is skipped if unreachable.
RUN pip install --no-cache-dir torch==2.5.1 --extra-index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

# Bake the model into the image, then forbid any further downloads at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY cluster/ cluster/
COPY tests/ tests/
COPY scripts/ scripts/

CMD ["python", "-m", "cluster.main"]
