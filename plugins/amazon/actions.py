"""Résolution de l'action d'achat principale d'une fiche Amazon.

PRINCIPE
--------
Une fiche produit porte des dizaines de contrôles. Un seul décrit ce que
l'acheteur peut faire **du produit** : mettre au panier, acheter,
précommander, demander une invitation — ou constater qu'il ne peut rien
faire. Tout le reste est du bruit, et c'est ce bruit qui produisait les
fausses alertes : une liste d'envies qui change d'identifiant, un bandeau
Prime, un encart de financement, une mention de livraison.

Ce module ne rend donc **qu'une seule action**, et dit toujours d'où elle
vient. Il énumère aussi ce qu'il a écarté, avec le motif : c'est la
différence entre « le statut a changé » et « je sais pourquoi le statut a
changé ».

ORDRE DE RÉSOLUTION, du plus fiable au moins fiable :

  1. **Mention bloquante du bloc de disponibilité** — « Temporairement en
     rupture », « Disponible sur invitation »… Elle prime sur tout bouton,
     car Amazon laisse fréquemment le bouton d'achat en place sur une
     fiche en rupture : le croire produirait un faux retour en stock.
  2. **Contrôle d'achat identifié** — un élément de `PURCHASE_CONTROLS`,
     dont le libellé réel précise l'action.
  3. **Contrôle non exclu dont le libellé nomme l'action** — filet de
     sécurité : Amazon renomme ses identifiants régulièrement.
  4. **Mention de disponibilité positive** — « En stock », et
     « bientôt disponible », qui n'a de sens qu'en l'absence de bouton.
  5. **Texte du périmètre** — dernier recours, marqueurs forts seuls.
  6. **Buy box sans libellé lisible** — un bouton d'achat existe mais son
     libellé est illisible : on conclut à la mise au panier.

Un contrôle **exclu** ne peut jamais décider, quel que soit son libellé et
quelle que soit l'étape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.models import PurchaseAction
from src.monitors.generic import normalise

from . import keywords

#: Contrôles d'achat reconnus, du plus spécifique au plus général.
#: L'action associée n'est qu'un défaut : le libellé réel du contrôle
#: prime toujours (un `#add-to-cart-button` peut dire « Précommander »).
PURCHASE_CONTROLS: tuple[tuple[str, PurchaseAction], ...] = (
    ("#hdp-invite-button", PurchaseAction.REQUEST_INVITE),
    ("#hdp-invite-button-announce", PurchaseAction.REQUEST_INVITE),
    ("[id^='hdp-invite']", PurchaseAction.REQUEST_INVITE),
    ("[id*='invite-button']", PurchaseAction.REQUEST_INVITE),
    ("[id*='invitationRequest']", PurchaseAction.REQUEST_INVITE),
    ("[id*='invite']", PurchaseAction.REQUEST_INVITE),
    ("#add-to-cart-button", PurchaseAction.ADD_TO_CART),
    ("input[name='submit.add-to-cart']", PurchaseAction.ADD_TO_CART),
    ("#addToCart input[type='submit']", PurchaseAction.ADD_TO_CART),
    ("#buy-now-button", PurchaseAction.BUY_NOW),
    ("input[name='submit.buy-now']", PurchaseAction.BUY_NOW),
    ("#one-click-button", PurchaseAction.BUY_NOW),
    ("#outOfStockBuyBox", PurchaseAction.CURRENTLY_UNAVAILABLE),
    ("#outOfStock", PurchaseAction.CURRENTLY_UNAVAILABLE),
)

#: Fragments d'identifiant, de nom ou de classe qui excluent formellement
#: un contrôle de la décision, avec leur motif.
#:
#: Amazon nomme ses blocs de façon très régulière : reconnaître un
#: fragment est à la fois plus rapide et plus robuste qu'une liste de
#: sélecteurs CSS, qui casse au moindre changement de structure.
EXCLUDED_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("wishlist", "liste d'envies"),
    ("wish-list", "liste d'envies"),
    ("add-to-registry", "liste d'envies"),
    ("registry", "liste d'envies"),
    ("glow", "adresse de livraison"),
    ("contextualingress", "adresse de livraison"),
    ("delivery-block", "adresse de livraison"),
    ("nav-global-location", "adresse de livraison"),
    ("prime", "Prime"),
    ("installment", "financement"),
    ("financ", "financement"),
    ("warranty", "garantie / assurance"),
    ("protection-plan", "garantie / assurance"),
    ("mbb-", "garantie / assurance"),
    ("share", "partage"),
    ("comparison", "comparaison"),
    ("askatf", "questions/réponses"),
    ("customer-reviews", "avis clients"),
    ("see-all-buying-choices", "renvoi vers les autres vendeurs"),
    ("sp-sponsored", "produit sponsorisé"),
    ("sponsored", "produit sponsorisé"),
)

#: Nombre d'ancêtres remontés pour juger si un contrôle appartient à un
#: bloc exclu. Au-delà, on atteindrait `<body>` et ses classes globales.
_ANCESTOR_DEPTH = 6

#: Libellé → action, dans l'ordre de priorité. C'est cette table qui
#: tranche entre « Ajouter au panier » et « Précommander » sur un même
#: bouton.
LABEL_ACTIONS: tuple[tuple[tuple[str, ...], PurchaseAction], ...] = (
    (keywords.INVITATION, PurchaseAction.REQUEST_INVITE),
    (keywords.PREORDER, PurchaseAction.PREORDER),
    (keywords.NOTIFY_ME, PurchaseAction.NOTIFY_ME),
    (keywords.ADD_TO_CART, PurchaseAction.ADD_TO_CART),
    (keywords.BUY_NOW, PurchaseAction.BUY_NOW),
)

#: Mentions du bloc de disponibilité qui PRIMENT sur les boutons.
#:
#: Amazon laisse fréquemment le bouton « Ajouter au panier » en place sur
#: une fiche en rupture. Croire le bouton plutôt que la mention produirait
#: une fausse alerte « retour en stock » — le pire faux positif possible.
BLOCKING_STATE_ACTIONS: tuple[tuple[tuple[str, ...], PurchaseAction], ...] = (
    (keywords.INVITATION, PurchaseAction.REQUEST_INVITE),
    (keywords.PREORDER, PurchaseAction.PREORDER),
    (keywords.NOTIFY_ME, PurchaseAction.NOTIFY_ME),
    (("temporairement en rupture de stock", "temporairement indisponible",
      "temporarily out of stock"), PurchaseAction.TEMPORARILY_UNAVAILABLE),
    (keywords.OUT_OF_STOCK, PurchaseAction.CURRENTLY_UNAVAILABLE),
    (keywords.UNAVAILABLE, PurchaseAction.DISCONTINUED),
)

#: Mentions consultées seulement si aucun contrôle d'achat n'a tranché.
#:
#: `COMING_SOON` est ici — et non dans la table bloquante — délibérément :
#: « bientôt disponible » décrit une attente, mais un bouton
#: « Précommander » actif décrit une action possible **maintenant**. Un
#: contrôle d'achat vivant l'emporte donc toujours sur une annonce de
#: sortie, sans quoi le moteur raterait l'ouverture d'une précommande.
POSITIVE_STATE_ACTIONS: tuple[tuple[tuple[str, ...], PurchaseAction], ...] = (
    (keywords.ADD_TO_CART, PurchaseAction.ADD_TO_CART),
    (keywords.BUY_NOW, PurchaseAction.BUY_NOW),
    (keywords.IN_STOCK, PurchaseAction.ADD_TO_CART),
    (keywords.COMING_SOON, PurchaseAction.COMING_SOON),
)

#: Marqueurs assez forts pour trancher depuis le simple texte du
#: périmètre. Volontairement restreint : c'est le dernier recours, et le
#: texte d'un bloc entier est la source la moins fiable.
SCOPE_ACTIONS: tuple[tuple[tuple[str, ...], PurchaseAction], ...] = (
    (keywords.INVITATION, PurchaseAction.REQUEST_INVITE),
    (keywords.PREORDER, PurchaseAction.PREORDER),
    (keywords.OUT_OF_STOCK, PurchaseAction.CURRENTLY_UNAVAILABLE),
    (keywords.COMING_SOON, PurchaseAction.COMING_SOON),
    (keywords.UNAVAILABLE, PurchaseAction.CURRENTLY_UNAVAILABLE),
)


@dataclass(frozen=True)
class IgnoredControl:
    """Un contrôle écarté de la décision, et pourquoi."""

    label: str
    selector: str
    reason: str


@dataclass
class ActionResolution:
    """L'action principale retenue, sa provenance et ce qui a été écarté."""

    action: PurchaseAction = PurchaseAction.NONE
    selector: str = ""
    label: str = ""
    origin: str = ""
    ignored: tuple[IgnoredControl, ...] = ()
    #: Nombre total de contrôles examinés dans le périmètre.
    examined: int = 0

    @property
    def resolved(self) -> bool:
        return self.action is not PurchaseAction.NONE

    @property
    def ignored_reasons(self) -> tuple[str, ...]:
        """Motifs d'exclusion rencontrés, dédoublonnés et ordonnés."""
        return tuple(dict.fromkeys(control.reason for control in self.ignored))

    def describe(self) -> str:
        if not self.resolved:
            return "aucune action d'achat identifiée"
        return f"{self.label or self.action.value} ({self.selector} · {self.origin})"

    def describe_ignored(self) -> str:
        if not self.ignored:
            return "aucun"
        return f"{len(self.ignored)} — {', '.join(self.ignored_reasons)}"


