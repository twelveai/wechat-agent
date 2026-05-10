import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WeChat Data AI Dashboard",
  description: "Local dashboard for decrypted WeChat data, messages, contacts, and sessions.",
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
