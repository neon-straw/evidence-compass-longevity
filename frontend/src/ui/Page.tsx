import type { ReactNode } from "react";

/** Общая раскладка экрана шага (макеты docs/ux/mocks/02–07). */
export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-panel border border-line bg-surface ${className}`}>
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  aside,
}: {
  title: string;
  subtitle?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-6">
      <div>
        <h1 className="text-title font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-lg text-ink-muted">{subtitle}</p>}
      </div>
      {aside && <div className="flex shrink-0 items-center gap-4">{aside}</div>}
    </header>
  );
}

/** Закреплённая нижняя панель: статус слева, действия справа. */
export function BottomBar({ status, children }: { status?: ReactNode; children: ReactNode }) {
  return (
    <div className="sticky bottom-0 z-10 -mx-gutter -mb-gutter mt-auto flex items-center gap-4 border-t border-line bg-surface px-gutter py-4">
      <div className="min-w-0 flex-1 text-ink-muted">{status}</div>
      <div className="flex shrink-0 items-center gap-3">{children}</div>
    </div>
  );
}
