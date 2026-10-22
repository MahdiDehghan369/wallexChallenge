# Wallex MCP
- Endpoint: https://api.wallex.ir/mcp  (remote MCP over HTTP)
- Auth: same Wallex API key, read from env WALLEX_API_KEY (never hardcode)
- Note: only read-only MCP tools may be exposed to the LLM agents (see T-107).