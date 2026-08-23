FROM python:3.11-slim

WORKDIR /app

# System dependencies for asyncpg / cryptography / scikit-learn builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install all Python dependencies.
# scikit-learn is included here (Python 3.11 has pre-built wheels) even though
# it is commented out in requirements.txt for local Python 3.14 compatibility.
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] pydantic pydantic-settings \
    sqlalchemy asyncpg alembic \
    ibm-watsonx-ai ibm-cloud-sdk-core \
    paho-mqtt redis aiohttp httpx python-dotenv \
    pandas numpy scikit-learn \
    python-jose[cryptography] passlib[bcrypt] python-multipart \
    schedule loguru

COPY . .

# Run as non-root user (security best practice)
RUN useradd -m -u 1000 ecoguard
USER ecoguard

EXPOSE 8000

# Health check — orchestration platforms use this to detect a crashed app.
# start-period gives the app time to finish DB init and agent startup.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
