import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../../api/client";
import type { Schemas } from "../../api/types";
import { STUDY_KEY } from "../../app/useReachedStep";
import { Button } from "../../ui/Button";
import { LoadFailed } from "../../ui/LoadFailed";
import { Modal } from "../../ui/Modal";
import { BottomBar, PageHeader, Panel } from "../../ui/Page";
import { SaveStatus } from "../../ui/SaveStatus";
import { useAutosave } from "../../ui/useAutosave";
import {
  ObjectsSection,
  ParametersSection,
  ResourcesSection,
  SearchCriteriaSection,
} from "./sections";

type Fields = Schemas["CriteriaFields"];
type Draft = Schemas["CriteriaDraft"];

const CRITERIA_KEY = ["criteria"] as const;

/** Разделяет ответ сервера на редактируемые поля и вычисленное сервером состояние. */
function split(draft: Draft) {
  const {
    status,
    gaps,
    problems,
    confirmed_version,
    has_unconfirmed_changes,
    allowed_actions,
    saved_at,
    ...fields
  } = draft;
  return {
    fields: fields satisfies Fields,
    server: {
      status,
      gaps,
      problems,
      confirmed_version,
      has_unconfirmed_changes,
      allowed_actions,
      saved_at,
    },
  };
}

export function CriteriaPage() {
  const criteria = useQuery({
    queryKey: CRITERIA_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/criteria");
      if (!data) throw new Error("Критерии не загружены");
      return data;
    },
    refetchInterval: (query) => (query.state.data?.status === "preparing" ? 500 : false),
  });
  if (criteria.isError) return <LoadFailed what="критерии" onRetry={() => criteria.refetch()} />;
  if (!criteria.data) return null;
  return <CriteriaForm draft={criteria.data} />;
}

