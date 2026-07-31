FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --uid 10001 viralforge \
    && mkdir -p /viralforge-data/uploads /viralforge-data/production /viralforge-data/models \
    && chown -R viralforge:viralforge /app /viralforge-data
COPY pyproject.toml ./
COPY app ./app
COPY config ./config
RUN pip install --no-cache-dir .
COPY alembic ./alembic
COPY alembic.ini ./
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
