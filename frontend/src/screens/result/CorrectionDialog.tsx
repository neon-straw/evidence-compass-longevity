import { type FormEvent, useState } from "react";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { Modal } from "../../ui/Modal";
import { type Correction, rowLabel } from "./format";

type Row = Schemas["MeasureRow"];

const field =
  "mt-1 w-full rounded-control border border-line px-3 py-2 outline-none focus:border-accent";

/** Исправление значения строки; история сохраняется, краткий вывод устаревает до обновления. */
export function CorrectionDialog({
  row,
  onClose,
  onSave,
}: {
  row: Row | null;
  onClose: () => void;
  onSave: (correction: Correction) => Promise<string | null>;
}) {
  return (
    <Modal
      open={row !== null}
      onClose={onClose}
      title={`Исправить извлечение · строка ${row ? rowLabel(row.number) : ""}`}
      description="Исправление сохранит историю: прежнее значение останется в ревизиях. Краткий вывод станет устаревшим до явного обновления результата."
    >
      {row && <CorrectionForm key={row.id} row={row} onClose={onClose} onSave={onSave} />}
    </Modal>
  );
}

function CorrectionForm({
  row,
  onClose,
  onSave,
}: {
  row: Row;
  onClose: () => void;
  onSave: (correction: Correction) => Promise<string | null>;
}) {
  const [value, setValue] = useState(row.value_original);
  const [unit, setUnit] = useState(row.unit_original);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const failure = await onSave({
      value: value.trim(),
      unit: unit.trim(),
      comment: comment.trim(),
    });
    setBusy(false);
    if (failure) setError(failure);
    else onClose();
  }

  return (
    <form onSubmit={submit} className="mt-4 space-y-3">
      <p className="text-caption text-ink-muted">
        {row.species_name_in_source} · {row.definition}
      </p>
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-caption text-ink-muted">Значение как в источнике</span>
          <input
            required
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className={field}
          />
        </label>
        <label className="block">
          <span className="text-caption text-ink-muted">Единицы как в источнике</span>
          <input
            required
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            className={field}
          />
        </label>
      </div>
      <label className="block">
        <span className="text-caption text-ink-muted">
          Что исправлено и по какому месту источника
        </span>
        <textarea
          required
          rows={3}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          className={field}
        />
      </label>
      {error && (
        <p role="alert" className="text-warning-ink">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-3 pt-2">
        <Button onClick={onClose}>Отмена</Button>
        <Button
          type="submit"
          variant="primary"
          disabled={busy || !value.trim() || !unit.trim() || !comment.trim()}
        >
          Сохранить исправление
        </Button>
      </div>
    </form>
  );
}
