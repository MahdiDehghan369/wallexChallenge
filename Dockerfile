# BASE_IMAGE can point at a registry mirror where Docker Hub is blocked,
# e.g. --build-arg BASE_IMAGE=docker.arvancloud.ir/python:3.11-slim
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ARG GIT_SHA=unknown
# Optional PyPI mirror for networks where files.pythonhosted.org is unreachable
ARG PIP_INDEX_URL=
ENV GIT_SHA=${GIT_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Optional Debian mirror host (e.g. mirror.arvancloud.ir) where deb.debian.org is slow/blocked
ARG DEBIAN_MIRROR=
# libgomp1: OpenMP runtime required by LightGBM
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; fi \
    && pip install .

COPY config ./config
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 siavash \
    && mkdir -p logs state data models reports \
    && chown -R siavash:siavash /app
USER siavash

ENTRYPOINT ["siavash"]
CMD ["run", "--mode", "paper"]
