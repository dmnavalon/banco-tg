import { NextRequest, NextResponse } from "next/server";

/**
 * Basic Auth gate. Activado solo si DASHBOARD_PASSWORD está definido.
 * Username se ignora (solo importa el password). Útil para staging/preview.
 */
export function proxy(req: NextRequest) {
  const password = process.env.DASHBOARD_PASSWORD;
  if (!password) return NextResponse.next();

  const auth = req.headers.get("authorization");
  if (auth) {
    const [scheme, encoded] = auth.split(" ");
    if (scheme === "Basic" && encoded) {
      try {
        const decoded = atob(encoded);
        const idx = decoded.indexOf(":");
        const provided = idx >= 0 ? decoded.slice(idx + 1) : decoded;
        if (provided === password) {
          return NextResponse.next();
        }
      } catch {
        // fallthrough to 401
      }
    }
  }

  return new NextResponse("Authentication required", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Dashboard de Finanzas Personales", charset="UTF-8"',
    },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
