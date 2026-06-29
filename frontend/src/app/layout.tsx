import type { Metadata } from "next";
import "./globals.css";
import { Navbar } from "@/components/layout/Navbar";

export const metadata: Metadata = {
  title: "SynViz — Verilog Syncode Visualizer",
  description:
    "Interactive visualization of grammar-constrained Verilog generation using SynCode. Inspect token-level distributions, allowed grammar continuations, filtered invalid tokens, and final Verilog output.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface text-[#e6edf3] antialiased">
        <Navbar />
        <main className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
          {children}
        </main>
      </body>
    </html>
  );
}
