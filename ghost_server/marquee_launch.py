"""Launch the normal Marquee UI without passing an assistant key to a client."""
from urllib.parse import urlsplit
from ipaddress import ip_address
from . import store


def public_launch() -> dict:
    raw=str(store.get('marquee_app_url') or store.get('marquee_url') or '').strip()
    try:
        url=urlsplit(raw)
        valid=(url.scheme in ('http','https') and bool(url.hostname) and url.username is None
               and url.password is None and not url.query and not url.fragment
               and not any(ord(c)<32 for c in raw))
        url.port
        if url.hostname and url.hostname.lower()=='localhost':valid=False
        try:
            host=ip_address(url.hostname or '')
            if host.is_loopback or host.is_unspecified:valid=False
        except ValueError:
            pass
    except ValueError:
        valid=False
    return {'configured':bool(valid),'url':raw.rstrip('/')+'/' if valid else ''}
