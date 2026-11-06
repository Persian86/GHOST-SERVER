"""HTTPS for Ghost Server — encrypted traffic between your devices and the NAS on your Wi-Fi.

The server makes its own certificate the first time it starts (data/tls/ghost.crt + ghost.key,
valid for 10 years, for the NAS's addresses). HTTPS runs on port 8781 next to plain HTTP on 8780.

There's no public certificate authority for a box on your LAN, so the apps use "pinning":
  1. A device asks GET /api/v1/tls (plain HTTP, public) for the certificate.
  2. From then on it only talks to https://…:8781 and only accepts THAT certificate — a copy of
     traffic on the Wi-Fi is unreadable, and nobody can pretend to be the server later.
  3. The admin page shows the certificate's fingerprint; the apps show the first characters in
     Settings so you can compare them once.

"HTTPS only" (admin page → Security) then stops the plain-HTTP API: only /api/v1/health,
/api/v1/tls and the certificate download stay on 8780, and the admin page redirects to HTTPS.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import socket
from pathlib import Path

from . import config

PORT = int(os.environ.get("GHOST_TLS_PORT", str(config.PORT + 1)))


def folder() -> Path:
    return config.DATA_DIR / "tls"


def cert_path() -> Path:
    return folder() / "ghost.crt"


def key_path() -> Path:
    return folder() / "ghost.key"


def local_ips() -> list[str]:
    """The NAS's addresses (Docker runs with network_mode: host, so these are the real ones)."""
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))                  # no traffic is sent; picks the LAN address
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    extra = os.environ.get("GHOST_TLS_HOSTS", "")      # e.g. "192.168.80.244, ghost.local"
    ips.update(x.strip() for x in extra.split(",") if x.strip())
    return sorted(ips)


def ensure() -> bool:
    """Make the certificate if there isn't one. False if the cryptography package is missing."""
    if cert_path().exists() and key_path().exists():
        return True
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:
        return False
    folder().mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ghost Server"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ghost (home)")])
    alt: list = [x509.DNSName("localhost"), x509.DNSName("ghost.local")]
    host = socket.gethostname()
    if host:
        alt.append(x509.DNSName(host))
    for h in local_ips():
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            alt.append(x509.DNSName(h))
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path().write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    os.chmod(key_path(), 0o600)
    cert_path().write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return True


def pem() -> str:
    return cert_path().read_text() if cert_path().exists() else ""


def fingerprint(spaced: bool = False) -> str:
    """SHA-256 of the certificate (DER) — what the apps pin."""
    p = pem()
    if not p:
        return ""
    import base64
    body = "".join(line for line in p.splitlines() if line and not line.startswith("-----"))
    h = hashlib.sha256(base64.b64decode(body)).hexdigest().upper()
    return " ".join(h[i:i + 4] for i in range(0, len(h), 4)) if spaced else h


def info() -> dict:
    return {"enabled": bool(pem()), "port": PORT, "sha256": fingerprint(), "pem": pem()}
