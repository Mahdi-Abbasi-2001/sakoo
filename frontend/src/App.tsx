import { lazy, Suspense, type ReactElement } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Shell } from './components/Shell';
import { useAuth } from './auth/AuthContext';
import { useI18n } from './i18n';
import { AuthPage } from './pages/AuthPage';
import { Settings } from './pages/Settings';
import { Dashboard } from './pages/Dashboard';
import { ReadyMade } from './pages/ReadyMade';
import { NewDeployment } from './pages/NewDeployment';
import { ProjectPage } from './pages/ProjectPage';
import { RunPage } from './pages/RunPage';
import { AgentStart } from './pages/AgentStart';
import { DemoPage } from './pages/DemoPage';

const Landing = lazy(() => import('./landing/Landing'));

function Protected({ children }: { children: ReactElement }) {
  const { state } = useAuth();
  const { t } = useI18n();
  const loc = useLocation();
  if (state.status === 'loading') return <div className="page"><div className="sk" style={{ height: 40, maxWidth: 360 }} role="status" aria-label={t('a11y.loading')} /></div>;
  // `/` is the landing page for visitors and the dashboard for signed-in users
  if (state.status === 'anon' && loc.pathname === '/') return <Suspense fallback={null}><Landing /></Suspense>;
  if (state.status === 'anon') return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route element={<Shell bare />}>
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/register" element={<AuthPage mode="register" />} />
      </Route>
      <Route path="/welcome" element={<Suspense fallback={null}><Landing /></Suspense>} />
      <Route element={<Shell bare />}>
        <Route path="/demo" element={<Navigate to="/demo/waiting" replace />} />
        <Route path="/demo/:name" element={<DemoPage />} />
      </Route>
      <Route element={<Protected><Shell /></Protected>}>
        <Route path="/runs/:id" element={<RunPage />} />
        <Route path="/" element={<Dashboard />} />
        <Route path="/agent" element={<AgentStart />} />
        <Route path="/new" element={<NewDeployment />} />
        <Route path="/apps" element={<ReadyMade />} />
        <Route path="/projects/:id" element={<ProjectPage />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/connect" element={<Navigate to="/settings" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
