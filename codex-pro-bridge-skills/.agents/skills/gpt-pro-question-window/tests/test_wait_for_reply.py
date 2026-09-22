"""Exercise DOM completion boundaries and local waiting without a paid Pro round."""
import json
from pathlib import Path
import subprocess
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/wait_for_reply.js"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const test = JSON.parse(fs.readFileSync(0, 'utf8'));
const start = 1800000000000;
let now = start, sleeps = 0;
class Clock extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
}
const user = id => ({getAttribute: key => key === 'data-message-author-role' ? 'user' : id});
const answer = id => ({
  getAttribute: key => key === 'data-message-author-role' ? 'assistant' : id,
  closest: () => ({querySelector: () => test.copy === false ? null : {disabled: false}}),
  querySelector: () => ({textContent: 'The exact answer. $a^2$'})
});
const location = {href: 'https://chatgpt.com/c/target'};
let owner = 'owner-a';
const scope = {
  Date: Clock, URL, location,
  sessionStorage: {getItem: () => owner},
  document: {
    querySelectorAll: () => (test.messages || ['old-user', 'old-answer', 'target', 'answer'])
      .map(id => id.includes('answer') ? answer(id) : user(id)),
    querySelector: () => test.generating ? {} : null
  },
  setTimeout: (fn, ms) => {
    now += ms; sleeps++;
    if (sleeps === test.complete_after) test.generating = false;
    if (sleeps === test.owner_change_after) owner = 'other-owner';
    if (sleeps === test.navigate_after) location.href = 'https://chatgpt.com/c/other';
    queueMicrotask(fn);
  },
};
vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), scope);
scope.waitForReply({attempt_id: 'attempt', remote_turn_id: 'target',
  conversation_url: location.href, tab_owner_token: owner,
  deadline: test.no_deadline ? null : new Date(start + (test.deadline_ms || 120000)).toISOString()
}).then(result => process.stdout.write(JSON.stringify({result, sleeps, elapsed: now-start})));
"""


class WaitForReplyTests(unittest.TestCase):
    def run_wait(self, **kwargs):
        proc = subprocess.run(['node', '-e', HARNESS, str(SOURCE)],
                              input=json.dumps(kwargs), text=True, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_returns_identity_without_answer_body_or_persistence_claim(self):
        out = self.run_wait()
        self.assertEqual(out['result']['status'], 'ready-for-capture')
        self.assertEqual(out['result']['assistant_turn_id'], 'answer')
        self.assertNotIn('The exact answer', json.dumps(out))
        self.assertEqual(out['sleeps'], 0)

    def test_generation_waits_locally_and_returns_only_when_ready(self):
        out = self.run_wait(generating=True, complete_after=7)
        self.assertEqual(out['result']['status'], 'ready-for-capture')
        self.assertEqual(out['elapsed'], 7000)

    def test_empty_batch_yields_pending_without_deadline_or_failure(self):
        out = self.run_wait(generating=True)
        self.assertEqual(out['result']['status'], 'pending')
        self.assertEqual(out['elapsed'], 55000)

    def test_missing_business_deadline_yields_pending_not_deadline(self):
        out = self.run_wait(generating=True, no_deadline=True, deadline_ms=1000)
        self.assertEqual(out['result']['status'], 'pending')
        self.assertEqual(out['elapsed'], 55000)

    def test_old_and_later_answers_cannot_complete_target(self):
        out = self.run_wait(messages=['old-user', 'old-answer', 'target', 'later-user', 'later-answer'],
                            deadline_ms=3000)
        self.assertEqual(out['result']['status'], 'deadline')

    def test_incomplete_copy_and_missing_target_stay_distinct(self):
        self.assertEqual(self.run_wait(copy=False, deadline_ms=2000)['result']['status'], 'deadline')
        self.assertEqual(self.run_wait(messages=['old-user', 'old-answer'])['result']['status'],
                         'target-not-visible')

    def test_identity_change_and_ambiguous_answers_require_inspection(self):
        for change in ['owner_change_after', 'navigate_after']:
            with self.subTest(change=change):
                self.assertEqual(self.run_wait(generating=True, **{change: 1})['result']['status'],
                                 'identity-changed')
        self.assertEqual(self.run_wait(messages=['target', 'answer-a', 'answer-b'])['result']['status'],
                         'ambiguous-answer')


if __name__ == '__main__':
    unittest.main()
