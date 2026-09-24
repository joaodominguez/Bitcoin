FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY btcsim ./btcsim
COPY examples ./examples

EXPOSE 8000

# Long timeout: simulations and walk-forward call external APIs.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", \
     "--timeout", "180", "btcsim.dashboard:app"]