def resolve(
    scope,
    availability_texts: tuple[tuple[str, str], ...],
    scope_selector: str,
    scope_text: str,
    has_buy_box: bool = False,
    buy_selector: str = "",
) -> ActionResolution:
    """Détermine l'action d'achat principale du périmètre fourni.

    `availability_texts` est une suite de couples (sélecteur, texte) : la
    provenance exacte de chaque mention est conservée pour pouvoir dire
    quel sélecteur a tranché.
    """
    ignored, examined = _survey_controls(scope)

    resolution = (
        # 1. Une mention bloquante prime sur tout bouton resté en place.
        _from_texts("bloc disponibilité", availability_texts,
                    BLOCKING_STATE_ACTIONS)
        # 2. Le contrôle d'achat lui-même, par identifiant connu.
        or _from_purchase_control(scope)
        # 3. Filet de sécurité : un contrôle non exclu dont le LIBELLÉ dit
        #    l'action. Amazon renomme ses identifiants régulièrement ; s'en
        #    remettre à eux seuls ferait manquer un bouton d'invitation
        #    parfaitement visible.
        or _from_labelled_control(scope)
        # 4. Une mention de disponibilité positive.
        or _from_texts("bloc disponibilité", availability_texts,
                       POSITIVE_STATE_ACTIONS)
        # 4. Dernier recours : le texte du périmètre, marqueurs forts seuls.
        or _from_texts("texte du périmètre", ((scope_selector, scope_text),),
                       SCOPE_ACTIONS)
        # 5. Un bouton d'achat existe mais rien n'est lisible : Amazon
        #    change régulièrement ses libellés, la présence du contrôle
        #    reste un signal.
        or _from_buy_box(has_buy_box, buy_selector)
    )
    if resolution is None:
        return ActionResolution(ignored=ignored, examined=examined)

    return ActionResolution(
        action=resolution.action,
        selector=resolution.selector,
        label=resolution.label,
        origin=resolution.origin,
        ignored=ignored,
        examined=examined,
    )


