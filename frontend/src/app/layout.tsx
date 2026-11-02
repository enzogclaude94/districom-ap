import type { Metadata } from "next";
import localFont from "next/font/local";

import { Providers } from "@/app/providers";

import "./globals.css";

// Brand typography (docs/BRAND.md): Manrope SemiBold for brand moments, Inter for product UI. Served from the
// repository (src/app/fonts/README.md), so the build needs no network; the same fonts and weights Google Fonts served.
const manrope = localFont({
  src: [{ path: "./fonts/Manrope-SemiBold.woff2", weight: "600", style: "normal" }],
  variable: "--font-manrope",
});
// One variable file (weight axis) behind each weight the brand uses, as Google Fonts declared them.
const inter = localFont({
  src: [
    { path: "./fonts/Inter-opsz14.woff2", weight: "400", style: "normal" },
    { path: "./fonts/Inter-opsz14.woff2", weight: "500", style: "normal" },
    { path: "./fonts/Inter-opsz14.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: { default: "DistriCom", template: "%s · DistriCom" },
  description: "Plataforma de gestión para distribuidoras.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es-AR" className={`${manrope.variable} ${inter.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
