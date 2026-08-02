import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Client Portal",
  description: "Live performance reporting",
  // The portal holds client personal information. Keep it out of every index.
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-ZA" suppressHydrationWarning>
      <body className="min-h-dvh bg-neutral-50 text-neutral-900 antialiased
                       dark:bg-neutral-950 dark:text-neutral-50">
        {children}
      </body>
    </html>
  );
}
