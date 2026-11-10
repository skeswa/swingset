# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
FROM python:3.12.16-slim-trixie@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS dependencies
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1 TMPDIR=/tmp
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM dependencies AS runtime
ARG DEPENDENCY_IMAGE_DIGEST
ARG SOURCE_REVISION=unknown
RUN test -n "$DEPENDENCY_IMAGE_DIGEST"
ENV SWINGSET_DEPENDENCY_IMAGE=$DEPENDENCY_IMAGE_DIGEST SWINGSET_REVISION=$SOURCE_REVISION PATH=/app/.venv/bin:$PATH
LABEL org.opencontainers.image.revision=$SOURCE_REVISION io.swingset.dependency-image=$DEPENDENCY_IMAGE_DIGEST
COPY src ./src
COPY config ./config
COPY overrides ./overrides
RUN --mount=type=cache,target=/root/.cache/uv chmod -R a+rX /app/src /app/config /app/overrides && uv sync --frozen --no-dev --no-editable && useradd --create-home --uid 10001 swingset && mkdir -p /run/swingset /var/lib/swingset/checkpoints && chown swingset:swingset /run/swingset /var/lib/swingset/checkpoints
USER swingset
RUN python -c "from pathlib import Path; import os, swingset; assert os.getuid() == 10001; roots = (Path('/app/src'), Path('/app/config'), Path('/app/overrides'), Path(swingset.__file__).parent); [path.open('rb').close() for root in roots for path in root.rglob('*') if path.is_file()]"
CMD ["swingset", "--help"]

FROM dependencies AS test
ARG DEPENDENCY_IMAGE_DIGEST
ARG SOURCE_REVISION=unknown
RUN test -n "$DEPENDENCY_IMAGE_DIGEST"
ENV SWINGSET_DEPENDENCY_IMAGE=$DEPENDENCY_IMAGE_DIGEST SWINGSET_REVISION=$SOURCE_REVISION PATH=/app/.venv/bin:$PATH CI=true SWINGSET_DEV_HOME=/tmp/swingset-dev RUFF_CACHE_DIR=/tmp/ruff-cache MYPY_CACHE_DIR=/tmp/mypy-cache PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"
LABEL org.opencontainers.image.revision=$SOURCE_REVISION io.swingset.dependency-image=$DEPENDENCY_IMAGE_DIGEST
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-editable && useradd --create-home --uid 10001 swingset
USER swingset
CMD ["python", "-m", "swingset.dev.commands", "check"]
