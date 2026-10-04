import Image from "next/image";

import { cn } from "@/lib/cn";
import type { ModuleLogoProps } from "@/lib/modules";

// Web version of the official logo: the original PNG cropped, with a transparent background and
// resized (docs/BRAND.md). Never recolor, redraw or distort it.
const SRC = "/brand/districom-rendiciones.png";
const RATIO = 880 / 240;

/** Official DistriCom Rendiciones logo, for the module home and the module selector only. */
export function SettlementsLogo({ height = 48, className, priority }: ModuleLogoProps) {
  return (
    <Image
      src={SRC}
      alt="DistriCom Rendiciones"
      width={Math.round(height * RATIO)}
      height={height}
      priority={priority}
      unoptimized
      className={cn("select-none", className)}
    />
  );
}
