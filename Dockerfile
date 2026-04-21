# To refresh: docker pull python:3.11-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim
FROM python:3.11-slim

ARG UID=1000
ARG GID=1000

# Install system dependencies (needed for sqlite-vec or any CLI binaries)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libblas3 \
    liblapack3 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Create our non-root caveman user with configurable UID/GID for host volume compatibility
RUN groupadd -g ${GID} grug && useradd -m -u ${UID} -g ${GID} -s /bin/bash grug

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The persistent brain volume
RUN mkdir -p /app/brain/daily_notes /app/brain/daily_logs /app/brain/summaries

COPY . .

# Fix ownership for the non-root user (includes mounted volume mount points)
RUN chown -R grug:grug /app

USER grug

# Pre-download embedding model as grug user so cache is accessible at runtime
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Assume the main entrypoint is app.py (the slack listener)
CMD ["python", "-u", "app.py"]