def _from_buy_box(has_buy_box: bool, buy_selector: str) -> Optional[ActionResolution]:
    if not has_buy_box:
        return None
    return ActionResolution(
        action=PurchaseAction.ADD_TO_CART,
        selector=buy_selector or "bouton d'achat",
        label="(bouton d'achat sans libellé lisible)",
        origin="buy box",
    )


def _from_purchase_control(scope) -> Optional[ActionResolution]:
    """Premier contrôle d'achat de la liste blanche présent dans le périmètre."""
    for selector, default_action in PURCHASE_CONTROLS:
        tag = _select_one(scope, selector)
        if tag is None:
            continue
        if _exclusion_reason(tag) is not None:
            continue

        label = _control_label(tag, scope)
        action = _action_for_label(label) or default_action
        return ActionResolution(
            action=action,
            # Un sélecteur à joker (`[id*='invite']`) ne désigne rien de
            # précis : on lui préfère l'élément réellement trouvé.
            selector=_describe(tag) if "*=" in selector or "^=" in selector
            else selector,
            label=label or "(libellé illisible)",
            origin="contrôle d'achat",
        )
    return None


def _from_labelled_control(scope) -> Optional[ActionResolution]:
    """Contrôle non exclu dont le libellé nomme lui-même l'action.

    La discipline d'exclusion reste entière : liste d'envies, adresse,
    Prime, financement, garantie et compagnie sont écartés avant même
    d'être lus. Seul un libellé d'achat explicite peut trancher ici.
    """
    try:
        controls = scope.find_all(["button", "a"], limit=200)
        controls += [
            tag for tag in scope.find_all("input", limit=200)
            if (tag.get("type") or "").lower() in ("submit", "button")
        ]
    except Exception:  # noqa: BLE001 — DOM inattendu
        return None

    for tag in controls:
        if _exclusion_reason(tag) is not None:
            continue
        label = _control_label(tag, scope)
        if _is_excluded_label(normalise(label)) is not None:
            continue
        action = _action_for_label(label)
        if action is None:
            continue
        return ActionResolution(
            action=action,
            selector=_describe(tag),
            label=label,
            origin="libellé de contrôle",
        )
    return None


