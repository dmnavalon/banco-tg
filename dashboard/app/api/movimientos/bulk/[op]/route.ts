import { NextResponse } from "next/server";

import { postBulk } from "@/lib/movimientos-api";

export const dynamic = "force-dynamic";

const ALLOWED = new Set([
  "approve",
  "categorize",
  "ignore",
  "comment",
  "reopen",
]);

type BulkOp = "approve" | "categorize" | "ignore" | "comment" | "reopen";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ op: string }> },
) {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return NextResponse.json({ error: "feature_disabled" }, { status: 404 });
  }
  const { op } = await params;
  if (!ALLOWED.has(op)) {
    return NextResponse.json({ error: "unknown_op", op }, { status: 404 });
  }
  let payload: Record<string, unknown> = {};
  try {
    payload = (await request.json()) as Record<string, unknown>;
  } catch {
    payload = {};
  }
  try {
    const result = await postBulk(op as BulkOp, payload);
    if (result.error) {
      return NextResponse.json({ error: "backend_error", message: result.error }, { status: result.status });
    }
    return NextResponse.json({ results: result.results });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_error", message: msg }, { status: 502 });
  }
}
