import { Navigate, createBrowserRouter } from "react-router";
import { AppShell, RootLayout } from "./AppShell";
import { LoginPage } from "./LoginPage";
import { CriteriaPage } from "../screens/criteria/CriteriaPage";
import { PreparationLogPage } from "../screens/preparation/PreparationLogPage";
import { PreparationPage } from "../screens/preparation/PreparationPage";
import { QuestionPage } from "../screens/QuestionPage";
import { CoveragePage } from "../screens/result/CoveragePage";
import { ResultPage } from "../screens/result/ResultPage";
import { SearchPage } from "../screens/SearchPage";
import { SelectionPage } from "../screens/selection/SelectionPage";
import { StepPage } from "./StepPage";
import { STEPS } from "./steps";
import { useReachedStep } from "./useReachedStep";

const SCREENS: Record<string, () => React.ReactNode> = {
  question: QuestionPage,
  criteria: CriteriaPage,
  search: SearchPage,
  selection: SelectionPage,
  preparation: PreparationPage,
  result: ResultPage,
};

/** Экран шага (или его отдельное представление) доступен, только если шаг достигнут. */
function StepRoute({ index, screen }: { index: number; screen?: () => React.ReactNode }) {
  const reached = useReachedStep();
  if (reached === null) return null;
  if (index > reached) return <Navigate to={`/${STEPS[reached].path}`} replace />;
  const Screen = screen ?? SCREENS[STEPS[index].path];
  return Screen ? <Screen /> : <StepPage step={STEPS[index]} />;
}

export const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [
      { path: "/login", element: <LoginPage /> },
      {
        path: "/",
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to={STEPS[0].path} replace /> },
          ...STEPS.map((step, index) => ({
            path: step.path,
            element: <StepRoute index={index} />,
          })),
          {
            path: "result/coverage",
            element: (
              <StepRoute
                index={STEPS.findIndex((s) => s.path === "result")}
                screen={CoveragePage}
              />
            ),
          },
          {
            path: "preparation/log",
            element: (
              <StepRoute
                index={STEPS.findIndex((s) => s.path === "preparation")}
                screen={PreparationLogPage}
              />
            ),
          },
          { path: "*", element: <Navigate to={STEPS[0].path} replace /> },
        ],
      },
    ],
  },
]);
