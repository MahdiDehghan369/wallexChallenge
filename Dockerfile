# BASE_IMAGE can point at a registry mirror where Docker Hub is blocked,
# e.g. --build-arg BASE_IMAGE=docker.arvancloud.ir/python:3.11-slim
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Commit sha baked into the image (.git is not copied, so this is the only source at runtime).
# Pass it on every build: docker compose build --build-arg GIT_SHA=... / liara deploy --build-arg GIT_SHA=...
# Unset -> empty -> runtime reports "unknown".
ARG GIT_SHA
# Release tag (live-vX.Y); a runtime env VERSION_TAG still overrides it
ARG VERSION_TAG
# Optional PyPI mirror for networks where files.pythonhosted.org is unreachable
ARG PIP_INDEX_URL=
ENV GIT_SHA=$GIT_SHA \
    VERSION_TAG=$VERSION_TAG \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Optional Debian mirror host (e.g. mirror.arvancloud.ir) where deb.debian.org is slow/blocked
ARG DEBIAN_MIRROR=
# libgomp1: OpenMP runtime required by LightGBM; make: `make report` / `make export-submission` in the container
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null \
        || sed -i "s|deb.debian.org|$DEBIAN_MIRROR|g" /etc/apt/sources.list; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; fi \
    && pip install .

COPY config ./config
COPY scripts ./scripts
COPY Makefile CHANGELOG.md ./

RUN useradd --create-home --uid 1000 siavash \
    && mkdir -p logs state data models reports submission \
    && chown -R siavash:siavash /app
USER siavash

ENTRYPOINT ["siavash"]
# No --mode here: the mode comes from env MODE (then base.yaml). live still needs CONFIRM_LIVE=YES and
# risk.endgame.competition_end_utc. A literal "--mode paper" here silently overrode MODE=live.
CMD ["run"]
