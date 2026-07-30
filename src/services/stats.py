"""Agrégations pour le dashboard (état global, page Monitors)."""

from __future__ import annotations

import time
from typing import Any

from src.core.engine import MonitorEngine
from src.monitors import MonitorRegistry
from src.repositories import AlertRepository, CheckRepository, ProductRepository


class StatsService:
    def __init__(
        self,
        products: ProductRepository,
        checks: CheckRepository,
        alerts: AlertRepository,
        registry: MonitorRegistry,
        engine: MonitorEngine,
        started_at: float,
    ) -> None:
        self._products = products
        self._checks = checks
        self._alerts = alerts
        self._registry = registry
        self._engine = engine
        self._started_at = started_at

    @property
    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self._started_at)

    async def overview(self) -> dict[str, Any]:
        """État global affiché en tête du dashboard."""
        last_check = await self._checks.last()
        return {
            "monitor_active": self._engine.active_count > 0,
            "products_total": await self._products.count(),
            "products_enabled": await self._products.count_enabled(),
            "products_watched": self._engine.active_count,
            "sites_count": len(await self._products.sites()),
            "last_check_at": last_check.checked_at if last_check else None,
            "last_alert_at": await self._alerts.last_created_at(),
            "uptime_seconds": self.uptime_seconds,
            "checks_total": await self._checks.count(),
            "alerts_total": await self._alerts.count(),
            "avg_response_ms_24h": await self._checks.avg_response_time(hours=24),
        }

    async def monitors(self) -> list[dict[str, Any]]:
        """Une entrée par plugin chargé, enrichie des agrégats du site."""
        counts = await self._products.count_by_site()
        watched_by_site: dict[str, int] = {}
        for product in self._engine.active_products:
            watched_by_site[product.site] = watched_by_site.get(product.site, 0) + 1

        entries: list[dict[str, Any]] = []
        for site in self._registry.known_sites:
            metadata = self._registry.get_metadata(site)
            stats = await self._checks.site_stats(site)
            entries.append({
                "site": site,
                "display_name": self._registry.get(site).display_name or site,
                "version": metadata.version if metadata else None,
                "base_url": metadata.base_url if metadata else None,
                "description": metadata.description if metadata else None,
                "product_count": counts.get(site, 0),
                "watched_count": watched_by_site.get(site, 0),
                **stats,
            })
        return entries
