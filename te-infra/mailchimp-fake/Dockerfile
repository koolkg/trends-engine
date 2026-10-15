# Stand-in for Mailchimp's Marketing API. Stdlib only, so no venv or requirements.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY mailchimp_fake.py .
COPY tests/ tests/
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status == 200 else 1)"
CMD ["python", "mailchimp_fake.py"]
