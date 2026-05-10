import { NextResponse } from "next/server";

import { createCategory, getCategories } from "@/lib/movimientos-api";

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

export async function POST(request: Request) {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return NextResponse.json({ error: "feature_disabled" }, { status: 404 });
  }
  let payload: { cat?: unknown; sub?: unknown } = {};
  try {
    payload = (await request.json()) as { cat?: unknown; sub?: unknown };
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const cat = typeof payload.cat === "string" ? payload.cat : "";
  const sub = typeof payload.sub === "string" ? payload.sub : "";
  if (!cat.trim() || !sub.trim()) {
    return NextResponse.json(
      { error: "validation_error", message: "cat y sub son obligatorios" },
      { status: 422 },
    );
  }
  try {
    const { status, body } = await createCategory({ cat: cat.trim(), sub: sub.trim() });
    return NextResponse.json(body, { status });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_error", message: msg }, { status: 502 });
  }
}
