import { NextRequest, NextResponse } from "next/server";

import { fetchBackend } from "@/lib/movimientos-api";

export const dynamic = "force-dynamic";

// Proxy único para las escrituras manuales de patrimonio/deudas desde el
// dashboard (entrada a mano por mes). El token vive server-side.
export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => ({}))) as {
    kind?: string;
    mes?: string;
    id?: string;
    fields?: Record<string, unknown>;
  };
  const { kind, mes, id, fields } = body;

  let path: string | null = null;
  if (kind === "patrimonio" && mes) {
    path = `/api/patrimonio/snapshots/${encodeURIComponent(mes)}`;
  } else if (kind === "deuda_snapshot" && mes && id) {
    path = `/api/deudas/snapshots/${encodeURIComponent(mes)}/${encodeURIComponent(id)}`;
  } else if (kind === "deuda_maestro" && id) {
    path = `/api/deudas/maestro/${encodeURIComponent(id)}`;
  }
  if (!path) {
    return NextResponse.json({ error: "bad_request", message: "kind/mes/id inválidos" }, { status: 400 });
  }

  try {
    const r = await fetchBackend(path, { method: "PUT", body: JSON.stringify(fields ?? {}) });
    const j = await r.json().catch(() => ({}));
    return NextResponse.json(j, { status: r.status });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_unreachable", message: msg }, { status: 502 });
  }
}
