import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Barca X Bot — Command Center",
  description: "Admin dashboard to control posting behavior, Gemini dual-key activity, match triggers, and GitHub images.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased selection:bg-[#a50044] selection:text-white">{children}</body>
    </html>
  );
}
