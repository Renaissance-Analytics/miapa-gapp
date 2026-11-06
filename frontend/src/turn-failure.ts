export type TurnFailure = { kind: 'rate_limit' | 'overloaded' | 'auth' | 'billing' | 'other' | 'unclassified'; resets_at_ms?: number };
export const COOLDOWN_MESSAGE = 'The provider usage limit is still active. Your draft has been kept.';
export function parseTurnFailure(value: unknown): TurnFailure | null {
  if (value === null) return null;
  if (typeof value !== 'object' || !value || Array.isArray(value)) throw Error('Conversation unavailable');
  const v=value as Record<string,unknown>;
  if (typeof v.kind !== 'string' || !['rate_limit','overloaded','auth','billing','other','unclassified'].includes(v.kind)
    || Object.keys(v).some(key=>!['kind','resets_at_ms'].includes(key))
    || ('resets_at_ms' in v && (v.kind !== 'rate_limit' || !Number.isSafeInteger(v.resets_at_ms)
      || Number(v.resets_at_ms)<=0 || Number(v.resets_at_ms)>253_402_300_799_999))) throw Error('Conversation unavailable');
  return {kind:v.kind as TurnFailure['kind'],...('resets_at_ms' in v?{resets_at_ms:Number(v.resets_at_ms)}:{})};
}
export const failureCooldown=(failure:TurnFailure|null|undefined,now:number):boolean=>
  failure?.kind==='rate_limit' && (failure.resets_at_ms??0)>now;
export function failureStatus(failure:TurnFailure):string {
  return ({rate_limit:'Provider usage limit reached',overloaded:'Provider is busy',auth:'Provider sign-in needs attention',
    billing:'Provider account needs attention',other:'Last reply failed; cause unconfirmed',unclassified:'Last reply failed; cause unconfirmed'})[failure.kind];
}
