import { ReactNode } from "react";
import { Database } from "lucide-react";
import { Card } from "./Card";

export function EmptyState({
  title,
  description,
  cta,
  icon,
}: {
  title: string;
  description: string;
  cta?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <Card padding="lg" className="border-dashed">
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-zinc-100 text-zinc-400 dark:bg-zinc-900 dark:text-zinc-600">
          {icon ?? <Database className="h-6 w-6" />}
        </div>
        <h3 className="mb-1 text-base font-semibold text-zinc-900 dark:text-zinc-100">{title}</h3>
        <p className="max-w-md text-sm text-zinc-500 dark:text-zinc-400">{description}</p>
        {cta && <div className="mt-4">{cta}</div>}
      </div>
    </Card>
  );
}
