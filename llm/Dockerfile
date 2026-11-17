# Build context is llm/  ->  docker build -t mindmap-llm-worker llm

FROM python:3.12-slim AS builder
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/mindmap_llm:$PYTHONPATH"
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY mindmap_llm ./mindmap_llm
USER app
CMD ["python", "-m", "arq", "mindmap_llm.worker.settings.WorkerSettings"]
