import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function backendUrl(): string {
  const url = process.env.BACKEND_API_URL;
  if (!url) throw new Error("BACKEND_API_URL no configurado");
  return url.replace(/\/$/, "");
}

function backendToken(): string {
  const t = process.env.BACKEND_API_TOKEN;
  if (!t) throw new Error("BACKEND_API_TOKEN no configurado");
  return t;
}

export async function GET() {
  try {
    const r = await fetch(`${backendUrl()}/api/patrimonio/status`, {
      headers: { Authorization: `Bearer ${backendToken()}` },
      cache: "no-store",
    });
    const body = await r.json().catch(() => ({}));
    return NextResponse.json(body, { status: r.status });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_unreachable", message: msg }, { status: 502 });
  }
}
