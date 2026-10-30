FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && apt-get clean \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin kokpit \
    && mkdir /data \
    && chown kokpit:kokpit /data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY kokpit ./kokpit
COPY docker/entrypoint.sh /usr/local/bin/kokpit-entrypoint
RUN pip install --no-cache-dir . \
    && chmod +x /usr/local/bin/kokpit-entrypoint

ENV KOKPIT_CONFIG=/data/kokpit.toml \
    KOKPIT_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
USER kokpit
VOLUME /data
EXPOSE 8501
ENTRYPOINT ["kokpit-entrypoint"]
CMD ["python", "-m", "kokpit", "run"]
