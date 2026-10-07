import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import type { Decision } from "../../api/types";
import { StatusPill } from "../../ui/StatusPill";
import { DECISIONS, PILL_WIDTH } from "./labels";

/** Причина решения, пока пользователь не уточнил её в карточке публикации. */
export const DEFAULT_USER_REASON = "Решение изменено пользователем без уточнения причины.";

/**
 * Кликабельный трёхпозиционный статус «Соответствует»: нажатие на сам статус открывает три решения,
 * выбор сразу сохраняется (shape §3.4). Причину можно уточнить в карточке публикации.
 */
export function DecisionControl({
  title,
  decision,
  onChange,
}: {
  title: string;
  decision: Decision;
  onChange: (decision: Decision) => void;
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`Соответствует ли «${title}»: ${DECISIONS[decision].label}. Изменить`}
        className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <StatusPill tone={DECISIONS[decision].tone} className={PILL_WIDTH}>
          <span className="flex-1 text-center">{DECISIONS[decision].label}</span>
          <ChevronDown aria-hidden className="-mr-1 size-4" />
        </StatusPill>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          className="z-50 min-w-52 rounded-control border border-line bg-surface p-1 shadow-md"
        >
          <DropdownMenu.RadioGroup
            value={decision}
            onValueChange={(value) => value !== decision && onChange(value as Decision)}
          >
            {(Object.keys(DECISIONS) as Decision[]).map((value) => (
              <DropdownMenu.RadioItem
                key={value}
                value={value}
                className="flex cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 outline-none data-highlighted:bg-accent-soft"
              >
                <span className="size-4">
                  <DropdownMenu.ItemIndicator>
                    <Check aria-hidden className="size-4" />
                  </DropdownMenu.ItemIndicator>
                </span>
                {DECISIONS[value].label}
              </DropdownMenu.RadioItem>
            ))}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
