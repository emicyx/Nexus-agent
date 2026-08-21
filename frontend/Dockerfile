# P0-2：多阶段生产构建（此前 next dev 直接上线——无优化、暴露开发报错）
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --registry https://registry.npmmirror.com

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* 构建期内联：默认全部留空 = 同源代理模式（任意域名/HTTPS 可用）
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
# 非 root 运行（node 镜像自带 uid 1000 的 node 用户）
USER node
EXPOSE 3000
CMD ["node", "server.js"]
