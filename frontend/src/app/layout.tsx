import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Project Nexus",
  description: "多功能私人 AI Agent 助手",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-sakura-50 text-zinc-700 antialiased">
        {children}
      </body>
    </html>
  );
}
