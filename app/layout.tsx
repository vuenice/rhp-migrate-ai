import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RHP Migration Workbench",
  description: "Professional Migration Workbench for PHP to strictly-typed compiled RHP",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased min-h-screen bg-neutral-950 text-neutral-100 selection:bg-emerald-500/30 selection:text-emerald-200">
        {children}
      </body>
    </html>
  );
}
