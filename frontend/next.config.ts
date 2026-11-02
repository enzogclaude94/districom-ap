import type { NextConfig } from "next";

// Where the server forwards /api/* (the FastAPI backend). Same-origin for the
// browser, so the session cookie never crosses sites (ARCHITECTURE.md §8).
// Read at build time: rebuild when it changes.
const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

// Content Security Policy (R9-5-5, O5), enforced since R9-5-6 after a clean Report-Only run of the whole E2E suite:
// the browser blocks what the policy does not allow and reports it to /api/v1/csp-report, which logs only the
// directive and a category. Never loosen it to make something work: report the violation instead.
// 'unsafe-inline' in scripts: Next.js hydrates with inline scripts; removing it needs a nonce per request (R1).
// `next dev` also evaluates code for Fast Refresh, so development adds 'unsafe-eval'; production builds never do.
const CSP_REPORT_PATH = "/api/v1/csp-report";
const scriptSrc = ["'self'", "'unsafe-inline'", ...(process.env.NODE_ENV === "development" ? ["'unsafe-eval'"] : [])];
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src ${scriptSrc.join(" ")}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
  // report-uri, not report-to: Chrome would then batch reports through the Reporting API, with a delay.
  `report-uri ${CSP_REPORT_PATH}`,
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // Docker builds set NEXT_OUTPUT=standalone; `next start` (dev/CI) needs the default output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
