# MCP server setup

This project loads optional MCP servers from `mcp/mcp_config.json`.

## Genko remote MCP

A starter config has been added for a remote Genko MCP endpoint.

### Current config
- Server name: `genko`
- Transport wrapper: `npx mcp-remote`
- MCP URL: `https://genko-platform-production.up.railway.app/mcp`

### Important
Do **not** commit plaintext API keys into `mcp/mcp_config.json`.
Store secrets in your runtime environment instead.

### Required environment variables
Add these to `.env` (not `mcp_config.json`):

```env
GENKO_MCP_API_KEY=your_mcp_api_key
GENKO_MCP_AUTH_HEADER=Bearer your_mcp_api_key
GENKO_BUSINESS_ID=your_business_id
GENKO_CLIENT_PROFILE_ID=your_client_profile_id
UCP_PLATFORM_URL=https://genko-platform-production.up.railway.app
UCP_PLATFORM_API_KEY=your_sdk_api_key
```

`mcp_config.json` may reference `${GENKO_MCP_AUTH_HEADER}` in args; LimeBot expands
that from `.env` at runtime. **Do not** put empty secret keys in `mcp_config.json` —
they used to overwrite `.env` values before startup.

### Dependency
This config assumes `mcp-remote` is available through `npx`:

```bash
npm install
npx -y mcp-remote --help
```

If startup fails, install it explicitly:

```bash
npm install -D mcp-remote
```

Then restart LimeBot and check the MCP page/status endpoint.
