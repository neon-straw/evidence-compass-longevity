import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, LogOut } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import { SESSION_KEY, initials } from "./session";

export function ProfileMenu({ login }: { login: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [failed, setFailed] = useState(false);

  async function signOut() {
    const ok = await api
      .DELETE("/api/session")
      .then(({ response }) => response.ok)
      .catch(() => false);
    setFailed(!ok);
    if (!ok) return;
    queryClient.setQueryData(SESSION_KEY, null);
    navigate("/login", { replace: true });
  }

  return (
    <>
      {failed && (
        <span role="alert" className="text-caption text-warning-ink">
          Не удалось выйти. Повторите.
        </span>
      )}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          aria-label={`Профиль: ${login}`}
          className="flex items-center gap-1 rounded-full text-ink-muted outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-caption font-semibold text-accent">
            {initials(login)}
          </span>
          <ChevronDown aria-hidden className="size-4" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            sideOffset={6}
            className="min-w-48 rounded-control border border-line bg-surface p-1 shadow-md"
          >
            <DropdownMenu.Label className="px-2 py-1.5 text-caption text-ink-muted">
              {login}
            </DropdownMenu.Label>
            <DropdownMenu.Item
              onSelect={signOut}
              className="flex cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 outline-none data-highlighted:bg-accent-soft"
            >
              <LogOut aria-hidden className="size-4" />
              Выйти
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </>
  );
}
