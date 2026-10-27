import type { PromptDraft } from '@particle-academy/react-fancy';

export const emptyDraft = (): PromptDraft => ({ v: 1, text: '', pills: [], attachments: [] });

/** Account-owned UI drafts and retry identities, never provider credentials. */
export class ChatDrafts {
  private entries = new Map<string, PromptDraft>();
  private requests = new Map<string, { text: string; id: string }>();
  read(account: string): PromptDraft { return structuredClone(this.entries.get(account) ?? emptyDraft()); }
  write(account: string, value: PromptDraft) { this.entries.set(account, structuredClone(value)); }
  request(account: string, text: string, makeId = () => crypto.randomUUID()): string {
    const old = this.requests.get(account);
    if (old?.text === text) return old.id;
    const id = makeId(); this.requests.set(account, { text, id }); return id;
  }
  acknowledge(account: string, id: string) {
    if (this.requests.get(account)?.id === id) this.requests.delete(account);
  }
}

export function sendProblem(text: string, attachments: readonly unknown[]): string | null {
  if (attachments.length) return 'Attachments cannot be sent yet. Remove them to send this message.';
  if (!text.trim()) return 'Write a message before sending.';
  if (text.length > 8000) return 'Keep the message within 8,000 characters. Your draft has been kept.';
  return null;
}
