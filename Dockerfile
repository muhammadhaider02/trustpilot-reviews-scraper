# NOTE: not yet built or tested. Written for the hosting step once local testing passes.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Installs the Camoufox/Playwright browser plus its system libraries. Needs root, hence before USER.
RUN uv run scrapling install

RUN useradd --create-home scraper && chown -R scraper:scraper /app /root
USER scraper

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["uv", "run", "--no-sync", "trustpilot-reviews", "serve"]
