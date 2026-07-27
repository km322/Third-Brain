# third-brain-mcp

Connect your MCP clients (Claude Desktop, Claude Code, Cursor, or anything that
speaks the Model Context Protocol over stdio) to a
[Third Brain](https://third-brain.ai/docs) server - your company's
knowledge base with document-level permissions.

Zero dependencies. Node 18 or newer.

## Quick start

```bash
npx third-brain-mcp connect
```

You will be asked for your Third Brain server URL - the API origin (for the hosted
service, `https://api.third-brain.ai`; for a local stack, `http://localhost:8000`).
A one-time code then opens in your browser at the dashboard's `/activate` page
(`https://third-brain.ai/activate` on the hosted service). Approve the device there
and the CLI saves a scoped API key to `~/.third-brain/config.json` (file permissions 600).

Then wire it into your client:

```bash
npx third-brain-mcp install claude       # Claude Desktop
npx third-brain-mcp install claude-code  # Claude Code (prints the command to run)
npx third-brain-mcp install cursor       # Cursor
```

Restart the client and the `third-brain` tools (search, get document, list
collections, add and update knowledge) appear.

Both `connect` and `install` print a short heads-up: agents connected through
this server can write to your organization's shared knowledge base, and their
captures are reviewable in the dashboard under Documents -> Written by agents.

## Commands

| Command                                         | What it does                                                                                                       |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `connect [--url <server>] [--api-key <tb_...>]` | Sign in via the browser device flow (or paste an existing API key), verify it, and save it.                        |
| `install <claude\|claude-code\|cursor>`         | Register the bridge with a client. Existing config entries are preserved (read-modify-write with a `.bak` backup). |
| `serve`                                         | The stdio-to-HTTP MCP bridge that clients invoke. Also the default when run with no arguments.                     |
| `status`                                        | Show the configured server, its `/mcp` descriptor, and whether the saved key still passes `tools/list`.            |
| `--help`, `--version`                           | The usual.                                                                                                         |

## How auth works

1. `connect` calls `POST {server}/api/v1/device-auth` and shows you a short
   user code plus an approval URL (opened in your browser automatically when
   possible).
2. An organization admin opens that URL and approves the code in the Third Brain
   dashboard, where a scoped, revocable API key is minted for the device. (You
   cannot approve your own code unless you are an admin.)
3. The CLI polls `POST {server}/api/v1/device-auth/token` at the server-given
   interval until it is approved, denied, or expired, then verifies the key with
   an authenticated call and prints which organization it connected to before
   saving it.

The key is stored in `~/.third-brain/config.json` with `600` permissions. For
stateless environments (CI, containers), the `THIRD_BRAIN_URL` and
`THIRD_BRAIN_API_KEY` environment variables take precedence over the file.

## How the bridge works

MCP clients speak newline-delimited JSON-RPC 2.0 over stdio. `serve` forwards
each message verbatim to `POST {server}/mcp` with your API key and writes the
response back as a single stdout line. Notifications get no reply (the server
acknowledges them with HTTP 202), transport failures are mapped to JSON-RPC
error responses, and logs only ever go to stderr - stdout is reserved for the
protocol.

## License

Apache-2.0
