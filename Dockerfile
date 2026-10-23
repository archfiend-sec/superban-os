# ==============================================================================
#  SUPERBAN OS v6.0 SOVEREIGN APEX // DOCKERFILE
# ==============================================================================
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HEADLESS=1 \
    DOCKER=1 \
    DEBIAN_FRONTEND=noninteractive

# Install system dependencies (FFmpeg for voice DVR, opus for VoIP, build-essential)
RUN apt-get update --fix-missing && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus-dev \
    build-essential \
    curl \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Ensure storage directories exist
RUN mkdir -p sessions /tmp

# Expose Web Mission Control port
EXPOSE 8000

# Run Web Mission Control
CMD ["python3", "web_app.py"]
