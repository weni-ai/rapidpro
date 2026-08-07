# syntax = docker/dockerfile:1

ARG PYTHON_VERSION="3.12"
ARG DEBIAN_VERSION="bookworm"
ARG POETRY_VERSION="2.1.3"

ARG BUILD_DEPS="\
  gcc bzip2 git curl libpq-dev gettext libffi-dev \
  libgdal-dev python3-cffi python3-gdal \
  python3-dev build-essential \
  cmake \
  autoconf pkg-config libtool automake \
  ccache \
  libmagic-dev"

ARG RUNTIME_DEPS="\
  git \
  tzdata \
  postgresql-client \
  netcat-traditional \
  curl \
  gosu \
  gdal-bin \
  libmagic1 \
  npm"

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION} AS base

ARG POETRY_VERSION

ENV PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  DEBIAN_FRONTEND=noninteractive \
  PROJECT=rapidpro \
  PROJECT_PATH=/app \
  PROJECT_USER=rapidpro \
  PROJECT_GROUP=rapidpro \
  NPM_CONFIG_PREFIX=/opt/npm-globals \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PATH="/opt/npm-globals/bin:/app/node_modules/.bin/:${PATH}:/install/bin" \
  PYTHONIOENCODING=UTF-8

ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

ARG RAPIDPRO_APPS_GIT_URL
ARG RAPIDPRO_APPS_GIT_BRANCH

LABEL app="rapidpro" \
  os="debian" \
  os.version="12" \
  name="RapidPro" \
  description="APP image" \
  maintainer="https://github.com/weni-ai" \
  org.opencontainers.image.url="https://github.com/weni-ai/rapidpro" \
  org.opencontainers.image.documentation="https://github.com/weni-ai/rapidpro" \
  org.opencontainers.image.source="https://github.com/weni-ai/rapidpro" \
  org.opencontainers.image.title="RapidPro"

RUN addgroup --gid 1999 "${PROJECT_GROUP}" \
  && useradd --system -m -d /app -u 1999 -g 1999 "${PROJECT_USER}"

WORKDIR "${PROJECT_PATH}"

RUN rm -f /etc/apt/apt.conf.d/docker-clean; echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

FROM base AS build-poetry

ARG POETRY_VERSION

COPY pyproject.toml poetry.lock docker/pip-requires.txt ./

RUN --mount=type=cache,mode=0755,target=/pip_cache,id=pip \
  pip install --cache-dir /pip_cache -U "poetry==${POETRY_VERSION}" "poetry-plugin-export>=1.10,<2" \
  && poetry cache clear -n --all pypi \
  && poetry add -n --lock $(cat pip-requires.txt) \
  && poetry export --without-hashes --with dev --output requirements.txt

FROM base AS build

ARG BUILD_DEPS

ENV CC="ccache gcc" CXX="ccache g++" PATH="/usr/lib/ccache:${PATH}"

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt,sharing=locked \
  apt-get update \
  && apt-get install --no-install-recommends --no-install-suggests -y ${BUILD_DEPS}

COPY --from=build-poetry /app/requirements.txt /tmp/dep/

RUN --mount=type=cache,mode=0755,target=/pip_cache,id=pip \
  --mount=type=cache,mode=0755,target=/root/.cache/ccache,id=ccache \
  pip install --cache-dir /pip_cache -U pip setuptools wheel \
  && pip install --cache-dir /pip_cache --prefer-binary --prefix=/install -r /tmp/dep/requirements.txt

FROM base

ARG BUILD_DEPS
ARG RUNTIME_DEPS

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt,sharing=locked \
  apt-get update \
  && apt-get install -y --no-install-recommends ${RUNTIME_DEPS} \
  && rm -rf /usr/share/man /usr/share/doc \
  && mkdir -p "${NPM_CONFIG_PREFIX}" \
  && chown -R "${PROJECT_USER}:${PROJECT_GROUP}" "${NPM_CONFIG_PREFIX}"

COPY --chown=${PROJECT_USER}:${PROJECT_GROUP} package.json yarn.lock ${PROJECT_PATH}/

RUN --mount=type=cache,id=npm,target=/npm_cache \
  --mount=type=cache,id=yarn,target=/yarn_cache \
  npm install --global --ignore-scripts --prefer-offline --cache /npm_cache \
    less \
    yarn \
  && yarn install --frozen-lockfile --cache-folder /yarn_cache \
  && chown -R "${PROJECT_USER}:${PROJECT_GROUP}" "${PROJECT_PATH}/node_modules" "${NPM_CONFIG_PREFIX}"

COPY --from=build /install /usr/local
COPY --chown=${PROJECT_USER}:${PROJECT_GROUP} . ${PROJECT_PATH}

RUN ln -s "${PROJECT_PATH}/temba/settings.py.prod" "${PROJECT_PATH}/temba/settings.py"

USER "${PROJECT_USER}:${PROJECT_GROUP}"

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -f http://127.0.0.1:8001/ || exit 1

ENTRYPOINT ["bash", "./docker/start"]
CMD ["start"]
