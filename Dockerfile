# syntax=docker/dockerfile:1.7
#
# MNScr — motor editorial do Cinerie, 24/7 no Coolify.
#
# Nenhum segredo na imagem nem em variável de build. O `.env` da máquina que rodava o
# MNScr, o banco (`app.db`) e o que o pipeline grava moram no volume /data; um redeploy
# troca a imagem e não perde nada disso. `/app/.env` aponta para `/data/.env`, e o
# `load_dotenv()` de app/config.py o lê COMPLETANDO o ambiente: o que o Coolify define
# (caminhos do banco e dos rascunhos, abaixo) vence o que está no arquivo.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app

# Dependências primeiro, pelo lockfile: uma mudança de código não reinstala nada.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# Tudo que precisa sobreviver a um redeploy mora em /data. O pipeline escreve em
# `logs/` (app.log e a contagem de tokens) e em `debug/` (prompts que falharam), os dois
# relativos a /app: viram links para o volume. O código e o .venv ficam do root, só
# leitura para o processo.
RUN useradd --system --uid 10001 --home /data mnscr \
    && mkdir -p /data \
    && rm -rf /app/.env /app/logs /app/debug /app/artifacts /app/data \
    && ln -s /data/.env /app/.env \
    && ln -s /data/logs /app/logs \
    && ln -s /data/debug /app/debug \
    && chown mnscr /data \
    && chmod +x /app/docker/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    TZ=America/Sao_Paulo \
    MNSCR_DB_PATH=/data/app.db \
    MNSCR_LOCAL_DRAFT_DIR=/data/drafts

USER mnscr
VOLUME ["/data"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
