import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type { Schemas } from "../api/types";
import { STUDY_KEY } from "../app/useReachedStep";
import { Button } from "../ui/Button";
import { LoadFailed } from "../ui/LoadFailed";
import { Panel } from "../ui/Page";
import { SaveStatus } from "../ui/SaveStatus";
import { useAutosave } from "../ui/useAutosave";

const QUESTION_KEY = ["question"] as const;
const EXAMPLE =
  "Какие численные показатели продолжительности жизни опубликованы для голого землекопа и гренландской акулы?";

type Failure =
  | { kind: "unsupported"; details: Schemas["UnsupportedQuestion"] }
  | { kind: "unavailable"; message: string };

export function QuestionPage() {
  const question = useQuery({
    queryKey: QUESTION_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/question");
      if (!data) throw new Error("Вопрос не загружен");
      return data;
    },
  });
  if (question.isError) return <LoadFailed what="вопрос" onRetry={() => question.refetch()} />;
  if (!question.data) return null;
  return <QuestionForm initial={question.data} />;
}

function QuestionForm({ initial }: { initial: Schemas["QuestionDraft"] }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [text, setText] = useState(initial.text);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);

  const save = useCallback(
    async (value: string) => {
      const { data } = await api.PUT("/api/question", { body: { text: value } });
      if (data) queryClient.setQueryData(QUESTION_KEY, data);
      return Boolean(data);
    },
    [queryClient],
  );
  const autosave = useAutosave(save, { initiallySaved: initial.saved_at !== null });

  function change(value: string) {
    setText(value);
    setFailure(null);
    autosave.schedule(value);
  }

  async function openCriteria(request: () => Promise<{ ok: boolean; failure?: Failure }>) {
    setBusy(true);
    setFailure(null);
    try {
      if (!(await autosave.flush())) {
        setFailure({ kind: "unavailable", message: "Черновик вопроса не сохранён." });
        return;
      }
      const result = await request();
      if (!result.ok) {
        setFailure(result.failure ?? { kind: "unavailable", message: "Сервер недоступен." });
        return;
      }
      await queryClient.invalidateQueries({ queryKey: STUDY_KEY });
      navigate("/criteria");
    } catch {
      setFailure({ kind: "unavailable", message: "Сервер недоступен." });
    } finally {
      setBusy(false);
    }
  }

  const prepare = () =>
    openCriteria(async () => {
      const { data, error, response } = await api.POST("/api/question/prepare-criteria");
      if (data) return { ok: true };
      if (response.status === 422 && error && "example" in error) {
        return { ok: false, failure: { kind: "unsupported", details: error } };
      }
      const message = error && "message" in error ? error.message : "Сервер недоступен.";
      return { ok: false, failure: { kind: "unavailable", message } };
    });

  const startManually = () =>
    openCriteria(async () => ({
      ok: Boolean((await api.POST("/api/criteria/start-manually")).data),
    }));

  const empty = text.trim() === "";

  return (
    <Panel className="px-8 py-7">
      <h1 className="text-title font-semibold">Исследовательский вопрос</h1>
      <p className="mt-2 text-lg text-ink-muted">
        Сформулируйте вопрос об опубликованных показателях возраста или продолжительности жизни
        животных.
      </p>

      <textarea
        ref={field}
        aria-label="Исследовательский вопрос"
        aria-invalid={failure?.kind === "unsupported"}
        aria-describedby={failure ? "question-failure" : undefined}
        value={text}
        onChange={(event) => change(event.target.value)}
        placeholder={`Например: ${EXAMPLE}`}
        rows={7}
        className="mt-6 max-h-[60vh] min-h-56 w-full resize-y rounded-control border border-line px-4 py-3 text-xl leading-relaxed outline-none placeholder:text-ink-muted/70 focus:border-accent focus:ring-2 focus:ring-accent-soft aria-invalid:border-warning"
      />

      <div className="mt-3 flex min-h-7 flex-wrap items-center gap-x-6 gap-y-1">
        <SaveStatus state={autosave.state} savedText="Черновик сохранён" />
        {empty && (
          <p className="text-ink-muted">
            Опишите виды животных и нужные показатели одним-двумя предложениями.
          </p>
        )}
      </div>

      {failure?.kind === "unsupported" && (
        <div
          id="question-failure"
          role="alert"
          className="mt-4 rounded-control bg-warning-soft px-4 py-3"
        >
          <p className="flex items-start gap-2 font-medium">
            <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warning-ink" />
            {failure.details.message}
          </p>
          <p className="mt-1 pl-6 text-ink-muted">
            Переформулируйте вопрос, например: «{failure.details.example}»
          </p>
        </div>
      )}
      {failure?.kind === "unavailable" && (
        <div
          id="question-failure"
          role="alert"
          className="mt-4 rounded-control bg-warning-soft px-4 py-3"
        >
          <p>Не удалось подготовить критерии: {failure.message} Вопрос сохранён и не изменился.</p>
          <div className="mt-2 flex gap-4">
            <Button variant="link" disabled={busy} onClick={prepare}>
              Повторить
            </Button>
            <Button variant="link" disabled={busy} onClick={startManually}>
              Заполнить критерии вручную
            </Button>
          </div>
        </div>
      )}

      <div className="mt-6 flex justify-end gap-3 border-t border-line pt-6">
        <Button
          variant="text"
          disabled={empty}
          onClick={() => {
            change("");
            field.current?.focus();
          }}
        >
          Очистить
        </Button>
        <Button variant="primary" disabled={empty || busy} onClick={prepare}>
          Подготовить критерии
        </Button>
      </div>
    </Panel>
  );
}
