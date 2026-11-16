"""Provider endpoint identities used by settings and outbound requests."""

from urllib.parse import urlsplit

import httpx

LOCAL_PROVIDERS = {"ollama", "lm_studio", "openai_compatible"}


def endpoint_identity(base_url: str) -> str:
    if not base_url or any(char.isspace() or ord(char) < 32 or char in "\\?#" for char in base_url):
        raise ValueError("invalid_provider_endpoint")
    try:
        parsed = urlsplit(base_url)
        url = httpx.URL(base_url)
        if (url.scheme not in {"http", "https"} or not url.host
                or "@" in parsed.netloc or parsed.port == 0):
            raise ValueError("invalid_provider_endpoint")
        host = url.raw_host.decode("ascii").lower()
        authority = f"[{host}]" if ":" in host else host
        port = url.port or (443 if url.scheme == "https" else 80)
        path = url.raw_path.decode("ascii").rstrip("/")
        return f"{url.scheme}://{authority}:{port}{path}"
    except (ValueError, httpx.InvalidURL) as exc:
        raise ValueError("invalid_provider_endpoint") from exc


def provider_endpoint(provider: str, base_url: str) -> str:
    identity = endpoint_identity(base_url)
    parsed = urlsplit(identity)
    if provider in LOCAL_PROVIDERS:
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Local provider must use a loopback endpoint")
    elif parsed.scheme != "https":
        raise ValueError("Cloud endpoint must use HTTPS")
    return identity
