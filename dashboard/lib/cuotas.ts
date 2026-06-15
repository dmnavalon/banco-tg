import { Movimiento } from "./types";
import { addMonthsUTC } from "./utils";

const RE_SUFIJO_CUOTA = /\s*\(\d+\s*\/\s*\d+\)\s*$/;

/** Expande compras en cuotas en un movimiento "virtual" por mes.
 *
 * El banco registra la compra UNA vez, con la fecha original y el monto total
 * (ej. compra de $14.471.416 en 10 cuotas el 05/12/2025). Sin expansión, la
 * cuota mensual solo contaba en el mes de la compra y las cuotas siguientes no
 * aparecían en NINGÚN mes. Aquí cada fila con cuotasTotal > 1 se REEMPLAZA por
 * una cuota k=1..N fechada en `fechaCompra + (k-1) meses`, cortando en el mes
 * calendario actual (sin proyectar cuotas futuras). Los virtuales llevan
 * montoCLP = montoMesCLP = cuota (nunca el total, para no contarlo N veces);
 * el total original queda en montoTotalCompraCLP.
 *
 * El caller debe reasignar `idx` después de expandir (los KPIs referencian
 * movimientos por posición en el array).
 *
 * Movido desde lib/sheets.ts en la migración a Firestore — lógica intacta. */
export function expandirCuotas(movimientos: Movimiento[], warnings: string[]): Movimiento[] {
  const ahora = new Date();
  const finMesActual = new Date(Date.UTC(ahora.getUTCFullYear(), ahora.getUTCMonth() + 1, 0));
  const out: Movimiento[] = [];

  for (const m of movimientos) {
    const n = m.cuotasTotal;
    if (!n || n <= 1) {
      out.push(m);
      continue;
    }
    if (!Number.isInteger(n) || n > 60) {
      warnings.push(`Movimiento "${m.descripcion}" (${m.fechaISO}) con cuotasTotal=${n} fuera de rango — no se expande.`);
      out.push(m);
      continue;
    }

    // "Cuota a pagar" llega en valor absoluto; montoCLP con signo.
    const signo = m.montoCLP >= 0 ? 1 : -1;
    const cuotaReal = m.cuotaAPagar !== null ? signo * Math.abs(m.cuotaAPagar) : null;
    // Sin cuota real: estimar total/N y cuadrar la última para que la suma
    // cierre exacta. Con cuota real NO se fuerza el cierre — las cuotas del
    // banco pueden incluir intereses y no sumar el total.
    const cuotaEstimada = Math.round(m.montoCLP / n);
    const descBase = m.descripcion.replace(RE_SUFIJO_CUOTA, "").trim();

    for (let k = 1; k <= n; k++) {
      const fechaK = addMonthsUTC(m.fecha, k - 1);
      if (k > 1 && fechaK > finMesActual) break;
      const montoK = cuotaReal ?? (k === n ? m.montoCLP - cuotaEstimada * (n - 1) : cuotaEstimada);
      const dd = String(fechaK.getUTCDate()).padStart(2, "0");
      const mm = String(fechaK.getUTCMonth() + 1).padStart(2, "0");
      out.push({
        ...m,
        fecha: fechaK,
        fechaISO: `${dd}/${mm}/${fechaK.getUTCFullYear()}`,
        descripcion: `${descBase} (${k}/${n})`,
        monto: montoK,
        montoCLP: montoK,
        montoMesCLP: montoK,
        cuotaActual: k,
        esCuotaVirtual: true,
        cuotaK: k,
        montoTotalCompraCLP: m.montoCLP,
      });
    }
  }
  return out.map((m, i) => ({ ...m, idx: i }));
}
