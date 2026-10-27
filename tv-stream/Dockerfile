# TV viewer: ws-scrcpy (github.com/NetrisTV/ws-scrcpy, MIT), built from ./src.
FROM node:20-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY src/ .
RUN npm ci && npm run dist && cd dist && npm install --omit=dev

FROM node:20-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends adb && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /build/dist/ .
COPY start.sh /start.sh
EXPOSE 8000
CMD ["sh", "/start.sh"]
