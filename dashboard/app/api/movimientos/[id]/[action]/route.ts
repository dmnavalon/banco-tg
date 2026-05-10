import { NextResponse } from "next/server";

import { postSingle } from "@/lib/movimientos-api";

export const dynamic = "force-dynamic";

const ALLOWED = new Set([
  "approve",
  "correct",
  "approve-correction",
  "ignore",
  "reopen",
  "sync",
]);

type Action =
  | "approve"
  | "correct"
  | "approve-correction"
  | "ignore"
  | "reopen"
  | "sync";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string; action: string }> },
) {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return NextResponse.json({ error: "feature_disabled" }, { status: 404 });
  }
  const { id, action } = await params;
  if (!ALLOWED.has(action)) {
    return NextResponse.json({ error: "unknown_action", action }, { status: 404 });
  }
  let payload: Record<string, unknown> = {};
  try {
    payload = (await request.json()) as Record<string, unknown>;
  } catch {
    payload = {};
  }
  try {
    const { status, body } = await postSingle(id, action as Action, payload);
    return NextResponse.json(body, { status });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ error: "backend_error", message: msg }, { status: 502 });
  }
}
