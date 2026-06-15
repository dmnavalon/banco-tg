"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Save } from "lucide-react";

import { DashboardData } from "@/lib/types";
import { Card, CardHeader } from "../ui/Card";

const INPUT = "w-full rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";

function mesActualISO(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

async function postFinanzas(payload: Record<string, unknown>): Promise<boolean> {
  const r = await fetch("/api/finanzas", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return r.ok;
}

/** Entrada manual del mes: patrimonio (caja líquida, patrimonio neto, pasivos) y
 * deudas (saldo/intereses/capital). Alimenta los KPIs de patrimonio/deuda que
 * antes se tipeaban directo en el GSheet. */
export function FinanzasManual({ data }: { data: DashboardData }) {
  const router = useRouter();
  const mes = mesActualISO();
  const pat = data.patrimonio.find((p) => p.mes === mes);

  const [caja, setCaja] = useState(pat ? String(pat.cajaLiquida) : "");
  const [neto, setNeto] = useState(pat ? String(pat.patrimonioNeto) : "");
  const [pasivos, setPasivos] = useState(pat ? String(pat.pasivosTotales) : "");
  const [notas, setNotas] = useState(pat?.notas ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const savePatrimonio = async () => {
    setSaving(true);
    setMsg(null);
    const ok = await postFinanzas({
      kind: "patrimonio",
      mes,
      fields: {
        caja_liquida: Number(caja) || 0,
        patrimonio_neto: Number(neto) || 0,
        pasivos_totales: Number(pasivos) || 0,
        notas,
      },
    });
    setSaving(false);
    setMsg(ok ? "✓ Patrimonio guardado" : "Error al guardar");
    if (ok) router.refresh();
  };

  return (
    <Card padding="md">
      <CardHeader title={`Datos manuales · ${mes}`} subtitle="Caja líquida y patrimonio neto del mes (alimentan los KPIs)" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs text-zinc-500">
          Caja líquida (CLP)
          <input className={INPUT} type="number" value={caja} onChange={(e) => setCaja(e.target.value)} />
        </label>
        <label className="text-xs text-zinc-500">
          Patrimonio neto (CLP)
          <input className={INPUT} type="number" value={neto} onChange={(e) => setNeto(e.target.value)} />
        </label>
        <label className="text-xs text-zinc-500">
          Pasivos totales (CLP)
          <input className={INPUT} type="number" value={pasivos} onChange={(e) => setPasivos(e.target.value)} />
        </label>
        <label className="text-xs text-zinc-500">
          Notas
          <input className={INPUT} type="text" value={notas} onChange={(e) => setNotas(e.target.value)} />
        </label>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={savePatrimonio}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          <Save className="h-3.5 w-3.5" />
          {saving ? "Guardando…" : "Guardar mes"}
        </button>
        {msg && <span className="text-xs text-zinc-500">{msg}</span>}
      </div>

      {data.deudasMaestro.length > 0 && (
        <DeudasEditor data={data} mes={mes} onSaved={() => router.refresh()} />
      )}
    </Card>
  );
}

function DeudasEditor({ data, mes, onSaved }: { data: DashboardData; mes: string; onSaved: () => void }) {
  return (
    <div className="mt-5 border-t border-zinc-100 pt-4 dark:border-zinc-800">
      <p className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-300">Deudas del mes</p>
      <div className="space-y-2">
        {data.deudasMaestro.filter((d) => d.activa).map((d) => (
          <DeudaRow
            key={d.id}
            id={d.id}
            label={`${d.institucion || d.id} (${d.tipo})`}
            mes={mes}
            snap={data.deudasSnapshot.find((s) => s.mes === mes && s.id === d.id)}
            onSaved={onSaved}
          />
        ))}
      </div>
    </div>
  );
}

function DeudaRow({
  id,
  label,
  mes,
  snap,
  onSaved,
}: {
  id: string;
  label: string;
  mes: string;
  snap: DashboardData["deudasSnapshot"][number] | undefined;
  onSaved: () => void;
}) {
  const [saldo, setSaldo] = useState(snap ? String(snap.saldoActual) : "");
  const [intereses, setIntereses] = useState(snap ? String(snap.interesesPagadosMes) : "");
  const [capital, setCapital] = useState(snap ? String(snap.capitalPagadoMes) : "");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    const ok = await postFinanzas({
      kind: "deuda_snapshot",
      mes,
      id,
      fields: {
        saldo_actual: Number(saldo) || 0,
        saldo_clp: Number(saldo) || 0,
        intereses_pagados_mes: Number(intereses) || 0,
        capital_pagado_mes: Number(capital) || 0,
      },
    });
    setSaving(false);
    if (ok) onSaved();
  };

  return (
    <div className="flex flex-wrap items-end gap-2">
      <span className="w-40 truncate text-xs text-zinc-600 dark:text-zinc-300" title={label}>{label}</span>
      <label className="text-[10px] text-zinc-400">
        Saldo
        <input className={INPUT + " w-28"} type="number" value={saldo} onChange={(e) => setSaldo(e.target.value)} />
      </label>
      <label className="text-[10px] text-zinc-400">
        Intereses mes
        <input className={INPUT + " w-28"} type="number" value={intereses} onChange={(e) => setIntereses(e.target.value)} />
      </label>
      <label className="text-[10px] text-zinc-400">
        Capital mes
        <input className={INPUT + " w-28"} type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
      </label>
      <button
        onClick={save}
        disabled={saving}
        className="rounded border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        {saving ? "…" : "Guardar"}
      </button>
    </div>
  );
}
