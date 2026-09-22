FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium ca-certificates fonts-liberation jq \
    && rm -rf /var/lib/apt/lists/*
ENV CHROME_PATH=/usr/bin/chromium
RUN npm install -g lighthouse
ENTRYPOINT ["lighthouse"]
