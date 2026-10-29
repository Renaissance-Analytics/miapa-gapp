import assert from 'node:assert/strict';
import test from 'node:test';
import { emptyChat, parseChatState } from '../.test-build/agent-chat.js';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { TurnFailureNotice } from '../.test-build/TurnFailureNotice.js';
import { failureCooldown, failureStatus } from '../.test-build/turn-failure.js';

const current = () => ({...emptyChat(), id:'conversation',phase:'unavailable',connection:{revision:1,account_id:'account',provider:'claude',label:'Claude',available:true}});
test('chat preserves only acknowledged account-bound failure enums and valid reset times', () => {
  const failure={kind:'rate_limit',resets_at_ms:1793313000000};
  assert.deepEqual(parseChatState({...current(),failure}).failure,failure);
  assert.equal(parseChatState({...current(),failure:null}).failure,null);
  assert.equal(parseChatState(emptyChat()).failure,undefined);
  for (const invalid of [{kind:'protocol'},{kind:'rate_limit',reason:'PRIVATE_REASON'},{kind:'rate_limit',resets_at_ms:true},{kind:'billing',resets_at_ms:1}])
    assert.throws(()=>parseChatState({...current(),failure:invalid}));
  assert.throws(()=>parseChatState({...emptyChat(),failure}));
});

test('public Fancy failure notice distinguishes known cooldown boundaries from unconfirmed causes',()=>{
  const failure={kind:'rate_limit',resets_at_ms:1793313000000};
  assert.equal(failureCooldown(failure,1793312999999),true);assert.equal(failureCooldown(failure,1793313000000),false);
  assert.equal(failureCooldown({kind:'rate_limit'},0),false);assert.equal(failureCooldown({kind:'other'},0),false);
  const html=renderToStaticMarkup(React.createElement(TurnFailureNotice,{failure}));
  assert.match(html,/data-react-fancy-callout/);assert.match(html,/2026-10-29T22:30:00.000Z/);assert.match(html,/No reply is retried automatically/);
  assert.match(failureStatus({kind:'other'}),/cause unconfirmed/);assert.match(failureStatus({kind:'unclassified'}),/cause unconfirmed/);
  assert.match(renderToStaticMarkup(React.createElement(TurnFailureNotice,{failure:{kind:'rate_limit'}})),/did not supply a reset time/);
});
