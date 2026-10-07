import { CircleAlert, CircleCheck, CircleMinus, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

export type Tone = "success" | "neutral" | "warning" | "progress";

const TONES: Record<Tone, { box: string; icon: ReactNode }> = {
  success: {
    box: "bg-success-soft text-success-ink",
    icon: <CircleCheck aria-hidden className="size-5 fill-success text-surface" />,
  },
  neutral: {
    box: "bg-neutral-soft text-ink-muted",
    icon: <CircleMinus aria-hidden className="size-5 fill-ink-muted text-surface" />,
  },
  warning: {
    box: "bg-warning-soft text-warning-ink",
    icon: <CircleAlert aria-hidden className="size-5 fill-warning text-surface" />,
  },
  progress: {
    box: "bg-accent-soft text-accent",
    icon: <LoaderCircle aria-hidden className="size-5 animate-spin" />,
  },
};

/** Статус словами; цвет и иконка только дополняют текст. */
export function StatusPill({
  tone,
  children,
  className = "",
}: {
  tone: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full py-1 pr-3 pl-1.5 whitespace-nowrap ${TONES[tone].box} ${className}`}
    >
      {TONES[tone].icon}
      {children}
    </span>
  );
}
