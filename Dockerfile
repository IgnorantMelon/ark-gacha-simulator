FROM ghcr.io/astral-sh/uv:0.11.6-python3.11-trixie-slim@sha256:5d2a332da2ed61662ec7cdb1cb819be5d4b4d893007f580cf9fae415825f7e42

ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 arkgacha \
    && useradd --uid 10001 --gid 10001 --create-home arkgacha

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python --no-cache \
    && mkdir -p /app/data \
    && chown arkgacha:arkgacha /app/data

USER 10001:10001
ENTRYPOINT ["ark-gacha"]
CMD ["run"]
