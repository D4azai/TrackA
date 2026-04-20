FROM python:3.11-slim

WORKDIR /app

# Create an unprivileged user for runtime
RUN useradd -r -u 10001 -g root appuser

# Install Python dependencies (app + worker)
COPY requirements.txt requirements-worker.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-worker.txt

# Copy application and worker packages
COPY app ./app
COPY worker ./worker

ENV PYTHONUNBUFFERED=1

USER 10001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]