def _from_texts(
    origin: str,
    sources: tuple[tuple[str, str], ...],
    table: tuple[tuple[tuple[str, ...], PurchaseAction], ...],
) -> Optional[ActionResolution]:
    """Première correspondance dans une table, sur des sources ordonnées.

    Chaque source est un couple (sélecteur, texte) : la décision annonce
    donc le sélecteur CSS réel, pas seulement le mot-clé trouvé.
    """
    for patterns, action in table:
        for selector, text in sources:
            normalised = normalise(text or "")
            if not normalised:
                continue
            marker = next((p for p in patterns if p in normalised), None)
            if marker is None:
                continue
            return ActionResolution(
                action=action, selector=selector, label=marker, origin=origin,
            )
    return None


def _action_for_label(label: str) -> Optional[PurchaseAction]:
    normalised = normalise(label or "")
    if not normalised:
        return None
    for patterns, action in LABEL_ACTIONS:
        if any(pattern in normalised for pattern in patterns):
            return action
    return None


def _control_label(tag, scope) -> str:
    """Libellé lisible d'un contrôle.

    Amazon place souvent le vrai libellé dans un `<span>` voisin suffixé
    `-announce` (destiné aux lecteurs d'écran), le bouton lui-même ne
    portant qu'une valeur technique. Il est donc consulté aussi.
    """
    for candidate in (
        tag.get("value") if tag.name == "input" else None,
        tag.get("aria-label"),
        tag.get_text(" ", strip=True) if tag.name != "input" else None,
        _announce_label(tag, scope),
    ):
        text = " ".join((candidate or "").split())
        if text:
            return text[:80]
    return ""


def _announce_label(tag, scope) -> str:
    identifier = tag.get("id") or ""
    if not identifier:
        return ""
    announce = _select_one(scope, f"#{identifier}-announce")
    if announce is None:
        return ""
    return " ".join(announce.get_text(" ", strip=True).split())


def _survey_controls(scope) -> tuple[tuple[IgnoredControl, ...], int]:
    """Recense les contrôles du périmètre : ceux écartés, et le total.

    Sert exclusivement au diagnostic : savoir que 22 boutons ont été
    ignorés pour cause de liste d'envies, livraison et Prime est ce qui
    permet de comprendre une décision en quelques secondes.
    """
    try:
        controls = scope.find_all(["button", "a"], limit=200)
        controls += [
            tag for tag in scope.find_all("input", limit=200)
            if (tag.get("type") or "").lower() in ("submit", "button")
        ]
    except Exception:  # noqa: BLE001 — DOM inattendu : le relevé n'est pas critique
        return (), 0

    ignored: list[IgnoredControl] = []
    for tag in controls:
        label = _plain_label(tag)
        reason = _exclusion_reason(tag) or _is_excluded_label(normalise(label))
        if reason is None:
            continue
        ignored.append(IgnoredControl(
            label=(label or "(sans libellé)")[:60],
            selector=_describe(tag),
            reason=reason,
        ))
    return tuple(ignored), len(controls)


def _plain_label(tag) -> str:
    """Libellé brut d'un contrôle, sans interprétation."""
    raw = (
        tag.get("value") if tag.name == "input"
        else tag.get_text(" ", strip=True)
    ) or tag.get("aria-label") or ""
    return " ".join(raw.split())


def _exclusion_reason(tag) -> Optional[str]:
    """Motif d'exclusion structurel : l'élément, ou le bloc qui le porte."""
    reason = _fragment_reason(tag)
    if reason is not None:
        return reason

    for depth, parent in enumerate(tag.parents):
        if depth >= _ANCESTOR_DEPTH or getattr(parent, "get", None) is None:
            break
        if parent.name in ("body", "html"):
            break
        reason = _fragment_reason(parent)
        if reason is not None:
            return reason
    return None


def _fragment_reason(tag) -> Optional[str]:
    """Motif d'exclusion lu dans l'identifiant, le nom ou les classes."""
    if getattr(tag, "get", None) is None:
        return None
    haystack = normalise(" ".join(filter(None, [
        tag.get("id") or "",
        tag.get("name") or "",
        " ".join(tag.get("class") or []),
        tag.get("data-component-type") or "",
    ])))
    if not haystack:
        return None
    for fragment, reason in EXCLUDED_FRAGMENTS:
        if fragment in haystack:
            return reason
    return None


def _is_excluded_label(normalised_label: str) -> Optional[str]:
    if not normalised_label:
        return None
    for pattern, reason in keywords.EXCLUDED_LABELS:
        if pattern in normalised_label:
            return reason
    return None


def _select_one(node, selector: str):
    try:
        return node.select_one(selector)
    except Exception:  # noqa: BLE001 — sélecteur invalide
        return None


def _describe(node) -> str:
    if node is None or not getattr(node, "name", None):
        return "—"
    if node.get("id"):
        return f"{node.name}#{node.get('id')}"
    classes = node.get("class")
    if classes:
        return f"{node.name}.{'.'.join(classes[:2])}"
    return node.name
