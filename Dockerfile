FROM python:3.11-slim

# Install git for plugin cloning, and python3-tk for the pixoo library simulator
RUN apt-get update && apt-get install -y --no-install-recommends git python3-tk && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY ui/ ui/

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
