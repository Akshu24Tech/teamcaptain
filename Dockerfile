# Single always-on web service: FastAPI serves both the Inngest workflows and the
# Telegram webhook. No separate poller process is needed in webhook mode.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the application code.
COPY . .
RUN uv sync --frozen --no-dev

# Render (and most PaaS) inject $PORT; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uv run uvicorn tc.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
