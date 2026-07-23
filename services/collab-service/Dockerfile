FROM node:22-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci
COPY src ./src
RUN npm run build

FROM node:22-alpine
RUN apk --no-cache add curl
WORKDIR /app
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY package.json ./

RUN adduser -D -u 1001 appuser
USER appuser

EXPOSE 8084
HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://localhost:8084/health || exit 1
CMD ["node", "dist/index.js"]
