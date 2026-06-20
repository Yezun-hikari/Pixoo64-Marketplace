FROM python:3.11-alpine

# Install git for plugin cloning, and tk/tcl for the pixoo library simulator
RUN apk add --no-cache git tk tcl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY ui/ ui/

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
