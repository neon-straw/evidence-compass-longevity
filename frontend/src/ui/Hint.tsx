import * as Tooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

/** Всплывающая подсказка у элемента; `children` — один фокусируемый элемент-триггер. */
export function Hint({ text, children }: { text: string; children: ReactNode }) {
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            side="top"
            sideOffset={4}
            className="z-50 max-w-xs rounded-control bg-ink px-2.5 py-1.5 text-caption text-surface"
          >
            {text}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
