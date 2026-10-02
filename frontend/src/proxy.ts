import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/api/session";

// Optimistic redirect only: the backend is the authority on whether the session is valid.
export function proxy(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const loginUrl = new URL("/login", request.url);
  const target = request.nextUrl.pathname + request.nextUrl.search;
  if (target !== "/") loginUrl.searchParams.set("next", target);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  // Skip the API, the login page, Next internals and any static file (brand assets, icons).
  matcher: ["/((?!api|login|_next/static|_next/image|.*\\.[a-z0-9]+$).*)"],
};
