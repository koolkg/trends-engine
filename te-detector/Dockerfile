# One image for the service and its tests (make start / test-unit / test-integration).
FROM python:3.12-slim

# Deps in their own venv, even inside the image.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY detector/ detector/
COPY tests/ tests/

CMD ["python", "-m", "detector.main"]
