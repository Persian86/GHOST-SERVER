# Faster Home Assistant requests

Ghost Server now reuses a bounded pool of Home Assistant connections for home controls, camera discovery, snapshots and dashboard callers that use the home request helper. Requests still fetch fresh responses; this is connection reuse, not a state or response cache.

The pool retains TLS certificate verification, a ten-second request timeout and disabled environment proxies/redirects. Failed action requests are not automatically replayed. Changing the server address or access token replaces the pool; requests already in progress can finish before the retired pool closes. Client headers and cookies are not shared across credential changes.

Tests verify actual TCP connection reuse against a local HTTP server, fresh responses, authorization headers, single delivery of a failed action, address/token rotation and concurrent cleanup. This reduces repeated handshake overhead where the upstream server supports keep-alive; no physical tablet/NAS timing has been measured. It does not shorten Gemini setup, change wake-word behaviour or alter Wi-Fi performance.

Deploy Ghost Server; no APK update or settings change is needed.
