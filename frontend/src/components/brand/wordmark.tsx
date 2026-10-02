import Image from "next/image";

import { cn } from "@/lib/cn";

const VARIANTS = {
  color: "/brand/districom-wordmark.svg",
  negative: "/brand/districom-wordmark-negative.svg",
  mono: "/brand/districom-wordmark-mono.svg",
} as const;

// Intrinsic ratio of the SVG wordmark (viewBox 9173 × 1500).
const RATIO = 9173 / 1500;

interface WordmarkProps {
  /** Rendered height in px; width follows the wordmark proportions. */
  height?: number;
  variant?: keyof typeof VARIANTS;
  className?: string;
  priority?: boolean;
}

/** DistriCom wordmark for wide contexts: login, expanded sidebar, reports. Never recolor or distort it. */
export function Wordmark({ height = 24, variant = "color", className, priority }: WordmarkProps) {
  return (
    <Image
      src={VARIANTS[variant]}
      alt="DistriCom"
      width={Math.round(height * RATIO)}
      height={height}
      priority={priority}
      unoptimized
      className={cn("select-none", className)}
    />
  );
}
