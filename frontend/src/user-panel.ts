import { MicroMcpServer, errorResult } from '@particle-academy/agent-integrations/mcp';
import { registerScreensBridge } from '@particle-academy/agent-integrations/bridges/screens';
import type { ScreenSchema } from '@particle-academy/fancy-screens';

const refuse = () => { throw Error('Work view request refused.'); };
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null
  && !Array.isArray(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
const handle = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9-]{0,47}$/.test(value);
const keys = (value: Record<string, unknown>, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));

/** Validate before Fancy sees a schema: no executable props, data selectors or URLs. */
export function validateWorkSchema(input: unknown): ScreenSchema {
  const ids = new Set<string>();
  let count = 0, facts = 0;
  function node(value: unknown, depth: number): ScreenSchema {
    if (!record(value) || !keys(value, ['id', 'type', 'props', 'children']) || !handle(value.id)
      || ids.has(value.id) || depth > 4 || ++count > 16) return refuse();
    ids.add(value.id);
    if (value.type === 'Maipa.Stack') {
      if (value.props !== undefined || !Array.isArray(value.children) || value.children.length < 1 || value.children.length > 16) return refuse();
      return { id: value.id, type: value.type, children: value.children.map(child => node(child, depth + 1)) };
    }
    if (value.children !== undefined) return refuse();
    if (value.type === 'Maipa.ClientFacts' || value.type === 'Maipa.WorkspaceGuide') {
      if (value.props !== undefined || (value.type === 'Maipa.ClientFacts' && ++facts > 1)) return refuse();
      return { id: value.id, type: value.type };
    }
    if (value.type !== 'Maipa.Note' || !record(value.props) || !keys(value.props, ['text'])
      || typeof value.props.text !== 'string' || !value.props.text.trim() || value.props.text.length > 2000
      || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value.props.text)) return refuse();
    return { id: value.id, type: value.type, props: { text: value.props.text } };
  }
  return node(input, 0);
}

export interface WorkView { id: string; title: string; schema: ScreenSchema; builtin: boolean }
export interface UserPanelState { active: string; views: WorkView[] }
export const initialUserPanelState = (): UserPanelState => ({ active: 'client-facts', views: [
  { id: 'client-facts', title: 'Client facts', builtin: true, schema: { id: 'client-facts-root', type: 'Maipa.ClientFacts' } },
  { id: 'workspace-guide', title: 'Workspace guide', builtin: true, schema: { id: 'workspace-guide-root', type: 'Maipa.WorkspaceGuide' } },
] });

