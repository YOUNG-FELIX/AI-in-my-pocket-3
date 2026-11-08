# Security notes

This is a hobby project that drives a physical camera and a microphone from a
web page. Treat it as a **trusted-LAN tool**, not a public service.

## Threat model

| Who | What they could do without the fixes below |
|---|---|
| Anyone who can reach the HTTP port | Watch the camera, aim the gimbal, record audio/video |
| Anyone who can reach the HTTP port | Make the LLM speak and move the camera on their behalf |
| A crafted `path` argument | Write a JPEG/MP4 anywhere the process can write |
| A crafted `base` URL | Receive your LLM api key as a Bearer token |

## What is enforced now

1. **Authentication is on by default.** If `--key` is omitted the server
   generates a random one and prints it. `--no-auth` is required to run open.
2. **The API key is never sent to an unauthenticated client.** The page reads
   the key from its own URL (`/?key=...`) and keeps it in `sessionStorage`;
   `GET /` on its own carries no secret.
3. **`path` arguments cannot escape the output directory.** Absolute paths and
   `..` are rejected; the resolved path is re-checked against the output root.
   See `_safe_path()` in `p3server.py`.
4. **A custom LLM `base` URL is disabled by default**, because that endpoint
   receives your provider api key as a Bearer token. Enable it only with
   `--allow-custom-base`, and only `https://` is accepted.
5. **The SAPI voice name is validated against a strict character set** rather
   than quote-escaped, so nothing is ever interpolated into PowerShell.

## Residual risks -- read these

- **No TLS.** The key travels in a header or a query string over plain HTTP.
  On a shared network anyone sniffing the wire can read it. Prefer
  `X-Api-Key` (header) over `?key=` (query) so it is less likely to land in
  logs and browser history, and keep `--host 127.0.0.1` behind an SSH tunnel
  if the machine is on a network you do not control.
- **`?key=` in a URL is logged.** Browsers keep it in history and may send it
  as a `Referer`. The header form avoids that.
- **No rate limiting.** A local process can hammer `/api/call`. The gimbal has
  a 1.5 degree deadband so this is mostly a nuisance, but it is unbounded.
- **Prompt injection.** Text sent to `/api/agent/chat` goes to the LLM and the
  LLM chooses tools. Anything that can reach the port can drive the camera
  this way. That is by design, which is why the key matters.
- **The camera and microphone are physical.** If the laptop is compromised the
  tool is compromised. There is no sandbox around the device.

## What was checked and is fine

- No `eval`, `exec`, `pickle`, `os.system` or `shell=True` anywhere in the
  published code.
- `subprocess.run` is called with an argument list, never a shell string.
- Spoken text reaches PowerShell through a temporary file, not string
  interpolation, so user text cannot inject commands.
- No `Access-Control-Allow-Origin` header is emitted, so a random web page
  cannot drive the camera from the user's browser.
- No credentials, API keys, serial numbers, MAC addresses or user paths are in
  the repository. Device identifiers were redacted from
  [PROTOCOL.md](PROTOCOL.md).
