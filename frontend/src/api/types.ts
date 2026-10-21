// Response types come from the generated schema (docs/openapi.json). Only the string unions that the schema leaves as `string` are narrowed here.
import type * as G from './schema.gen';
import type { RunEvent, OpenRequest, RunStatus } from './events';
export type { RunEvent, OpenRequest, RunStatus };

export type Lang = 'en' | 'fa';
export type Me = Omit<G.MeOut, 'language'> & { language: Lang };
export type SettingsOut = Omit<G.SettingsOut, 'language'> & { language: Lang };
export type Session = { authenticated: boolean; user?: G.UserOut | null };
export type Team = G.TeamOut;
export type LiaraCheckResult = G.CheckOut;
export type Limits = G.LimitsOut;
export type ProjectRow = Omit<G.ProjectRowOut, 'source_kind' | 'last_run'> & { source_kind: 'zip' | 'git'; last_run?: LastRun | null };
export type ProjectDetail = Omit<G.ProjectDetailOut, 'source_kind' | 'last_run' | 'runs'> & { source_kind: 'zip' | 'git'; last_run?: LastRun | null; runs?: RunRow[] };
export type LastRun = Omit<G.LastRunOut, 'mode' | 'status'> & { mode: 'deploy' | 'preview'; status: RunStatus };
export type RunRow = Omit<G.RunRowOut, 'mode' | 'status'> & { mode: 'deploy' | 'preview'; status: RunStatus };
export type RunResult = G.RunResultOut;
export type LiveInfo = G.LiveOut;
export type RunDetail = Omit<G.RunDetailOut, 'mode' | 'status' | 'open_requests'> & { mode: 'deploy' | 'preview'; status: RunStatus; open_requests?: OpenRequest[] };
