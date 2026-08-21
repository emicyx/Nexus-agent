/**
 * /v1/* 同源代理（P0-1/P0-4）。
 *
 * 浏览器所有 API 请求发往 Next 自身（相对路径），由本路由转发到后端：
 * - API_BASE 不再写死 localhost：部署到任意域名 / HTTPS 反代后即开即用，
 *   也没有 http→https 混合内容问题；
 * - X-API-Key 只在服务端持有（BACKEND 侧 APP_API_KEY 注入），彻底离开
 *   浏览器 bundle——此前 NEXT_PUBLIC_API_KEY 内联进 JS 等于公开密钥；
 * - APP_ACCESS_PASSWORD 设置时，未携带有效会话 Cookie 的请求 401（登录门）。
 *
 * SSE 流式：上游响应体以 ReadableStream 原样回传（不缓冲），聊天流式不受影响。
 */
import { COOKIE_NAME, authGateEnabled, getCookie, verifySessionToken } from "@/lib/server-auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:8000";

// 逐跳头不透传（Host/Content-Length 与新目标不符；Cookie 留在前端域内）
const HOP_HEADERS = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-length",
  "cookie",
]);

async function proxy(
  req: Request,
  ctx: { params: { path: string[] } },
): Promise<Response> {
  if (authGateEnabled() && !verifySessionToken(getCookie(req, COOKIE_NAME))) {
    return Response.json(
      { detail: "未登录或会话已过期，请先登录" },
      { status: 401 },
    );
  }

  const { path } = ctx.params;
  const incoming = new URL(req.url);
  const target = `${BACKEND_ORIGIN}/v1/${path.join("/")}${incoming.search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  // P0-4：密钥只在服务端注入，浏览器 bundle 不含任何 API 凭据
  const apiKey = process.env.APP_API_KEY || "";
  if (apiKey) headers.set("x-api-key", apiKey);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const upstream = await fetch(target, {
    method: req.method,
    headers,
    ...(hasBody ? { body: req.body, duplex: "half" } : {}),
  } as RequestInit);

  const respHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_HEADERS.has(key.toLowerCase())) respHeaders.set(key, value);
  });
  // 流式透传：body 不读取不缓冲（SSE 依赖）
  return new Response(upstream.body, {
    status: upstream.status,
    headers: respHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
