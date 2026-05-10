import { NextResponse } from "next/server";

import { getCategories } from "@/lib/movimientos-api";

export const dynamic = "force-dynamic";

export async function GET() {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return NextResponse.json({ error: "feature_disabled" }, { status: 404 });
  }
  try {
    const data = await getCategories();
    return NextResponse.json(data);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_error", message: msg }, { status: 502 });
  }
}
