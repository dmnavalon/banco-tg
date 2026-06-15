import { loadDashboardData } from "@/lib/backend-data";
import { calculateDashboard } from "@/lib/kpis";
import { Dashboard } from "@/components/Dashboard";

export const dynamic = "force-dynamic";

export default async function Page() {
  const data = await loadDashboardData();
  const kpis = calculateDashboard(data);
  return <Dashboard kpis={kpis} data={data} />;
}
