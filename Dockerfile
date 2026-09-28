FROM python:3.13.15-slim-trixie

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && groupadd --gid "${APP_GID}" pixelotheque \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home pixelotheque \
    && mkdir -p /data /media /app/staticfiles \
    && chown -R "${APP_UID}:${APP_GID}" /data /media /app

COPY --chown=${APP_UID}:${APP_GID} . /app

# Cette valeur publique sert uniquement à collectstatic. Aucun secret réel
# n'entre dans le contexte de build ni dans les couches de l'image.
RUN DJANGO_SECRET_KEY=build-only-not-a-runtime-secret-0123456789abcdefghijklmnopqrstuvwxyz \
    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
    DJANGO_DEBUG=False \
    STATIC_ROOT=/app/staticfiles \
    python manage.py collectstatic --noinput

USER ${APP_UID}:${APP_GID}
EXPOSE 8000
CMD ["gunicorn", "pixelotheque.wsgi:application", "--bind=0.0.0.0:8000", "--workers=1", "--worker-class=gthread", "--threads=2", "--timeout=60", "--graceful-timeout=30", "--worker-tmp-dir=/tmp", "--error-logfile=-", "--capture-output"]
