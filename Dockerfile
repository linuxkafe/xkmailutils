# mailutils / xkmailutils
#
# Imagem mínima. O objectivo é que `docker pull` traga o código e as
# dependências e nada mais: um atacante com acesso ao registo não deve encontrar
# um compilador de C, um git, nem as ferramentas de build do Python.
#
# Estágio final sem `pip install`: as dependências são copiadas do estágio de
# build. `pip install --no-cache-dir` e a limpeza de `__pycache__` são o que
# impede a imagem de carregar ficheiros que ninguém pediu.

FROM python:3.12-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src

# O pacote é **instalado**, não copiado. É o que garante que o `pyproject.toml`
# está correcto: se o `package-dir` estiver errado, o `pip install` falha aqui,
# no build, e não no arranque do contentor com um `ModuleNotFoundError`.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install "uvicorn[standard]>=0.23" \
    && /venv/bin/pip install --no-cache-dir .


FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="xkmailutils" \
      org.opencontainers.image.description="Gerador de assinaturas de email \
compatíveis com filtros de spam, e analisador de emails recebidos" \
      org.opencontainers.image.source="https://github.com/linuxkafe/xkmailutils" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

# `curl` é o healthcheck. `tini` como PID 1 para que SIGTERM chegue ao
# uvicorn e o contentor pare em segundos em vez de ser morto à força — sem isto,
# um `docker compose down` durante um `docker compose up` deixa a base de
# dados SQLite com um WAL por recovers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAILUTILS_ENV=production \
    MAILUTILS_PATH_PREFIX=/xkmailutils \
    MAILUTILS_DB_PATH=/data/mailutils.db \
    MAILUTILS_MEDIA_DIR=/data/media \
    MAILUTILS_TRUST_PROXY=1

# Utilizador sem privilégios. O contentor não escreve fora de `/data` nem `/tmp`,
# e um `--read-only` com tmpfs funciona.
RUN useradd --system --create-home --uid 10001 mailutils \
    && mkdir -p /data/media /app \
    && chown -R mailutils:mailutils /data /app

# O código, os templates e a folha de estilo vêm **todos** de dentro do venv,
# instalados pelo estágio de build. Copiar `src/` para `/app` parecia mais
# simples e é um erro: com `WORKDIR /app` no caminho de sistema, a cópia
# sombreava o pacote instalado e o arranque falhava com um
# `ModuleNotFoundError` para um módulo que estava lá.
#
# A única cópia do código na imagem é a de `site-packages`.
COPY --from=build /venv /venv
WORKDIR /app
COPY --chown=mailutils:mailutils README.md LICENSE ./

USER mailutils
EXPOSE 8000
VOLUME ["/data"]

# O healthcheck vai ao endereço **completo**, com o prefixo. Um healthcheck que
# acerta em `/` devolve 404 e o contentor é sempre considerado unhealthy — o
# sintoma é `docker compose ps` a dizer "unhealthy" com a aplicação a funcionar.
HEALTHCHECK --interval=30s --timeout=4s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:8000${MAILUTILS_PATH_PREFIX}/saude" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "--factory", "mailutils.main:create_app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--no-server-header"]
