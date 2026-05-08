import { loadDashboardData } from "@/lib/sheets";
import { calculateDashboard } from "@/lib/kpis";
import { Dashboard } from "@/components/Dashboard";

export const dynamic = "force-dynamic";

export default async function Page() {
  const data = await loadDashboardData();
  const kpis = calculateDashboard(data);
  const spreadsheetId = process.env.GSHEET_SPREADSHEET_ID ?? "";
  return <Dashboard kpis={kpis} data={data} spreadsheetId={spreadsheetId} />;
}
