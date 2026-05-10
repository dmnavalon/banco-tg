import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { listMovements } from "@/lib/movimientos-api";
import type { MovementsFilters } from "@/lib/movimientos-types";

export const dynamic = "force-dynamic";

function parseFilters(url: URL): MovementsFilters {
  const f: MovementsFilters = {};
  const sp = url.searchParams;
  const passthrough = ["status", "from", "to", "bank", "persona", "categoria",
                       "subcategoria", "q", "comercio"] as const;
  for (const k of passthrough) {
    const v = sp.get(k);
    if (v !== null && v !== "") f[k] = v;
  }
  for (const k of ["min_amount", "max_amount", "confidence_min", "limit"] as const) {
    const v = sp.get(k);
    if (v !== null && v !== "") {
      const n = Number(v);
      if (!Number.isNaN(n)) f[k] = n;
    }
  }
  return f;
}

export async function GET(request: NextRequest) {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return NextResponse.json({ error: "feature_disabled" }, { status: 404 });
  }
  try {
    const data = await listMovements(parseFilters(request.nextUrl));
    return NextResponse.json(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_error", message: msg }, { status: 502 });
  }
}
