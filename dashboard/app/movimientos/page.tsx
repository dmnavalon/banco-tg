import Link from "next/link";

import { MovimientosTable } from "@/components/movimientos/MovimientosTable";

export const dynamic = "force-dynamic";

export default function MovimientosPage() {
  if (process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW !== "true") {
    return (
      <main className="min-h-screen bg-slate-50 px-6 py-12">
        <div className="mx-auto max-w-2xl rounded-lg border border-slate-200 bg-white p-8 text-slate-600">
          <h1 className="text-xl font-semibold text-slate-800">Sección no disponible</h1>
          <p className="mt-2 text-sm">
            La feature &ldquo;Movimientos&rdquo; no está activa en este entorno.
            Setea <code className="rounded bg-slate-100 px-1">NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW=true</code> y
            redeploy para habilitarla.
          </p>
          <Link href="/" className="mt-4 inline-block text-sm text-blue-600 hover:underline">
            ← Volver al dashboard
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Movimientos</h1>
            <p className="text-sm text-slate-500">
              Revisa, corrige, aprueba o ignora movimientos antes de enviarlos al registro final.
            </p>
          </div>
          <Link href="/" className="text-sm text-blue-600 hover:underline">
            ← Dashboard
          </Link>
        </div>
      </header>
      <section className="mx-auto max-w-[1600px] px-6 py-6">
        <MovimientosTable />
      </section>
    </main>
  );
}
