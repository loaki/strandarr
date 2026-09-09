FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock alembic.ini ./
COPY strandarr ./strandarr
COPY alembic ./alembic
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
CMD ["python", "-m", "strandarr", "worker"]
