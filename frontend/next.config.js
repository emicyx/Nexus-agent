/** @type {import('next').NextConfig} */
// P0-1/P0-2：standalone 输出（生产镜像只带 server.js + 最小依赖）。
// /v1/* 同源代理由 app/v1/[...path]/route.ts 实现（服务端持 API Key + SSE 流式透传），
// 浏览器一律相对路径请求，无需在此配置 rewrites。
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
};

module.exports = nextConfig;
