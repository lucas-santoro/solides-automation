FROM ghcr.io/astral-sh/uv:0.12.18-python3.12-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

# Chromium headless e as bibliotecas do sistema, na versão do pacote playwright do uv.lock
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

COPY src ./src
RUN uv sync --locked --no-dev
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 app && mkdir -p /app/artifacts && chown app /app/artifacts
USER app

ENV SOLIDES_MCP_HOST=0.0.0.0 \
    SOLIDES_MCP_PORT=8000 \
    SOLIDES_HEADLESS=true \
    SOLIDES_ARTIFACTS_DIR=/app/artifacts
EXPOSE 8000
CMD ["solides-mcp"]
