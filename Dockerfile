FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps --no-shell chromium \
 && rm -rf /var/lib/apt/lists/*
COPY fbevents ./fbevents
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app /data
ENV FBEVENTS_STATE=/data FBEVENTS_CONFIG=/etc/fbevents/config.toml FBEVENTS_AUTH_SEED=/etc/fbevents/auth.json
USER app
ENTRYPOINT ["python", "-m", "fbevents"]
CMD ["run"]
