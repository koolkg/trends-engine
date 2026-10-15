# Runs scripts/smoke_test.py in a pinned Python, independent of whatever
# python3 the host has (psycopg2-binary only ships prebuilt wheels for
# specific Python versions — a newer host Python falls back to a source
# build that needs libpq/pg_config).
FROM python:3.12-slim

# Deps go in their own venv even inside the image, isolated from the
# image's system site-packages.
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY smoke_test.py .

CMD ["python", "smoke_test.py"]