function CriteriaForm({ draft }: { draft: Draft }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const preparing = draft.status === "preparing";
  const [fields, setFields] = useState<Fields>(() => split(draft).fields);
  const fieldsRef = useRef(fields);
  fieldsRef.current = fields;
  const [server, setServer] = useState(() => split(draft).server);
  const [proposal, setProposal] = useState<Schemas["CriteriaProposal"] | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Меняется при замене всех критериев: секция сбрасывает введённый текст терминов.
  const [criteriaEpoch, setCriteriaEpoch] = useState(0);
  const dirtyWhilePreparing = useRef(false);

  const save = useCallback(async (value: Fields) => {
    const { data } = await api.PUT("/api/criteria", { body: value });
    if (data) setServer(split(data).server);
    return Boolean(data);
  }, []);
  const autosave = useAutosave(save, { initiallySaved: draft.saved_at !== null });
  const { schedule } = autosave;

  // Предложения пришли: берём их, сохраняя правки параметров, сделанные во время подготовки.
  const wasPreparing = useRef(preparing);
  useEffect(() => {
    if (wasPreparing.current && !preparing) {
      const merged = {
        ...fieldsRef.current,
        objects: draft.objects,
        search_criteria: draft.search_criteria,
      };
      setFields(merged);
      setServer(split(draft).server);
      if (dirtyWhilePreparing.current) schedule(merged);
      dirtyWhilePreparing.current = false;
    }
    wasPreparing.current = preparing;
  }, [preparing, draft, schedule]);

  function update(patch: Partial<Fields>) {
    const next = { ...fields, ...patch };
    setFields(next);
    setActionError(null);
    if (preparing) dirtyWhilePreparing.current = true;
    else schedule(next);
  }

  async function reformulate() {
    setActionError(null);
    try {
      const { data } = await api.POST("/api/criteria/reformulate");
      if (data) setProposal(data);
      else setActionError("Не удалось получить новую редакцию: сервер недоступен.");
    } catch {
      setActionError("Не удалось получить новую редакцию: сервер недоступен.");
    }
  }

  async function confirm() {
    setActionError(null);
    try {
      if (!(await autosave.flush())) {
        setActionError("Критерии не сохранены: сервер недоступен. Подтверждение отложено.");
        return;
      }
      const { data, error } = await api.POST("/api/criteria/confirm");
      if (!data) {
        if (error && "problems" in error) setServer(split(error).server);
        else setActionError("Не удалось подтвердить критерии: сервер недоступен.");
        return;
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: STUDY_KEY }),
        queryClient.invalidateQueries({ queryKey: CRITERIA_KEY }),
      ]);
      navigate("/search");
    } catch {
      setActionError("Не удалось подтвердить критерии: сервер недоступен.");
    }
  }

  const { problems, allowed_actions } = server;
  const canConfirm =
    !preparing && allowed_actions.includes("confirm") && autosave.state !== "saving";
  const version = server.confirmed_version
    ? `Версия ${server.confirmed_version}${server.has_unconfirmed_changes ? " · есть неподтверждённые изменения" : ""}`
    : "Черновик · ещё не подтверждён";
  const blockedReason = problems[0]?.message;

  return (
    <>
      <Panel className="px-6 pt-5">
        <PageHeader
          title="Критерии поиска"
          subtitle="Проверьте интерпретацию вопроса перед запуском поиска."
          aside={
            <>
              <span className="text-accent">{version}</span>
              <span aria-hidden className="h-5 w-px bg-line" />
              <SaveStatus state={autosave.state} savedText="Все изменения сохранены" />
            </>
          }
        />
        {server.gaps.length > 0 && (
          <div role="status" className="mt-4 rounded-control bg-warning-soft px-4 py-3">
            <p className="flex items-center gap-2 font-medium">
              <CircleAlert aria-hidden className="size-4 text-warning-ink" />
              {server.status === "failed"
                ? "Предложения не получены"
                : "Часть предложений не получена"}
            </p>
            <ul className="mt-1 list-disc pl-10">
              {server.gaps.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="mt-4">
          <ObjectsSection
            objects={fields.objects}
            preparing={preparing}
            problems={problems}
            update={update}
          />
          <ParametersSection fields={fields} update={update} />
          <SearchCriteriaSection
            key={criteriaEpoch}
            criteria={fields.search_criteria}
            preparing={preparing}
            canReformulate={!preparing && allowed_actions.includes("reformulate")}
            problems={problems}
            update={update}
            onReformulate={reformulate}
          />
          <ResourcesSection
            resources={fields.resources}
            problem={problems.find((p) => p.target === "resources")}
            update={update}
          />
        </div>
      </Panel>

      <BottomBar
        status={
          <span
            id="confirm-status"
            role="status"
            className="flex items-center gap-2 text-warning-ink"
          >
            {(actionError || blockedReason) && <CircleAlert aria-hidden className="size-4" />}
            {actionError ?? (blockedReason && `Подтверждение недоступно. ${blockedReason}`)}
          </span>
        }
      >
        <Button onClick={() => navigate("/question")}>Назад к вопросу</Button>
        <Button
          variant="primary"
          disabled={!canConfirm}
          aria-describedby="confirm-status"
          onClick={confirm}
        >
          Подтвердить и перейти к поиску
        </Button>
      </BottomBar>

      <ReformulateDialog
        proposal={proposal}
        onClose={() => setProposal(null)}
        onApply={(search_criteria) => {
          update({ search_criteria });
          setCriteriaEpoch((epoch) => epoch + 1);
          setProposal(null);
        }}
      />
    </>
  );
}

function ReformulateDialog({
  proposal,
  onClose,
  onApply,
}: {
  proposal: Schemas["CriteriaProposal"] | null;
  onClose: () => void;
  onApply: (criteria: Fields["search_criteria"]) => void;
}) {
  return (
    <Modal
      open={proposal !== null}
      onClose={onClose}
      title="Новая редакция критериев"
      description="Система предлагает критерии по вопросу. Применение заменит ваши текущие критерии поискового запроса."
    >
      <ul className="mt-4 max-h-80 space-y-2 overflow-y-auto">
        {proposal?.search_criteria.map((criterion) => (
          <li key={criterion.id} className="rounded-control border border-line px-3 py-2">
            <p className="font-medium">{criterion.label}</p>
            <p className="text-caption text-ink-muted">{criterion.terms.join("; ")}</p>
          </li>
        ))}
      </ul>
      <div className="mt-5 flex justify-end gap-3">
        <Button onClick={onClose}>Оставить мои критерии</Button>
        <Button variant="primary" onClick={() => proposal && onApply(proposal.search_criteria)}>
          Применить редакцию
        </Button>
      </div>
    </Modal>
  );
}
