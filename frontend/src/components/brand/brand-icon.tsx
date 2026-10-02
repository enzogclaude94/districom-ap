import Image from "next/image";

import { cn } from "@/lib/cn";

interface BrandIconProps {
  size?: number;
  className?: string;
  /** Decorative when next to the wordmark or a visible product name. */
  decorative?: boolean;
}

/** DC icon for compact contexts: collapsed navigation, mobile header, app icon. */
export function BrandIcon({ size = 32, className, decorative = false }: BrandIconProps) {
  return (
    <Image
      src={size <= 96 ? "/brand/districom-icon-96.png" : "/brand/districom-icon.png"}
      alt={decorative ? "" : "DistriCom"}
      width={size}
      height={size}
      unoptimized
      className={cn("select-none", className)}
    />
  );
}
