import type { ButtonHTMLAttributes } from "react";

const variants = {
  primary: "bg-accent text-surface hover:bg-accent-strong disabled:bg-accent/40",
  secondary:
    "border border-line bg-surface text-ink hover:border-ink-muted disabled:text-ink-muted",
  outlineAccent: "border border-accent/40 bg-surface text-accent hover:bg-accent-soft",
  text: "text-ink hover:text-accent disabled:text-ink-muted",
  link: "text-accent hover:text-accent-strong disabled:text-ink-muted",
} as const;

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof variants };

export function Button({ variant = "secondary", className = "", ...props }: Props) {
  const padding = variant === "link" ? "" : "px-4 py-2.5";
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-2 rounded-control font-medium disabled:cursor-not-allowed ${padding} ${variants[variant]} ${className}`}
      {...props}
    />
  );
}
