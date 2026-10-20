FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=7860

WORKDIR /app

# Install system audio and build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libportaudio2 \
    libasound2-dev \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose Hugging Face Space default port
EXPOSE 7860

# Launch Kinesia backend dashboard server
CMD ["python", "-m", "backend.dashboard_server"]
