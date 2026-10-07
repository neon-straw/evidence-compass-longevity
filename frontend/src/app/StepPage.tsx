import type { Step } from "./steps";

export function StepPage({ step }: { step: Step }) {
  return (
    <section className="rounded-panel border border-line bg-surface px-6 py-5">
      <h1 className="text-title font-semibold">{step.label}</h1>
    </section>
  );
}
