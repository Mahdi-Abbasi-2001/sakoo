# Deploy Agent: the API and the built frontend in one image (one origin, so cookies and CSRF just work).
# NOTE: written for Liara's docker platform but NOT yet built or deployed (no Docker on the machine it was written on).
# Settings go in as environment variables on the app: WEB_SECRET_KEY (required: encrypts stored tokens), LLM_API_KEY / LLM_BASE_URL / LLM_MODEL,
# WEB_REGISTRATION_CODE (an invite code: set it, the server is on the internet and accounts can use the model key), and optionally WEB_GITHUB_CLIENT_ID / WEB_GITHUB_CLIENT_SECRET (sign in with GitHub). Keep the /data disk or accounts and run history are lost on redeploy.

FROM node:22-alpine AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# git is needed to clone public repositories users paste in
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY --from=web /build/frontend/dist ./frontend/dist
# accounts, projects and run logs live here: mount a Liara disk at /data or they are lost on every deploy
ENV WEB_DATA_DIR=/data \
    WEB_STATIC_DIR=/app/frontend/dist \
    WEB_COOKIE_SECURE=true \
    TMPDIR=/data/tmp \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 agent && mkdir -p /data && chown agent /data
# The entrypoint starts as root only to fix the owner of the mounted /data disk, then runs the server as the unprivileged user.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["liara-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
