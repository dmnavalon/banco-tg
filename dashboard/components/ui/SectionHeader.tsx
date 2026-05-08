import { ReactNode } from "react";

export function SectionHeader({
  title,
  description,
  question,
  right,
}: {
  title: string;
  description?: string;
  question?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-xl font-bold tracking-tight text-zinc-900 dark:text-zinc-50 sm:text-2xl">{title}</h2>
        {question && (
          <p className="mt-1 text-sm font-medium text-zinc-700 dark:text-zinc-300">
            <span className="text-zinc-400">¿</span>
            {question}
            <span className="text-zinc-400">?</span>
          </p>
        )}
        {description && <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{description}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}
