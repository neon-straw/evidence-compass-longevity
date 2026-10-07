import * as Tooltip from "@radix-ui/react-tooltip";
import { useQuery } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";
import { NavLink } from "react-router";
import { resultQuery } from "../screens/result/format";
import { STEPS, type Step } from "./steps";
import { useReachedStep } from "./useReachedStep";

const tab = "flex items-center gap-1.5 border-b-2 px-3 py-3";

function LockedStep({ step }: { step: Step }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <a
          role="link"
          tabIndex={0}
          aria-disabled="true"
          aria-describedby={`step-locked-${step.path}`}
          className={`${tab} cursor-not-allowed border-transparent text-ink-muted`}
        >
          {step.label}
        </a>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="bottom"
          sideOffset={4}
          className="rounded-control bg-ink px-2.5 py-1.5 text-caption text-surface"
        >
          {step.lockedReason}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function StepNav() {
  const reached = useReachedStep() ?? 0;
  // Наблюдаем уже полученный результат, не запускаем запрос из навигации.
  const result = useQuery({ ...resultQuery, enabled: false });

  return (
    <nav aria-label="Шаги исследования" className="border-b border-line bg-surface px-4">
      <Tooltip.Provider delayDuration={200}>
        <ol className="flex gap-2">
          {STEPS.map((step, index) => (
            <li key={step.path}>
              {index <= reached ? (
                <NavLink
                  to={`/${step.path}`}
                  className={({ isActive }) =>
                    `${tab} ${isActive ? "border-accent font-medium text-accent" : "border-transparent text-ink hover:text-accent"}`
                  }
                >
                  {(index < reached || (step.path === "result" && !!result.data)) && (
                    <CircleCheck aria-hidden className="size-5 fill-success text-surface" />
                  )}
                  {step.label}
                </NavLink>
              ) : (
                <LockedStep step={step} />
              )}
            </li>
          ))}
        </ol>
      </Tooltip.Provider>
      {STEPS.slice(reached + 1).map((step) => (
        <span key={step.path} id={`step-locked-${step.path}`} hidden>
          {step.lockedReason}
        </span>
      ))}
    </nav>
  );
}
