# MCP safety annotations and server discovery

## Tools vs resources

| Kind | Mutates | Scope | HITL |
|------|---------|-------|------|
| Tool (action) | maybe | `payments:execute` if destructive | every destructive call; enhanced review above threshold |
| Resource (read) | never | `payments:status` / `compliance:read` | never |

Annotations emitted on every tool (both AgentBridge aliases and MCP hints):

```json
{
  "readOnly": false,
  "destructive": true,
  "idempotent": false,
  "readOnlyHint": false,
  "destructiveHint": true,
  "idempotentHint": false
}
```

## Server card

`GET /.well-known/mcp.json` is a static, indexable capability document. Crawlers and orchestrators can classify high-risk financial actions **without** opening a session. It includes each tool's complete input schema, and contract tests prevent discovery metadata from drifting from runtime definitions.

## Least-privilege scopes

| Scope | Permits |
|-------|---------|
| `payments:quote` | `quote_payment` |
| `payments:status` | `check_transaction`, `mpesa_stk_query`, `paystack_verify`, wallet resource |
| `payments:execute` | `process_payment`, STK push, B2C, Paystack initialize, NGN transfer, `execute_payment` |
| `compliance:read` | OSCAL assessment results / POA&M |
| `admin:budget` | raise ceilings (never implied by quote/status) |

A read token **cannot** execute a transfer even if the model asks.

## OAuth 2.1 + PKCE

1. Client generates S256 `code_verifier` / `code_challenge`
2. Authorize with registered `redirect_uri` (loopback HTTP or HTTPS only)
3. Code is single-use; exchange verifies PKCE and exact URI
4. Access token is bound to the granted scope set
