# Algorithm Bundle Schema

A good upload bundle should be a zip package when code or implementation evidence is involved:

```text
README_FOR_GPT_PRO.md
source/
context/
```

`README_FOR_GPT_PRO.md` should contain:

1. Metadata: safe repository label, branch, commit, task mode, bridge thread id. Never include the absolute local repository path.
2. Codex session id and required Codex session notes path.
3. Repository context policy: `auto`, `explicit`, or `none`.
4. User goal and exact question for GPT Pro.
5. Review instructions and output format.
6. Evidence contract: Codex notes, compact bridge event context, files supplied, unresolved inputs, and policy exclusions.
7. Git status and diff stat.
8. Selected evidence file list, pointing to files under `source/`.
9. Context file list, pointing to files under `context/`.
10. Explicit requested output format.

`source/` should contain selected source/config/doc files as normal files, not pasted into one large markdown.

`context/` should contain Codex session notes, compact bridge event context, git status/diff stat, and optional extra notes.

A single markdown bundle is acceptable only for very small evidence or when upload is unavailable and the user approves the fallback.

The package is immutable: refuse an existing output path, verify zip integrity, and record its SHA-256 only when the package is actually sent in a GPT exchange.

Selection priority:

- User-specified files.
- Codex session notes.
- Bridge thread timeline.
- Current round summaries and decisions.
- Definitions named in the question.
- Definitely-local direct and transitive dependencies of admitted source files.
- Matching tests, direct callers, and boundary partners.
- Runtime or compiled counterparts when they are the executed form.
- Changed files related to the task.
- Docs explaining the method.
- Data construction / label construction.
- Training, reward/loss, sampling, config.
- Eval scripts, judge prompts, result summaries.
- Only then support files.

Red flags:

- No baseline/eval files included.
- Only implementation files, no problem statement.
- Huge unrelated files.
- Too many files from broad directories; prefer explicit focus paths.
- Web source files omitted for frontend reviews, especially `index.html`, `.css`, `.js`, or `.ts`.
- Static or binary assets that do not affect the algorithmic decision.
- Env files, secrets, cookies, keys, databases, raw private data, and large artifacts.
- GPT Pro is asked to judge without seeing metrics or configs.
- A requested include is missing, resolves outside the repository without explicit approval, or is silently omitted.
- A broad auto bundle reaches its file budget before closing local dependencies.
- A manifest lists many files but gives no selection reason or unresolved-dependency status.
- The manifest requests an algorithm-review schema for a non-algorithm mode.
