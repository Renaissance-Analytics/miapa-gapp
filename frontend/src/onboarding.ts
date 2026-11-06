import { csrfToken } from './api.js';

export const steps = ['display_name', 'workspace', 'engagement_name', 'entity_name', 'currency', 'reporting_basis', 'effective_from', 'review', 'complete'] as const;
export type SetupStep = typeof steps[number];
export type OnboardingState = {
  setup_id: string;
  revision: number; step: SetupStep; draft: Record<string, string | number | null>;
  messages: { role: 'guide' | 'user' | 'agent'; text: string }[];
  choices: { value: string; label: string }[]; entity_id: string | null;
  deferred: boolean; provider_ready: false; can_edit: boolean;
  /** The current step's question and what it is for. */
  question: string; explanation: string;
  /** Every step on this person's path, each with a plain-language purpose. */
  steps: { id: SetupStep; title: string; explanation: string }[];
};
export type SetupResult = { state: 'ready'; value: OnboardingState } | { state: 'unavailable' | 'conflict' | 'denied' | 'invalid' };
const options = (): RequestInit => ({ credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10_000) });
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(value);

export async function readFirstRun(fetcher: typeof fetch = fetch): Promise<boolean | null> {
  try {
    const response = await fetcher('/api/setup/', options());
    if (response.status !== 200) return null;
    const value: unknown = await response.json();
    return object(value) && typeof value.setup_required === 'boolean' ? value.setup_required : null;
  } catch { return null; }
}

export async function createAdmin(username: string, password: string, confirmation: string, fetcher: typeof fetch = fetch): Promise<'created' | 'closed' | 'invalid' | 'unavailable'> {
  if (!username || username.length > 150 || password.length < 12 || password.length > 1024 || password !== confirmation) return 'invalid';
  try {
    const token = await csrfToken(fetcher);
    const response = await fetcher('/api/setup/', { ...options(), method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
      body: JSON.stringify({ username, password, password_confirmation: confirmation }) });
    if (response.status === 409) return 'closed';
    if (response.status === 400) return 'invalid';
    if (response.status !== 201) return 'unavailable';
    const value: unknown = await response.json();
    return object(value) && object(value.user) && typeof value.user.id === 'string' && /^[0-9]+$/.test(value.user.id) ? 'created' : 'unavailable';
  } catch { return 'unavailable'; }
}

function parse(value: unknown): OnboardingState {
  if (!object(value) || !uuid(value.setup_id) || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0 || Number(value.revision) >= 2_000_000_000 ||
      !steps.includes(value.step as SetupStep) || !object(value.draft) || !Array.isArray(value.messages) || value.messages.length > 19 ||
      !Array.isArray(value.choices) || value.choices.length > 10002 || typeof value.deferred !== 'boolean' ||
      value.provider_ready !== false || typeof value.can_edit !== 'boolean' || (value.entity_id !== null && !uuid(value.entity_id)) ||
      typeof value.question !== 'string' || value.question.length > 512 || typeof value.explanation !== 'string' || value.explanation.length > 512 ||
      !Array.isArray(value.steps) || value.steps.length > steps.length) throw Error('Invalid setup');
  const allowed = ['display_name', 'workspace', 'engagement_name', 'entity_name', 'currency', 'reporting_basis', 'effective_from', 'entity_id', 'entity_revision'];
  const draft: OnboardingState['draft'] = {};
  for (const [key, item] of Object.entries(value.draft)) {
    if (!allowed.includes(key) || !(item === null || (typeof item === 'string' && item.length <= 128) || (key === 'entity_revision' && Number.isSafeInteger(item) && Number(item) >= 0))) throw Error('Invalid draft');
    draft[key] = item as string | number | null;
  }
  const messages = value.messages.map(item => {
    if (!object(item) || !['guide', 'user', 'agent'].includes(item.role as string) || typeof item.text !== 'string' || item.text.length > 512) throw Error('Invalid message');
    return { role: item.role, text: item.text } as OnboardingState['messages'][number];
  });
  const choices = value.choices.map(item => {
    if (!object(item) || typeof item.value !== 'string' || item.value.length > 128 || typeof item.label !== 'string' || item.label.length > 300) throw Error('Invalid choice');
    return { value: item.value, label: item.label };
  });
  const path = value.steps.map(item => {
    if (!object(item) || !steps.includes(item.id as SetupStep) || typeof item.title !== 'string' || item.title.length > 80 ||
        typeof item.explanation !== 'string' || item.explanation.length > 512) throw Error('Invalid step');
    return { id: item.id as SetupStep, title: item.title, explanation: item.explanation };
  });
  if (!path.some(item => item.id === value.step)) throw Error('Invalid step');
  return { question: value.question, explanation: value.explanation, steps: path, setup_id: value.setup_id, revision: Number(value.revision), step: value.step as SetupStep, draft, messages, choices, entity_id: value.entity_id as string | null,
    deferred: value.deferred, provider_ready: false, can_edit: value.can_edit };
}

export async function readOnboarding(fetcher: typeof fetch = fetch): Promise<SetupResult> {
  return request(undefined, fetcher);
}
export async function answerOnboarding(setupId: string, revision: number, answer: string, action: 'reply' | 'reset' | 'resume' = 'reply', fetcher: typeof fetch = fetch): Promise<SetupResult> {
  if (!uuid(setupId) || !Number.isSafeInteger(revision) || revision < 0 || answer.length > 128) return { state: 'invalid' };
  return request({ expected_revision: revision, answer, action }, fetcher, setupId);
}
async function request(body: object | undefined, fetcher: typeof fetch, setupId?: string): Promise<SetupResult> {
  try {
    const token = body ? await csrfToken(fetcher) : null;
    const response = await fetcher('/api/onboarding/', { ...options(), ...(body ? { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token!, 'X-Maipa-Setup': setupId! }, body: JSON.stringify(body) } : {}) });
    if (response.status === 409) return { state: 'conflict' };
    if (response.status === 401 || response.status === 403 || response.status === 404) return { state: 'denied' };
    if (response.status === 400) return { state: 'invalid' };
    if (response.status !== 200) return { state: 'unavailable' };
    return { state: 'ready', value: parse(await response.json()) };
  } catch { return { state: 'unavailable' }; }
}
