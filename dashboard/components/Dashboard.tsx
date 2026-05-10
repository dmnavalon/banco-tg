"use client";

import { useState } from "react";
import { DashboardKPIs, DashboardData, Kpi } from "@/lib/types";
import { ResumenGeneralSection } from "./sections/ResumenGeneral";
import { GastosSection } from "./sections/Gastos";
import { CalidadDatosSection } from "./sections/CalidadDatos";
import { AlertasSection } from "./sections/Alertas";
import { PlaceholderSection } from "./sections/Placeholder";
import { DetalleKpiPanel } from "./ui/DetalleKpiPanel";
import { ThemeToggle } from "./ui/ThemeToggle";
import { cn } from "@/lib/utils";
import { LayoutDashboard, TrendingUp, DollarSign, Target, CreditCard, Wallet, PieChart, Building, ShieldCheck, Bell, Sparkles, ListChecks } from "lucide-react";
import Link from "next/link";

const MOVIMIENTOS_ENABLED = process.env.NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW === "true";

const SECTIONS = [
  { id: "resumen", label: "Resumen", Icon: LayoutDashboard },
  { id: "flujo", label: "Flujo de caja", Icon: TrendingUp },
  { id: "gastos", label: "Gastos", Icon: DollarSign },
  { id: "presupuesto", label: "Presupuesto", Icon: Target },
  { id: "deuda", label: "Deuda", Icon: CreditCard },
  { id: "fondo", label: "Fondo emergencia", Icon: Wallet },
  { id: "inversiones", label: "Inversiones", Icon: PieChart },
  { id: "patrimonio", label: "Patrimonio", Icon: Building },
  { id: "calidad", label: "Calidad datos", Icon: ShieldCheck },
  { id: "alertas", label: "Alertas", Icon: Bell },
  { id: "insights", label: "Insights", Icon: Sparkles },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

export function Dashboard({ kpis, data, spreadsheetId }: { kpis: DashboardKPIs; data: DashboardData; spreadsheetId: string }) {
  const [active, setActive] = useState<SectionId>("resumen");
  const [kpiSeleccionado, setKpiSeleccionado] = useState<{ kpi: Kpi; contexto: string } | null>(null);

  const fechaUpdate = new Date(data.fetchedAt).toLocaleString("es-CL", {
    dateStyle: "medium",
    timeStyle: "short",
  });

  const openKpi = (kpi: Kpi, contexto: string) => setKpiSeleccionado({ kpi, contexto });

  return (
    <div className="flex h-full flex-col bg-zinc-50 dark:bg-zinc-950">
      {/* Header */}
      <header className="z-30 shrink-0 border-b border-zinc-200 bg-white/80 backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-950/80">
        <div className="mx-auto max-w-7xl px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h1 className="text-base font-bold tracking-tight text-zinc-900 dark:text-zinc-50 sm:text-lg">Dashboard de Finanzas Personales</h1>
              <p className="text-xs text-zinc-500">Mes {kpis.mesActual} · {data.movimientos.length} movimientos · actualizado {fechaUpdate}</p>
            </div>
            <div className="flex items-center gap-3">
              <div className="hidden text-right text-xs text-zinc-500 sm:block">
                <p>{kpis.alertas.length} alertas activas</p>
                <p>{kpis.alertas.filter((a) => a.severidad === "alta").length} de severidad alta</p>
              </div>
              {MOVIMIENTOS_ENABLED && (
                <Link
                  href="/movimientos"
                  className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
                >
                  <ListChecks className="h-3.5 w-3.5" />
                  Movimientos
                </Link>
              )}
              <ThemeToggle />
            </div>
          </div>
        </div>
        {/* Tabs scroll */}
        <div className="border-t border-zinc-100 dark:border-zinc-900">
          <nav className="mx-auto max-w-7xl overflow-x-auto px-4 sm:px-6 lg:px-8">
            <div className="flex gap-1 py-1">
              {SECTIONS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => setActive(s.id)}
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    active === s.id
                      ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                      : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900",
                  )}
                >
                  <s.Icon className="h-3.5 w-3.5" />
                  {s.label}
                </button>
              ))}
            </div>
          </nav>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {data.warnings.length > 0 && (
          <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
            <p className="font-medium">Avisos:</p>
            <ul className="ml-4 mt-1 list-disc text-xs">
              {data.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        )}

        {active === "resumen" && <ResumenGeneralSection kpis={kpis} onKpiClick={(kpi) => openKpi(kpi, "Resumen general")} />}
        {active === "gastos" && <GastosSection kpis={kpis} />}
        {active === "calidad" && <CalidadDatosSection kpis={kpis} />}
        {active === "alertas" && <AlertasSection kpis={kpis} onNavigate={(s) => setActive(s as SectionId)} />}
        {active === "flujo" && (
          <PlaceholderSection
            title="Flujo de caja"
            question="Tengo liquidez para los próximos 30/90 días"
            pestañasRequeridas={["IngresosEsperados", "EgresosEsperados", "Patrimonio (caja líquida)"]}
          />
        )}
        {active === "presupuesto" && (
          <PlaceholderSection
            title="Presupuesto y control mensual"
            question="Voy dentro del presupuesto"
            pestañasRequeridas={["Presupuesto"]}
          />
        )}
        {active === "deuda" && (
          <PlaceholderSection
            title="Deuda"
            question="Es sostenible mi deuda"
            pestañasRequeridas={["Deudas_Maestro", "Deudas_Snapshot"]}
          />
        )}
        {active === "fondo" && (
          <PlaceholderSection
            title="Fondo de emergencia y liquidez"
            question="Tengo colchón suficiente"
            pestañasRequeridas={["Patrimonio (caja líquida)", "+ historial de gasto esencial"]}
          />
        )}
        {active === "inversiones" && (
          <PlaceholderSection
            title="Inversiones"
            question="Estoy diversificado y rentable"
            pestañasRequeridas={["Inversiones_Maestro", "Inversiones_Snapshot", "InversionesObjetivo"]}
          />
        )}
        {active === "patrimonio" && (
          <PlaceholderSection
            title="Patrimonio"
            question="Aumenta mi patrimonio y por qué"
            pestañasRequeridas={["Patrimonio", "ActivosIlíquidos"]}
          />
        )}
        {active === "insights" && (
          <PlaceholderSection
            title="Insights y plan de acción"
            question="Qué decisiones concretas tomo este mes"
            pestañasRequeridas={["Patrimonio", "Presupuesto", "Metas (al menos una de las tres)"]}
          />
        )}
        </div>
        <footer className="border-t border-zinc-200 py-6 text-center text-xs text-zinc-500 dark:border-zinc-800">
          Fuente única: GSheet · {data.movimientos.length} movimientos · {data.taxonomia.length} categorías en taxonomía
        </footer>
      </main>

      {/* Panel de detalle de KPI (slide-over) */}
      <DetalleKpiPanel
        kpi={kpiSeleccionado?.kpi ?? null}
        movimientos={data.movimientos}
        contextoTitulo={kpiSeleccionado?.contexto}
        spreadsheetId={spreadsheetId}
        onClose={() => setKpiSeleccionado(null)}
      />
    </div>
  );
}
