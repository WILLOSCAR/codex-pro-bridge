async function waitForReply(config) {
  const batchEnd = Date.now() + 55000;
  const deadline = config.deadline ? Date.parse(config.deadline) : null;
  const result = (status, extra = {}) => ({
    status, attempt_id: config.attempt_id, remote_turn_id: config.remote_turn_id,
    ...extra
  });
  while (true) {
    if (location.href !== config.conversation_url ||
        sessionStorage.getItem('codex-pro-bridge.tab-owner.v1') !== config.tab_owner_token) {
      return result('identity-changed');
    }
    const messages = Array.from(document.querySelectorAll(
      '[data-message-author-role][data-message-id]'));
    const userIndex = messages.findIndex(node =>
      node.getAttribute('data-message-author-role') === 'user' &&
      node.getAttribute('data-message-id') === config.remote_turn_id);
    if (userIndex < 0) return result('target-not-visible');
    const answers = [];
    for (const node of messages.slice(userIndex + 1)) {
      const role = node.getAttribute('data-message-author-role');
      if (role === 'user') break;
      if (role === 'assistant') answers.push(node);
    }
    if (answers.length > 1) return result('ambiguous-answer');
    if (answers.length === 1) {
      const answer = answers[0];
      const turn = answer.closest('[data-testid^="conversation-turn-"]');
      const copy = turn?.querySelector(
        'button[data-testid="copy-turn-action-button"],button[aria-label="Copy response"],button[aria-label="复制回复"]');
      const body = answer.querySelector('.markdown');
      const generating = document.querySelector(
        '[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="停止回答"]');
      if (copy && !copy.disabled && body?.textContent.trim() && !generating) {
        return result('ready-for-capture', {
          assistant_turn_id: answer.getAttribute('data-message-id'),
          observed_at: new Date().toISOString()
        });
      }
    }
    if (deadline !== null && Date.now() >= deadline) return result('deadline');
    if (Date.now() >= batchEnd) return result('pending');
    const remaining = deadline === null
      ? batchEnd - Date.now()
      : Math.min(deadline - Date.now(), batchEnd - Date.now());
    await new Promise(resolve => setTimeout(resolve,
      Math.min(1000, remaining)));
  }
}
