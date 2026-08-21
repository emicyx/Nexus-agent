/**
 * 登录门（P0-1/P0-4）：POST 密码 → 签发 HttpOnly 会话 Cookie。
 *
 * 后端 API 密钥（APP_API_KEY）不再进浏览器 bundle——前端访问 /v1/* 一律
 * 走同源代理路由，代理校验本 Cookie 后在服务端注入 X-API-Key。
 */
import { NextResponse } from "next/server";
import {
  COOKIE_NAME,
  SESSION_TTL_MS,
  authGateEnabled,
  makeSessionToken,
  passwordMatches,
} from "@/lib/server-auth";

export const runtime = "nodejs";

export async function POST(req: Request) {
  if (!authGateEnabled()) {
    return NextResponse.json(
      { error: "未启用访问密码（APP_ACCESS_PASSWORD 为空），无需登录" },
      { status: 400 },
    );
  }
  const body = await req.json().catch(() => ({}) as { password?: string });
  const password = typeof body.password === "string" ? body.password : "";
  if (!password || !passwordMatches(password)) {
    return NextResponse.json({ error: "密码错误" }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE_NAME, makeSessionToken(), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: Math.floor(SESSION_TTL_MS / 1000),
  });
  return res;
}
