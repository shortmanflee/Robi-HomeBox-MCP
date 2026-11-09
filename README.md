# Robi HomeBox MCP

A [FastMCP](https://gofastmcp.com) server that lets MCP clients such as Claude Code and Claude Desktop create, read, update and delete records in a [HomeBox](https://github.com/sysadminsmedia/homebox) home-inventory instance. It runs in Docker on your LAN, and clients authenticate with a bearer token.

Tested against **sysadminsmedia/homebox v0.27.x**. In that release, items and locations are both stored as *entities*, and labels have been renamed *tags*.

## Tools

| Area | Tools |
|---|---|
| Items & locations | `search_entities`, `get_entity`, `get_entity_path`, `get_location_tree`, `create_item`, `create_location`, `update_entity`, `duplicate_entity`, `delete_entity` |
| Entity types | `list_entity_types`, `create_entity_type`, `update_entity_type`, `delete_entity_type` |
| Tags | `list_tags`, `get_tag`, `create_tag`, `update_tag`, `delete_tag` |
| Maintenance | `list_maintenance`, `create_maintenance`, `update_maintenance`, `delete_maintenance` |
| Attachments | `list_attachments`, `get_attachment`, `upload_attachment`, `upload_attachment_from_url`, `add_link_attachment`, `update_attachment`, `delete_attachment` |
| Reporting | `get_statistics`, `get_purchase_value_over_time`, `export_inventory_csv`, `get_server_status` |

The server also exposes two resources: `homebox://entity-types` and `homebox://tags`.

Things to know:
- **Updates only change what you send.** `update_*` tools read the current record, merge in the fields you passed, and write the whole record back. Fields you didn't mention are left as they are.
- **Moving an entity** is done with `update_entity(entity_id, parent_id=...)`.
- **Deletes are marked destructive** (`destructiveHint`), so clients can ask for confirmation first.
- **Attachments from a URL.** `upload_attachment_from_url` downloads a file and stores a copy in HomeBox. `add_link_attachment` saves only the link. Downloads are protected against server-side request forgery: only `https://` URLs are allowed, private, LAN, loopback and cloud-metadata addresses are refused, DNS is pinned, redirects (up to 3) are re-checked, and the size limit is enforced. Set `MCP_ALLOW_URL_FETCH=false` to remove the tool.
- **Read-only mode.** Setting `MCP_READ_ONLY=true` registers only the search, get, list and report tools.
- **Not exposed on purpose:** wipe-inventory and the other bulk "actions", user and group admin, notifiers, and label printing.

### Tool search (large tool lists)

With 32 tools, sending every schema up front costs context. There are two ways around that:

- **Client-side, recommended.** Claude Code and the Claude API's tool search load MCP tools on demand. They read only names and descriptions until a tool is needed. You don't need to configure anything on the server; the clear tool names and descriptions are what this search matches against.
- **Server-side, for clients without tool search.** Set `MCP_TOOL_SEARCH=bm25` (or `regex`). The client then sees only `search_entities`, `get_entity`, `search_tools` and `call_tool`, and finds everything else through `search_tools`.

  The trade-off: other tools run through the generic `call_tool` proxy. Per-tool annotations such as `destructiveHint` and per-tool client permissions (for example "always allow get_entity, ask before delete_entity") no longer apply to those calls. Pair this mode with `MCP_READ_ONLY=true`, or with a client that confirms every call.

## Quick start (Docker)

```bash
cp .env.example .env
# Edit .env: set HOMEBOX_URL, the service-account credentials, and a token:
openssl rand -hex 32   # paste into MCP_AUTH_TOKENS
docker compose up -d --build
curl http://localhost:8000/health    # {"status":"ok","homebox":"ok"}
```

Create a dedicated HomeBox user for the MCP server, or a HomeBox API key (`HOMEBOX_API_KEY`). Everything the MCP server does is recorded as that user.

If HomeBox runs in another Compose project on the same host, uncomment the `networks` block in `docker-compose.yml` and set `HOMEBOX_URL=http://homebox:7745`.

### Throwaway dev stack

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

This starts HomeBox at http://localhost:7745. Register the user named in `.env` there, then connect a client.

## Connecting clients

The MCP endpoint is `http://<server-lan-ip>:8000/mcp`. Every request must include the header `Authorization: Bearer <one of MCP_AUTH_TOKENS>`.

**Claude Code**
```bash
claude mcp add --transport http homebox http://192.168.1.20:8000/mcp \
  --header "Authorization: Bearer $HOMEBOX_MCP_TOKEN"
```

**Claude Desktop / other JSON-configured clients**
```json
{
  "mcpServers": {
    "homebox": {
      "type": "http",
      "url": "http://192.168.1.20:8000/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

**MCP Inspector:** run `npx @modelcontextprotocol/inspector`, choose "Streamable HTTP", and add the Authorization header.

**Local stdio** (no network, no token): set `MCP_TRANSPORT=stdio` and run `uv run homebox-mcp`.

## Security notes

- **Tokens.** Tokens must be at least 16 characters, and comparison is constant-time. You can list several comma-separated tokens, for example one per client or for rotation. `/health` is the only unauthenticated route, and it reveals nothing beyond up/down.
- **Use TLS.** Over plain HTTP, anyone on the network path can read the bearer token. Put the server behind a TLS reverse proxy, or keep it on a trusted VLAN. For example, with Caddy and its internal CA:
  ```caddyfile
  homebox-mcp.lan {
      tls internal
      reverse_proxy homebox-mcp:8000
  }
  ```
- **Host allowlist.** `MCP_ALLOWED_HOSTS` restricts which `Host` headers are accepted. This protects against DNS rebinding.
- **Least privilege.** Use a dedicated HomeBox account, and set `MCP_READ_ONLY=true` for clients that only need to look things up.
- **Container hardening.** The container runs as a non-root user with a read-only filesystem and no Linux capabilities.

## Configuration

See [.env.example](.env.example) for every variable. Each secret (`HOMEBOX_PASSWORD`, `HOMEBOX_API_KEY`, `MCP_AUTH_TOKENS`) can also be read from a file named in a `*_FILE` variable.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Layout: `src/homebox_mcp/`
- `client.py`: HomeBox HTTP client (login, refresh, 401 retry)
- `auth.py`: bearer-token verifier
- `server.py`: app factory, `/health` and resources
- `tools/`: one module per area
