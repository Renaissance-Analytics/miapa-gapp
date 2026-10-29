import { Callout } from '@particle-academy/react-fancy';
import { failureStatus, type TurnFailure } from './turn-failure.js';

export function TurnFailureNotice({ failure }: { failure: TurnFailure }) {
  const details={rate_limit:failure.resets_at_ms===undefined
    ? 'The provider did not supply a reset time. Try again later. Your conversation and new draft are kept.'
    : 'No new reply is sent before the reported reset. Your conversation and new draft are kept.',
    overloaded:'Try again later. Your conversation and new draft are kept.',auth:'Check the selected provider connection and sign-in status.',
    billing:'Check your selected provider account or subscription.',other:'The provider did not identify a specific cause. Your conversation and new draft are kept.',
    unclassified:'A specific cause was not confirmed. Your conversation and new draft are kept.'};
  return <Callout color="amber" role="status" className="turn-failure-notice" data-turn-failure={failure.kind}>
    <strong>{failureStatus(failure)}</strong><p>{details[failure.kind]}</p>
    {failure.resets_at_ms!==undefined&&<p>Reported reset: <time dateTime={new Date(failure.resets_at_ms).toISOString()}>
      {new Date(failure.resets_at_ms).toLocaleString()}</time>. No reply is retried automatically.</p>}
  </Callout>;
}
