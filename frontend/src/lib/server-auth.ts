/**
 * 服务端会话签发/校验（P0-1/P0-4 配套）。
 *
 * 仅可在 Route Handler / 服务端代码中导入（依赖 node:crypto 与服务端 env），
 * 绝不能被客户端组件引入，否则密钥材料会被打进浏览器 bundle。
 *
 * 模型：单租户访问密码（APP_ACCESS_PASSWORD）→ HMAC 签名的 HttpOnly Cookie。
 * 后端 X-API-Key 由代理路由（/v1/[...path]）在服务端注入，浏览器全程不可见。
 */
import crypto from "node:crypto";

export const COOKIE_NAME = "nexus_session";
export const SESSION_TTL_MS = 7 * 24 * 3600 * 1000; // 7 天

export function accessPassword(): string {
  return process.env.APP_ACCESS_PASSWORD || "";
}

/** 访问门是否启用（未设密码 = 关闭，本地开发免登录）。 */
export function authGateEnabled(): boolean {
  return accessPassword().length > 0;
}

function hmac(value: string): string {
  // 派生密钥含访问密码本身：改密码即让全部既有会话失效
  const key = `nexus:${accessPassword()}:${process.env.SESSION_SECRET || ""}`;
  return crypto.createHmac("sha256", key).update(value).digest("hex");
}

export function makeSessionToken(): string {
  const exp = String(Date.now() + SESSION_TTL_MS);
  return `${exp}.${hmac(exp)}`;
}

export function verifySessionToken(token: string | undefined | null): boolean {
  if (!token) return false;
  const dot = token.indexOf(".");
  if (dot <= 0) return false;
  const exp = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  const expNum = Number(exp);
  if (!Number.isFinite(expNum) || expNum < Date.now()) return false;
  const expected = hmac(exp);
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

/** 登录口令比对（哈希后常量时间比较，防计时侧信道）。 */
export function passwordMatches(pw: string): boolean {
  const a = crypto.createHash("sha256").update(pw).digest();
  const b = crypto.createHash("sha256").update(accessPassword()).digest();
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

/** 从 Cookie 头解析指定项（避免引入 cookie 依赖）。 */
export function getCookie(req: Request, name: string): string | undefined {
  const header = req.headers.get("cookie") || "";
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq <= 0) continue;
    if (part.slice(0, eq).trim() === name) {
      return part.slice(eq + 1).trim();
    }
  }
  return undefined;
}
