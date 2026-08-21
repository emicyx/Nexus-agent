/** @type {import('next').NextConfig} */
// 注：曾有 /v1/* rewrites 代理，但 api-client.ts 始终直连绝对 API_BASE，
// 代理从未被使用（死代码），已移除。如需同源代理再恢复并改用相对路径。
const nextConfig = {};

module.exports = nextConfig;
