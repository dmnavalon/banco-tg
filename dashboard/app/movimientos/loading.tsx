export default function Loading() {
  return (
    <main className="min-h-screen bg-slate-50 px-6 py-12">
      <div className="mx-auto max-w-2xl rounded-lg border border-slate-200 bg-white p-8 text-slate-600">
        <div className="h-4 w-40 animate-pulse rounded bg-slate-200" />
        <div className="mt-4 h-3 w-full animate-pulse rounded bg-slate-100" />
        <div className="mt-2 h-3 w-3/4 animate-pulse rounded bg-slate-100" />
      </div>
    </main>
  );
}
