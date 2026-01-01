import type { Metadata } from "next";
import { JetBrains_Mono, Space_Grotesk } from "next/font/google";

import { Sidebar } from "@/components/sidebar";
import { ThemeProvider } from "@/components/theme-provider";
import { TopBar } from "@/components/top-bar";
import { Toaster } from "@/components/ui/sonner";
import { cn } from "@/lib/utils";

import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Long-Form Content Intelligence Engine",
  description: "Sources, verification, and answers in one workspace.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={cn(
          spaceGrotesk.variable,
          jetbrainsMono.variable,
          "min-h-screen bg-[radial-gradient(1200px_800px_at_-10%_-20%,rgba(251,191,36,0.22),transparent),radial-gradient(900px_700px_at_110%_-10%,rgba(14,165,233,0.16),transparent)] font-sans antialiased"
        )}
      >
        <ThemeProvider>
          <div className="flex min-h-screen flex-col md:flex-row">
            <Sidebar />
            <div className="flex min-h-screen flex-1 flex-col">
              <TopBar />
              <main className="flex-1 px-6 py-8 md:px-10 md:py-10">
                <div className="animate-fade-up">{children}</div>
              </main>
            </div>
          </div>
          <Toaster richColors closeButton />
        </ThemeProvider>
      </body>
    </html>
  );
}
