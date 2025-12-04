# syntax = docker/dockerfile:1

ARG PYTHON_VERSION="3.12"
ARG DEBIAN_VERSION="bullseye"

ARG BUILD_DEPS="\
  build-essential \
  python3-dev \
  gcc \
  bzip2 \
  git \
  libpq-dev \
  python3-cffi \
  curl \
  pkg-config \
  apt-utils"

ARG RUNTIME_DEPS="\
  libpq5 \
  gettext \
  libgdal-dev \
  vim \
  gosu \
  curl \
  bzip2 \
  git \
  tzdata \
  netcat-traditional"

FROM python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION} as base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PROJECT=rapidpro \
    PROJECT_PATH=/app \
    PROJECT_USER=rapidpro \
    PROJECT_GROUP=rapidpro \
    NPM_CONFIG_PREFIX=/opt/npm-globals \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/npm-globals/bin:/install/bin:${PATH}"

RUN addgroup --gid 1999 "${PROJECT_GROUP}" \
  && useradd --system -m -d "${PROJECT_PATH}" -u 1999 -g 1999 "${PROJECT_USER}" \
  && usermod -aG adm "${PROJECT_USER}"

WORKDIR "${PROJECT_PATH}"

RUN rm -f /etc/apt/apt.conf.d/docker-clean; echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

FROM base as build-assets

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y curl \
    && curl -sL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs

RUN mkdir -p ${NPM_CONFIG_PREFIX} \
    && chown -R ${PROJECT_USER}:${PROJECT_GROUP} ${PROJECT_PATH} ${NPM_CONFIG_PREFIX}

COPY . ${PROJECT_PATH}

USER "${PROJECT_USER}:${PROJECT_GROUP}"
RUN npm install --global yarn \
    && yarn global add less \
    && yarn install

FROM base as build-python

ARG BUILD_DEPS

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends --no-install-suggests -y ${BUILD_DEPS}

COPY ./pyproject.toml ./poetry.lock ./docker/pip-requires.txt ./

RUN --mount=type=cache,mode=0755,target=/pip_cache,id=pip \
    pip install --cache-dir /pip_cache -U poetry==2.0.0 \
    && poetry self add poetry-plugin-export \
    && poetry cache clear -n --all pypi \
    && poetry add -n --lock $(cat pip-requires.txt) \
    && poetry export --without-hashes --with dev --output requirements.txt

RUN --mount=type=cache,mode=0755,target=/pip_cache,id=pip \
    pip install --cache-dir /pip_cache --prefix=/install -r requirements.txt

FROM base

ARG RUNTIME_DEPS
ARG COMPRESS_ENABLED
ARG BRANDING_ENABLED

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ${RUNTIME_DEPS} \
    && rm -rf /usr/share/man /usr/share/doc

COPY --from=build-python /install /install
COPY --from=build-assets --chown=${PROJECT_USER}:${PROJECT_GROUP} ${NPM_CONFIG_PREFIX} ${NPM_CONFIG_PREFIX}

COPY --chown=${PROJECT_USER}:${PROJECT_GROUP} . ${PROJECT_PATH}

RUN ln -s ${PROJECT_PATH}/temba/settings.py.prod ${PROJECT_PATH}/temba/settings.py

USER ${PROJECT_USER}:${PROJECT_GROUP}

EXPOSE 8000
ENTRYPOINT ["bash", "./docker/start"]