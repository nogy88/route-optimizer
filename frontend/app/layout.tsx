import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/lib/state";

export const metadata: Metadata = {
  title: "VRP Route Optimizer",
  description: "OR-Tools + OSRM — Ulaanbaatar delivery routing",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
      </head>
      <body className="bg-slate-100 text-slate-900 overflow-hidden h-screen">
        <AppProvider>{children}</AppProvider>
      </body>
    </html>
  );
}
