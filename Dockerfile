FROM python:3.12-slim

WORKDIR /app

# Install system deps for playwright
RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers
RUN pip install playwright && playwright install --with-deps chromium

# Copy project files
COPY . .

# Create data directories
RUN mkdir -p data reports output logs

# Default command: run full pipeline
CMD ["python", "run_all.py"]
