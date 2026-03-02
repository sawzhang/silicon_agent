FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps (git + openssh-client needed for private pip deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

# Add GitHub host key so git+ssh doesn't fail on host verification
RUN mkdir -p /root/.ssh && ssh-keyscan github.com >> /root/.ssh/known_hosts

# Use SSH mount to clone private dependencies during build
RUN --mount=type=ssh pip install --no-cache-dir .

# Copy application code
COPY app/ ./app/
COPY sandbox/ ./sandbox/
COPY skills/ ./skills/
COPY memory/ ./memory/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
