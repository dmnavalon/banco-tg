import { EmptyState } from "../ui/EmptyState";
import { SectionHeader } from "../ui/SectionHeader";
import { Database } from "lucide-react";

export function PlaceholderSection({
  title,
  question,
  pestañasRequeridas,
}: {
  title: string;
  question: string;
  pestañasRequeridas: string[];
}) {
  return (
    <div>
      <SectionHeader title={title} question={question} />
      <EmptyState
        icon={<Database className="h-6 w-6" />}
        title="No hay data"
        description={`Para calcular esta sección, completá las pestañas: ${pestañasRequeridas.join(", ")} en el GSheet.`}
      />
    </div>
  );
}
