import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

/** Центрированный диалог для коротких ограниченных действий (shape §7). */
export function Modal({
  open,
  onClose,
  title,
  description,
  width = "w-[40rem]",
  onCloseAutoFocus,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  width?: string;
  onCloseAutoFocus?: (event: Event) => void;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-30 bg-ink/30" />
        <Dialog.Content
          onCloseAutoFocus={onCloseAutoFocus}
          className={`fixed top-1/2 left-1/2 z-40 ${width} max-w-[90vw] -translate-1/2 rounded-panel bg-surface p-6 shadow-lg`}
        >
          <Dialog.Title className="text-lg font-semibold">{title}</Dialog.Title>
          {description && (
            <Dialog.Description className="mt-1 text-ink-muted">{description}</Dialog.Description>
          )}
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
