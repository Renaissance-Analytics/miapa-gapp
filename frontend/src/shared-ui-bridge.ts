import { MicroMcpServer, errorResult } from '@particle-academy/agent-integrations/mcp';
import { dismissComposition, type Composition } from './agent-chat.js';

/** Human+ adapter over the SAME authorized composition used by the provider agent. */
export class SharedUiBridge {
  readonly server = new MicroMcpServer({ info: { name: 'maipa-shared-ui', version: '0.1.0' } });
  private closed = false;
  constructor(userId: string, current: () => Composition | null,
    commit: typeof dismissComposition = dismissComposition) {
    if (!/^[0-9]+$/.test(userId)) throw Error('UI unavailable');
    this.server.registerTool({ name: 'maipa_ui_dismiss', description: 'Close the current shared composition at its exact revision.',
      inputSchema: { type: 'object', properties: { id: { type: 'string' }, revision: { type: 'integer', minimum: 0 } },
        required: ['id', 'revision'], additionalProperties: false } }, async args => {
      const ui = current();
      if (this.closed || !ui || !ui.composition || !args || typeof args !== 'object'
        || Object.keys(args).sort().join(',') !== 'id,revision' || args.id !== ui.id || args.revision !== ui.revision)
        return errorResult('The shared panel changed. Reload its current state.');
      try {
        await commit(userId, structuredClone(ui));
        if (this.closed) return errorResult('UI unavailable');
        return { content: [{ type: 'text' as const, text: 'Contextual panel closed.' }] };
      } catch { return errorResult('The shared panel could not be closed. Reload its current state.'); }
    });
  }
  async dismiss(ui: Composition) {
    const result = await this.server.callTool('maipa_ui_dismiss', { id: ui.id, revision: ui.revision });
    if (result.isError) throw Error('The shared panel changed. Reconnect before closing it.');
  }
  dispose() {
    this.closed = true;
    for (const tool of this.server.listTools()) this.server.unregisterTool(tool.name);
  }
}