/** One mounted principal, one private in-process host. No network relay is opened. */
export class UserPanelModel {
  readonly server = new MicroMcpServer({ info: { name: 'maipa-work-views', version: '0.1.0' } });
  private value: UserPanelState = initialUserPanelState();
  private listener: ((state: UserPanelState) => void) | null = null;
  private disposed = false;
  private bridge: ReturnType<typeof registerScreensBridge>;
  get current(): UserPanelState { return structuredClone(this.value); }
  constructor(userId: string, private selectBuiltin?: (id: string) => boolean) {
    if (!/^[0-9]+$/.test(userId)) refuse();
    // The library's optional agentId argument must never select a different
    // host's global undo history. Bind every registered handler to this mount.
    const agentId = `maipa-user-${userId}-${crypto.randomUUID()}`;
    const host: Parameters<typeof registerScreensBridge>[0] = {
      getTool: name => this.server.getTool(name), listTools: () => this.server.listTools(),
      callTool: (name, args) => this.server.callTool(name, args),
      registerTool: (definition, handler) => this.server.registerTool(definition, async (args, context) => {
        const properties = definition.inputSchema.properties ?? {};
        if (this.disposed || !record(args) || Object.keys(args).some(key => !Object.hasOwn(properties, key))
          || (definition.inputSchema.required ?? []).some(key => !Object.hasOwn(args, key))
          || ('agentId' in args && args.agentId !== agentId)) return errorResult('Work view request refused.');
        return handler(args, context);
      }),
    };
    this.bridge = registerScreensBridge(host, { agent: { id: agentId, name: 'Maipa' }, adapter: {
      listScreens: () => { this.ensureActive(); return this.value.views.map(view => ({ id: view.id, title: view.title, active: view.id === this.value.active, kind: 'maipa-composition' })); },
      getActive: () => { this.ensureActive(); return this.value.active; },
      setActive: id => this.navigate(id),
      listKinds: () => [{ kind: 'maipa-composition', label: 'Scoped work view',
        description: 'Bounded schema using Maipa.Stack, Maipa.ClientFacts, Maipa.WorkspaceGuide and Maipa.Note. Stable unique node IDs required. Notes are unverified agent commentary; client facts always load through the signed-in server scope.' }],
      createScreen: spec => {
        this.ensureActive();
        if (!handle(spec.id) || ['users-access', 'client-setup'].includes(spec.id) || this.value.views.filter(view => !view.builtin).length >= 6 || this.value.views.some(view => view.id === spec.id)
          || spec.kind !== 'maipa-composition' || !record(spec.config) || !keys(spec.config, ['schema'])
          || (spec.title !== undefined && (typeof spec.title !== 'string' || !spec.title.trim() || spec.title.length > 80 || /[\u0000-\u001f]/.test(spec.title)))) return refuse();
        const schema = validateWorkSchema(spec.config.schema);
        this.value = { ...this.value, views: [...this.value.views, { id: spec.id, title: spec.title ?? 'Work view', schema, builtin: false }] };
        this.emit();
      },
      destroyScreen: id => {
        this.ensureActive();
        const view = this.value.views.find(item => item.id === id);
        if (!view || view.builtin) return refuse();
        this.value = { active: this.value.active === id ? 'client-facts' : this.value.active, views: this.value.views.filter(item => item.id !== id) };
        this.emit();
      },
      updateScreenContent: (id, partial) => {
        this.ensureActive();
        const view = this.value.views.find(item => item.id === id);
        if (!view || view.builtin || !record(partial) || !keys(partial, ['schema'])) return refuse();
        const schema = validateWorkSchema(partial.schema);
        this.value = { ...this.value, views: this.value.views.map(item => item.id === id ? { ...item, schema } : item) };
        this.emit();
      },
    } });
  }
  private ensureActive() { if (this.disposed) refuse(); }
  enableAdministration() {
    this.ensureActive();
    if (this.value.views.some(view => view.id === 'users-access')) return;
    this.value = { ...this.value, views: [...this.value.views, { id: 'users-access', title: 'Users and access',
      builtin: true, schema: { id: 'users-access-root', type: 'Maipa.AdminUsers' } }] };
    this.emit();
  }
  private emit() { this.listener?.(this.current); }
  enableClientAdministration() {
    this.ensureActive();
    if (this.value.views.some(view => view.id === 'client-setup')) return;
    this.value = { ...this.value, views: [...this.value.views, { id: 'client-setup', title: 'Client setup',
      builtin: true, schema: { id: 'client-setup-root', type: 'Maipa.ClientCatalog' } }] };
    this.emit();
  }
  subscribe(listener: (state: UserPanelState) => void) { this.ensureActive(); this.listener = listener; listener(this.current); return () => { this.listener = null; }; }
  navigate(id: string) {
    this.ensureActive();
    const view = this.value.views.find(item => item.id === id);
    if (!view || (view.builtin && !['users-access', 'client-setup'].includes(id) && this.selectBuiltin && !this.selectBuiltin(id))) return refuse();
    this.value = { ...this.value, active: id }; this.emit();
  }
  restoreBuiltin(id: string) {
    this.ensureActive();
    if (!this.value.views.some(view => view.id === id && view.builtin)) return refuse();
    this.value = { ...this.value, active: id }; this.emit();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.listener = null;
    this.bridge.dispose();
    for (const tool of this.server.listTools()) this.server.unregisterTool(tool.name);
  }
}
