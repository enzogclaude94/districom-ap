import type { Metadata } from "next";
import { Inter, Manrope } from "next/font/google";

import { Providers } from "@/app/providers";

import "./globals.css";

// Brand typography (docs/BRAND.md): Manrope SemiBold for brand moments, Inter for product UI.
const manrope = Manrope({ variable: "--font-manrope", subsets: ["latin"], weight: ["600"] });
const inter = Inter({ variable: "--font-inter", subsets: ["latin"], weight: ["400", "500", "600"] });

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
