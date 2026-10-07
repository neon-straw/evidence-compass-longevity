import { type FormEvent, useState } from "react";
import { api } from "../../api/client";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { Modal } from "../../ui/Modal";
import { useSelectionActions } from "./actions";

type Candidate = Schemas["LookupCandidate"];

const detail = (error: unknown) =>
  error && typeof error === "object" && "detail" in error && typeof error.detail === "string"
    ? error.detail
    : "сервер недоступен";

/** Ручное добавление по DOI, PMID или названию; запись навсегда помечается «Добавлено вручную». */
export function ManualAddDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const actions = useSelectionActions();
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function close() {
    setQuery("");
    setCandidates(null);
    setError(null);
    onClose();
  }

  async function lookup(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { data, error: failure } = await api.POST("/api/publications/lookup", {
        body: { query },
      });
      if (data) setCandidates(data);
      else setError(`Поиск записи не выполнен: ${detail(failure)}.`);
    } catch {
      setError("Поиск записи не выполнен: сервер недоступен.");
    } finally {
      setBusy(false);
    }
  }

  async function add(candidate: Candidate) {
    setBusy(true);
    setError(null);
    try {
      const { data, error: failure } = await api.POST("/api/publications/manual", {
        body: { doi: candidate.doi, pmid: candidate.pmid },
      });
      if (!data) {
        setError(`Запись не добавлена: ${detail(failure)}.`);
        return;
      }
      await actions.refresh();
      close();
    } catch {
      setError("Запись не добавлена: сервер недоступен.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Добавить публикацию вручную"
      description="Запись получит постоянную метку «Добавлено вручную» и не засчитывается как находка автоматического поиска."
    >
      <form onSubmit={lookup} className="mt-4 flex gap-2">
        <input
          aria-label="DOI, PMID или название"
          placeholder="DOI, PMID или название"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-full rounded-control border border-line px-3 py-2 outline-none focus:border-accent"
        />
        <Button type="submit" disabled={busy || query.trim().length < 3}>
          Найти
        </Button>
      </form>
      <p className="mt-1 text-caption text-ink-muted">Не меньше трёх символов.</p>
      {error && (
        <p role="alert" className="mt-3 text-warning-ink">
          {error}
        </p>
      )}
      {candidates && candidates.length === 0 && (
        <p role="status" className="mt-3 text-ink-muted">
          Ничего не найдено. Проверьте DOI или PMID.
        </p>
      )}
      {candidates && candidates.length > 0 && (
        <ul className="mt-3 max-h-72 space-y-2 overflow-y-auto">
          {candidates.map((candidate) => (
            <li
              key={candidate.doi ?? candidate.title}
              className="flex items-center justify-between gap-4 rounded-control border border-line px-3 py-2"
            >
              <div>
                <p className="font-medium">{candidate.title}</p>
                <p className="text-caption text-ink-muted">
                  {[candidate.year, candidate.doi, candidate.pmid].filter(Boolean).join(" · ")}
                </p>
              </div>
              {candidate.already_listed ? (
                <span className="shrink-0 text-caption text-ink-muted">Уже в списке</span>
              ) : (
                <Button
                  variant="primary"
                  disabled={busy}
                  aria-label={`Добавить «${candidate.title}»`}
                  onClick={() => add(candidate)}
                >
                  Добавить
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-5 flex justify-end">
        <Button onClick={close}>Закрыть</Button>
      </div>
    </Modal>
  );
}
