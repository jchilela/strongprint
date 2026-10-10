FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

# LibreOffice (Office → PDF), Ghostscript (cobertura de tinta) e fontes comuns.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gettext ghostscript \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-dejavu-core fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /usr/local/bin/uv

WORKDIR /app

# INSTALL_DEV=1 em desenvolvimento (pytest, ruff); 0 em produção.
ARG INSTALL_DEV=1
COPY pyproject.toml ./
RUN if [ "$INSTALL_DEV" = "1" ]; then \
        uv pip install -r pyproject.toml --extra dev; \
    else \
        uv pip install -r pyproject.toml; \
    fi

COPY . .

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
