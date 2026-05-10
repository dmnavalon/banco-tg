<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

## Notas concretas para Next 16.2 (esta versión)

- **Route handlers `params` es Promise**: en archivos `app/.../[id]/route.ts`, la firma es `({ params }: { params: Promise<{ id: string }> })` y dentro hay que `const { id } = await params`. Aplica a `page.tsx` y `route.ts`. Ver `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/route.md`.
- **Middleware se llama `proxy`**: el archivo es `proxy.ts` (no `middleware.ts`). Ver `dashboard/proxy.ts`. La función exportada es `proxy(req)`.
- **Dynamic rendering**: rutas que dependen de fetch real-time deben tener `export const dynamic = "force-dynamic"`. Aplicado en `app/movimientos/page.tsx` y todos los `app/api/movimientos/**/route.ts`.

## Feature "Movimientos"

Sección agregada en 2026-05-09. Revisión masiva de movimientos. Detalles en `../HANDOFF.md` sección 17.

- Página: `app/movimientos/page.tsx` + `components/movimientos/MovimientosTable.tsx`.
- Route handlers proxy: `app/api/movimientos/`, `app/api/categorias/`. Llaman al backend Python en Railway con `BACKEND_API_TOKEN` (server-side only — nunca al navegador).
- Activación: env vars `NEXT_PUBLIC_ENABLE_MOVIMIENTOS_REVIEW=true`, `BACKEND_API_URL`, `BACKEND_API_TOKEN`.
- Sin nuevas dependencies — usa fetch nativo, React 19, lucide-react que ya estaba.
