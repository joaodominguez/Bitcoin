FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY examples ./examples

EXPOSE 8001

CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--workers", "2", "--timeout", "120", "app.wsgi:app"]
