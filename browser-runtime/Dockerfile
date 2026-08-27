FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PROFILE_DIR=/profile \
    CONTROLLER_PORT=3001

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      chromium \
      fonts-noto-cjk \
      fonts-noto-color-emoji \
      openbox \
      x11vnc \
      xvfb \
      ca-certificates \
      dumb-init \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /runtime
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY entrypoint.sh server.mjs autoplay-guard.mjs ./
RUN chmod +x /runtime/entrypoint.sh \
    && mkdir -p /profile /profile/Downloads

EXPOSE 3001 6080
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/runtime/entrypoint.sh"]
