import { type FormEvent, useState } from "react";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { Modal } from "../../ui/Modal";

type Doc = Schemas["DocumentRow"];

/** Новая версия документа или новое приложение работы. */
export type UploadTarget =
  { kind: "document"; document: Doc } | { kind: "supplement"; work: Schemas["DocumentWork"] };

function uploadText(target: UploadTarget | null) {
  if (!target) return { title: "", description: "", work: "" };
  if (target.kind === "supplement") {
    return {
      title: "Загрузить приложение",
      description:
        "Файл станет новым приложением работы; до подтверждения принадлежности он не служит основанием.",
      work: target.work.label,
    };
  }
  const { document } = target;
  return {
    title: "Загрузить документ вручную",
    description: document.version
      ? `Загрузка создаст версию v${document.version + 1} документа «${document.name}»; прежняя версия сохранится в истории.`
      : `Файл станет первой версией документа «${document.name}».`,
    work: document.publication_label,
  };
}

const ACCEPTED = ".pdf,.xml,.csv,.xlsx,.docx";

/** Ручная загрузка файла; готовый документ получает новую версию, прежняя сохраняется. */
export function UploadDialog({
  target,
  onClose,
  onUpload,
}: {
  target: UploadTarget | null;
  onClose: () => void;
  onUpload: (file: File) => Promise<string | null>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const text = uploadText(target);

  function close() {
    setFile(null);
    setError(null);
    onClose();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    const failure = await onUpload(file);
    setBusy(false);
    if (failure) setError(failure);
    else close();
  }

  return (
    <Modal open={target !== null} onClose={close} title={text.title} description={text.description}>
      <form onSubmit={submit} className="mt-4">
        <label className="block">
          <span className="text-caption text-ink-muted">Файл (PDF, XML, CSV, XLSX, DOCX)</span>
          <input
            type="file"
            accept={ACCEPTED}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="mt-1 block w-full rounded-control border border-line px-3 py-2"
          />
        </label>
        <p className="mt-2 text-caption text-ink-muted">
          Документ относится к работе «{text.work}». Автоматическое чтение любого файла не
          гарантируется: статус разбора появится после загрузки.
        </p>
        {error && (
          <p role="alert" className="mt-3 text-warning-ink">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-3">
          <Button onClick={close}>Отмена</Button>
          <Button type="submit" variant="primary" disabled={!file || busy}>
            Загрузить
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** Подтверждение, что файл относится к работе: до решения он не служит основанием. */
export function IdentityDialog({
  document,
  onClose,
  onDecide,
}: {
  document: Doc | null;
  onClose: () => void;
  onDecide: (belongs: boolean) => Promise<string | null>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function close() {
    setError(null);
    onClose();
  }

  async function decide(belongs: boolean) {
    setBusy(true);
    const failure = await onDecide(belongs);
    setBusy(false);
    if (failure) setError(failure);
    else close();
  }

  return (
    <Modal
      open={document !== null}
      onClose={close}
      title="Проверить документ"
      description={`${document?.status_detail ?? ""} Файл «${document?.name ?? ""}» — это документ работы «${document?.publication_label ?? ""}»?`}
      width="w-[34rem]"
    >
      <p className="mt-3 text-caption text-ink-muted">
        Пока принадлежность не подтверждена, документ не используется как основание.
      </p>
      {error && (
        <p role="alert" className="mt-3 text-warning-ink">
          {error}
        </p>
      )}
      <div className="mt-5 flex justify-end gap-3">
        <Button disabled={busy} onClick={() => decide(false)}>
          Нет, это другой файл
        </Button>
        <Button variant="primary" disabled={busy} onClick={() => decide(true)}>
          Да, документ этой работы
        </Button>
      </div>
    </Modal>
  );
}
