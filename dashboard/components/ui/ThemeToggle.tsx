"use client";
import { useEffect, useRef, useState } from "react";
import { Sun, Moon, Monitor, Check } from "lucide-react";
import { cn } from "@/lib/utils";

type Theme = "light" | "dark" | "system";

const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System default", Icon: Monitor },
];

function applyTheme(theme: Theme) {
  const isDark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", isDark);
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stored = (localStorage.getItem("theme") as Theme) || "system";
    setTheme(stored);
    setMounted(true);

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      if ((localStorage.getItem("theme") as Theme) === "system") applyTheme("system");
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // Cerrar al click fuera (mobile / accesibilidad por click)
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const openNow = () => {
    if (closeTimer.current) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setOpen(true);
  };
  const scheduleClose = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(false), 120);
  };

  const select = (next: Theme) => {
    setTheme(next);
    localStorage.setItem("theme", next);
    applyTheme(next);
    setOpen(false);
  };

  if (!mounted) {
    return <div className="h-7 w-7" aria-hidden="true" />;
  }

  const Active = OPTIONS.find((o) => o.value === theme)!;

  return (
    <div
      ref={wrapperRef}
      className="relative"
      onMouseEnter={openNow}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Tema: ${Active.label}`}
        className={cn(
          "inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-600 transition-colors",
          "hover:bg-zinc-100 hover:text-zinc-900",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500",
          "dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100",
          open && "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100",
        )}
      >
        <Active.Icon className="h-3.5 w-3.5" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1 min-w-[160px] overflow-hidden rounded-lg border border-zinc-200 bg-white py-1 shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
        >
          {OPTIONS.map((o) => {
            const isActive = o.value === theme;
            return (
              <button
                key={o.value}
                role="menuitem"
                type="button"
                onClick={() => select(o.value)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors",
                  "hover:bg-zinc-100 dark:hover:bg-zinc-800",
                  isActive ? "text-zinc-900 dark:text-zinc-50" : "text-zinc-600 dark:text-zinc-400",
                )}
              >
                <o.Icon className="h-3.5 w-3.5" />
                <span className="flex-1">{o.label}</span>
                {isActive && <Check className="h-3 w-3 text-blue-600 dark:text-blue-400" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
