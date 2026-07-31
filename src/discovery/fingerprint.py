"""Identité stable d'une fiche produit découverte.

Une même fiche peut être rencontrée plusieurs fois, sous des URL
différentes : paramètres de tracking (`?utm_source=…`), variantes de
casse, barre finale, ancre. Sans normalisation, le même produit serait
« découvert » à chaque scan.

Le fingerprint retient l'identifiant le plus fort disponible :

    1. EAN          — identifiant mondial du produit, indépendant du site
    2. site + SKU   — référence interne, stable dans le temps
    3. site + URL canonique — repli universel

Le niveau retenu est conservé (`FingerprintInfo.basis`) : c'est une
information de diagnostic utile quand deux fiches se confondent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

#: Paramètres de requête à ignorer (tracking, affiliation, sessions).
_NOISE_PARAMS = re.compile(
    r"^(utm_|gclid|fbclid|msclkid|mc_|_ga|ref|source|cmpid|sessionid|sid|"
    r"affiliate|partner|campaign)",
    re.IGNORECASE,
)

_MULTI_SLASH = re.compile(r"/{2,}")


def canonical_url(url: str) -> str:
    """URL débarrassée du bruit : schéma et hôte en minuscules, ancre et
    paramètres de tracking retirés, barre finale supprimée."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = _MULTI_SLASH.sub("/", parts.path)
    if len(path) > 1:
        path = path.rstrip("/")

    kept = [
        pair for pair in parts.query.split("&")
        if pair and not _NOISE_PARAMS.match(pair.split("=", 1)[0])
    ]
    query = "&".join(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


def product_slug(url: str) -> str:
    """Dernier segment significatif de l'URL — sert de clé de regroupement."""
    path = urlsplit(canonical_url(url)).path
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return ""
    slug = segments[-1]
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug, flags=re.IGNORECASE)
    return slug.lower()


@dataclass(frozen=True)
class FingerprintInfo:
    value: str
    basis: str          # "ean" | "sku" | "url"
    canonical: str


def compute(
    site: str,
    url: str,
    sku: str | None = None,
    ean: str | None = None,
) -> FingerprintInfo:
    """Empreinte stable d'une fiche produit."""
    canonical = canonical_url(url)

    if ean and ean.strip():
        seed, basis = f"ean:{ean.strip()}", "ean"
    elif sku and sku.strip():
        seed, basis = f"{site.lower()}:sku:{sku.strip().lower()}", "sku"
    else:
        seed, basis = f"{site.lower()}:url:{canonical}", "url"

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return FingerprintInfo(value=digest, basis=basis, canonical=canonical)
