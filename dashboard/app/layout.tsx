import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WeChat Alpha Desk",
  description: "Fintech-style local dashboard for decrypted WeChat messages, contacts, sessions, and AI summaries.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      className="h-full antialiased"
    >
      <body className="min-h-full">{children}</body>
    </html>
  );
}
