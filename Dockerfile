FROM python:3.12-slim

# git: installing and updating plugins
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # common plugin dependencies, so plugins don't have to install them at runtime
 && pip install --no-cache-dir "pycryptodome>=3.19"

COPY pixoohub/ pixoohub/

ENV PYTHONUNBUFFERED=1 \
    PIXOOHUB_DATA=/data \
    PIXOOHUB_PORT=8000
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/state', timeout=4)" || exit 1

CMD ["python", "-m", "pixoohub"]
