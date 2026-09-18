# syntax=docker/dockerfile:1

FROM python:3.12-trixie AS build-poetry

WORKDIR /app

COPY ./pyproject.toml .
COPY ./poetry.lock .
COPY ./docker/pip-requires.txt .

RUN python -m pip install -U "poetry>=2.1,<3" \
 && poetry self add "poetry-plugin-export>=1.10,<2" \
 && poetry cache clear -n --all pypi \
 && poetry add -n --lock $(cat pip-requires.txt) \
 && poetry export --without-hashes --with dev --output requirements.txt


FROM python:3.12-trixie AS base

ARG PROJECT_USER=rapidpro
ARG PROJECT_GROUP=rapidpro

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PROJECT=rapidpro \
    PROJECT_PATH=/app \
    PROJECT_USER=${PROJECT_USER} \
    PROJECT_GROUP=${PROJECT_GROUP} \
    PROJECT_CONF=/app-conf \
    NPM_CONFIG_PREFIX=/opt/npm-globals \
    PATH="/opt/npm-globals/bin:${PATH}" \
    APP_PORT=8001

RUN addgroup --gid 1999 "${PROJECT_GROUP}" \
 && useradd --system \
    --create-home \
    --home-dir "${PROJECT_PATH}" \
    --uid 1999 \
    --gid 1999 \
    "${PROJECT_USER}"

WORKDIR "${PROJECT_PATH}"

RUN rm -f /etc/apt/apt.conf.d/docker-clean \
 && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
    > /etc/apt/apt.conf.d/keep-cache


FROM base AS build

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
 && apt-get install --no-install-recommends --no-install-suggests -y \
    gcc \
    bzip2 \
    git \
    curl \
    libpq-dev \
    gettext \
    libgdal-dev \
    python3-cffi \
    python3-gdal \
    vim

RUN curl -sL https://deb.nodesource.com/setup_20.x | bash -

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get install --no-install-recommends --no-install-suggests -y \
    nodejs

COPY --from=build-poetry /app/requirements.txt /tmp/dep/requirements.txt
COPY ./docker/pip-freeze.txt /tmp/dep/pip-freeze.txt

RUN --mount=type=cache,mode=0755,target=/pip_cache,id=pip \
    pip install \
    --cache-dir /pip_cache \
    --prefix=/install \
    -r /tmp/dep/requirements.txt

COPY ./package.json ./yarn.lock ./

RUN --mount=type=cache,id=npm,target=/npm_cache \
    npm install --global --cache /npm_cache yarn less

RUN yarn install


FROM base

ARG RUNTIME_DEPS="\
    gosu \
    libpq-dev \
    gettext \
    libgdal-dev \
    python3-cffi \
    python3-gdal \
    nodejs"

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
 && apt-get install --no-install-recommends --no-install-suggests -y \
    ${RUNTIME_DEPS} \
 && rm -rf /usr/share/man /usr/share/doc

COPY --from=build /install /usr/local

COPY --from=build /opt/npm-globals /opt/npm-globals

COPY --from=build --chown=${PROJECT_USER}:${PROJECT_GROUP} \
    ${PROJECT_PATH}/node_modules \
    ${PROJECT_PATH}/node_modules

COPY --chown=${PROJECT_USER}:${PROJECT_GROUP} . ${PROJECT_PATH}

RUN mkdir -p "${PROJECT_CONF}" \
 && chown "${PROJECT_USER}:${PROJECT_GROUP}" "${PROJECT_CONF}"

WORKDIR "${PROJECT_PATH}"

USER "${PROJECT_USER}:${PROJECT_GROUP}"

EXPOSE 8001

ENTRYPOINT ["bash", "./docker/start"]

CMD ["start"]