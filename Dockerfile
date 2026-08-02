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
COPY assets ./assets
RUN pip install --no-cache-dir .
COPY alembic ./alembic
COPY alembic.ini ./
# Keep verification artifacts available in the same immutable production image.
# They are only executed by an operator; the runtime command remains the API.
COPY tests ./tests
COPY scripts ./scripts
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
