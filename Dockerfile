# ---- DevGordon Dockerfile ----
# We use a slim Python image. "slim" = smaller download, no build tools.
# If you need to compile native extensions later, switch to python:3.11

FROM python:3.11-slim

# Install system packages
# ansible and ansible-lint: needed to actually run playbooks and scan them
# kubectl: installed separately below (not in apt)
# curl: needed to download kubectl
RUN apt-get update && apt-get install -y \
    ansible \
    curl \
    && pip install ansible-lint --quiet \
    && ansible-galaxy collection install kubernetes.core \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install kubectl (latest stable)
# This is what lets DevGordon run kubectl commands against your cluster
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/kubectl

# Create a non-root user — running as root in a container is bad practice
# (and SonarQube would flag it if you didn't)
RUN useradd -m -u 1000 devgordon

WORKDIR /app

# Copy requirements first — Docker caches layers, so this layer only
# rebuilds when requirements.txt changes, not when your code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Step into the app subfolder so uvicorn finds main.py directly,
# and so relative imports (from tools import, from scanner import) work.
# File layout after COPY: /app/app/main.py, /app/app/tools.py, etc.
WORKDIR /app/app

# Switch to non-root user
USER devgordon

# Expose port 8000 (FastAPI default)
EXPOSE 8000
# Health check — Docker will mark the container unhealthy if this fails
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8000/history || exit 1

# Start the app — WORKDIR is /app/app so main:app resolves correctly
CMD ["sh", "-c", "\
  mkdir -p /tmp/kube && \
  cp /home/devgordon/.kube/config /tmp/kube/config 2>/dev/null || true && \
  sed -i 's|https://127.0.0.1|https://host.docker.internal|g' /tmp/kube/config 2>/dev/null || true && \
  KUBECONFIG=/tmp/kube/config uvicorn main:app --host 0.0.0.0 --port 8000"]