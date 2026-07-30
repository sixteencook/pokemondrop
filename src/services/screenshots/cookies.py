"""Fermeture des bandeaux de consentement avant capture.

Trois passes, de la plus fiable à la plus générique :
  1. sélecteurs des plateformes connues (OneTrust, Didomi, Cookiebot, Axeptio…) ;
  2. sélecteurs fournis par le plugin du site (BaseMonitor.cookie_selectors) ;
  3. boutons dont le texte contient « Tout accepter », « Accepter », « Accept all »…

RÈGLE ABSOLUE : un échec n'interrompt jamais la capture. Mieux vaut une
capture avec bandeau qu'aucune capture.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.utils.logger import get_logger

log = get_logger("screenshots.cookies")

#: Sélecteurs des principales plateformes de consentement.
PLATFORM_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",                    # OneTrust
    ".onetrust-close-btn-handler",
    "#didomi-notice-agree-button",                     # Didomi
    "button#didomi-notice-agree-button",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",   # Cookiebot
    "#CybotCookiebotDialogBodyButtonAccept",
    "#axeptio_btn_acceptAll",                          # Axeptio
    "button[mode='primary'][class*='sp_choice']",      # Sourcepoint
    "#tarteaucitronPersonalize2",                      # Tarteaucitron
    "button[aria-label*='Accepter']",
    "button[data-testid='uc-accept-all-button']",      # Usercentrics
)

#: Textes de boutons acceptés en dernier recours (comparaison insensible à la casse).
TEXT_HINTS: tuple[str, ...] = (
    "tout accepter",
    "j'accepte",
    "jaccepte",
    "accepter et fermer",
    "accepter",
    "accept all",
    "allow all",
    "i accept",
    "accept",
    "ok pour moi",
)

#: Masquage CSS des conteneurs qui résistent (dernier filet de sécurité).
OVERLAY_HIDE_CSS = """
#onetrust-consent-sdk, .onetrust-pc-dark-filter, #didomi-host,
#CybotCookiebotDialog, #CybotCookiebotDialogBodyUnderlay, #axeptio_overlay,
#tarteaucitronRoot, [id*='sp_message_container'], [class*='cookie-banner'],
[class*='cookie-consent'], [id*='usercentrics-root'] {
  display: none !important;
  visibility: hidden !important;
}
"""


async def dismiss_cookie_banners(
    page: Any, extra_selectors: Sequence[str] = (), timeout_ms: int = 1200
) -> str | None:
    """Tente de fermer le bandeau. Retourne le sélecteur qui a fonctionné.

    Ne lève jamais : toute exception est avalée et loggée en debug.
    """
    for selector in (*PLATFORM_SELECTORS, *extra_selectors):
        if await _try_click(page, selector, timeout_ms):
            log.check("Bandeau cookies fermé via « %s »", selector)
            return selector

    matched = await _try_click_by_text(page, timeout_ms)
    if matched:
        log.check("Bandeau cookies fermé via le texte « %s »", matched)
        return matched

    # Rien n'a fonctionné : on masque les conteneurs connus en CSS.
    try:
        await page.add_style_tag(content=OVERLAY_HIDE_CSS)
    except Exception:  # noqa: BLE001 — purement cosmétique
        pass
    return None


async def _try_click(page: Any, selector: str, timeout_ms: int) -> bool:
    try:
        locator = page.locator(selector).first
        await locator.click(timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001 — sélecteur absent / non cliquable
        return False


async def _try_click_by_text(page: Any, timeout_ms: int) -> str | None:
    """Cherche un bouton dont le texte correspond à un consentement."""
    try:
        buttons = page.locator(
            "button:visible, a[role='button']:visible, input[type='button']:visible"
        )
        count = min(await buttons.count(), 40)  # borne : pages très riches
    except Exception:  # noqa: BLE001
        return None

    for index in range(count):
        try:
            button = buttons.nth(index)
            label = ((await button.inner_text()) or "").strip().lower()
            if not label or len(label) > 40:
                continue
            if any(hint in label for hint in TEXT_HINTS):
                await button.click(timeout=timeout_ms)
                return label
        except Exception:  # noqa: BLE001 — bouton disparu entre-temps
            continue
    return None
