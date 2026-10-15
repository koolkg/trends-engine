# The e2e suite runs in its own container, in te-deployment's compose project.
FROM python:3.12-slim

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY e2e/ e2e/
COPY test_*.py ./

CMD ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"]
