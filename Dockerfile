FROM python:3.11-slim

# Install system dependencies (needed for sqlite-vss or any CLI binaries)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create our non-root caveman user
RUN useradd -m -s /bin/bash grug

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN chown -R grug:grug /app
USER grug

# The persistent brain volume
RUN mkdir -p /app/brain/daily_notes

COPY . .

# Assume the main entrypoint is app.py (the slack listener)
CMD ["python", "app.py"]
