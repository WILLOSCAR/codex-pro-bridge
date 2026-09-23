# Browser host topologies

Codex Pro Bridge supports two Windows browser-host layouts without embedding a
drive letter, user name, or machine path in the repository.

## Windows-native

Codex, Python, Chrome DevTools MCP, and Chrome all run on Windows. Run Bridge
helpers from the Codex workspace root. Without a config file, staging defaults
to `.codex/codex-pro-bridge/browser-staging` below that workspace so DevTools MCP
can read the upload through its workspace-root boundary.

Install the package with `install.ps1`, run Bridge scripts with `py -3` (or an
explicit Python 3.10+ executable), and start `chrome-devtools-mcp` with the
Windows Node.js runtime. No WSL process or path conversion is required.

To choose a different root, copy `config/browser-host.windows.json.example` to
any private location and set the following values. The chosen directory must
also be configured as an allowed MCP workspace root:

```text
CODEX_PRO_BRIDGE_HOST_CONFIG=<absolute path to the copied JSON file>
CODEX_BRIDGE_STAGING_ROOT=<absolute Windows staging directory>
CODEX_BRIDGE_STAGING_LOCK=<absolute Windows lock-file path>
```

The two root fields intentionally refer to the same environment variable: the
execution host and browser host share one Windows path namespace.

## WSL execution with Windows MCP and Chrome

Codex and Python run in WSL while Chrome DevTools MCP and Chrome run on Windows.
Copy `config/browser-host.wsl-windows.json.example` to a private location and
set:

```text
CODEX_PRO_BRIDGE_HOST_CONFIG=<absolute WSL path to the copied JSON file>
CODEX_BRIDGE_STAGING_EXECUTION_ROOT=<absolute WSL path to a Windows-backed directory>
CODEX_BRIDGE_STAGING_BROWSER_ROOT=<the same directory as an absolute Windows path>
CODEX_BRIDGE_STAGING_LOCK=<absolute WSL lock-file path>
```

The Windows representation must be inside a workspace root exposed to
`chrome-devtools-mcp`. Digest verification proves file identity; it does not
override the MCP filesystem allowlist.

The Bridge copies only the approved bundle into the configured root, verifies
its SHA-256, returns both path representations, and deletes only that exact file
after checking its digest. It never scans or recursively clears the staging
directory.

## MCP examples

On Windows-native Codex, run `chrome-devtools-mcp` through the Windows Node.js
installation. With WSL execution, launch the MCP process through Windows
interop so it shares the same host and path namespace as Chrome. Keep concrete
commands and environment values in the user's Codex configuration; do not add
them to this repository.

In both layouts, enable Chrome remote debugging explicitly and keep the
debugging endpoint local to the browser host.
