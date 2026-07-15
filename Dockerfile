# OpenMontage - Production Docker Image
# Builds a container with Python backend + Node.js runtime for Remotion/HyperFrames.

FROM python:3.11-slim-bookworm AS base

# Install system dependencies: Node.js 22, FFmpeg, git, curl, build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22.x (required by HyperFrames)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN node --version && npm --version && ffmpeg -version

WORKDIR /app

# Copy dependency manifests first for better layer caching
COPY requirements.txt .
COPY api/requirements-mcp.txt ./api/requirements-mcp.txt
COPY remotion-composer/package.json ./remotion-composer/package.json
COPY setup.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt \
    -r api/requirements-mcp.txt \
    && pip install --no-cache-dir fastapi uvicorn python-multipart

# Install Remotion composer dependencies
RUN cd remotion-composer && npm install

# Warm the HyperFrames npx cache so first render isn't slow
# Create an npm cache directory owned by the runtime user to avoid EACCES
ENV NPM_CONFIG_CACHE=/app/.npm
RUN mkdir -p /app/.npm && npm install -g hyperframes && npx --yes hyperframes --version || echo "[warn] hyperframes cache-warm failed; will fetch on demand"

# Copy application code
COPY --chown=openmontage:openmontage . .

# Install the openmontage package in editable mode so imports resolve
RUN pip install --no-cache-dir -e .

# Create runtime directories with open permissions for container user
RUN mkdir -p /app/projects /app/pipelines /app/output /app/logs \
    && chmod -R 777 /app/projects /app/pipelines /app/output /app/logs /app/.npm

# Non-root user for security
RUN groupadd -r openmontage && useradd -r -g openmontage -d /app openmontage
USER openmontage

# Ensure global npm packages are on PATH for the runtime user
ENV PATH=/usr/local/bin:$PATH

# Expose the web API port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "api.web_server:app", "--host", "0.0.0.0", "--port", "8000"]
