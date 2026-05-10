import { NextResponse } from "next/server";

import { getAudit } from "@/lib/movimientos-api";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return NextResponse.json({ error: "feature_disabled" }, { status: 404 });
  }
  const { id } = await params;
  try {
    const data = await getAudit(id);
    return NextResponse.json(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_error", message: msg }, { status: 502 });
  }
}
