"""Synchronise the factual plugin/datapack inventory from YAML and the panel."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.config import Settings
from app.db.models import ServerAsset
from app.db.session import Database
from app.kb.ingest_docs import load_datapacks, load_plugins
from app.server.pterodactyl import PanelError, PterodactylClient

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(?P<name>.+?)[-_ ]v?(?P<version>\d+(?:\.\d+){1,3}[^.]*)$")


def _asset_name(filename: str) -> tuple[str, str | None]:
    stem = filename.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(jar|zip)$", "", stem, flags=re.IGNORECASE)
    match = _VERSION_RE.match(stem)
    if match:
        return match.group("name").strip("-_ "), match.group("version").strip()
    return stem, None


class AssetService:
    """Keeps `server_assets` in sync.

    YAML is the reviewed baseline. The panel only adds jars/datapacks it can see,
    and a panel outage never removes known facts.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        panel: PterodactylClient | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.panel = panel
        self.plugins = load_plugins(settings)
        self.datapacks = load_datapacks(settings)

    async def sync(self, *, from_panel: bool = True) -> int:
        rows: list[tuple[str, str, str | None, str]] = []
        for item in self.plugins:
            rows.append(("plugin", item["name"], item.get("version"), "kb"))
        for item in self.datapacks:
            rows.append(("datapack", item["name"], item.get("version"), "kb"))
        if from_panel and self.panel is not None and self.panel.ready:
            try:
                rows.extend(await self._panel_rows())
            except PanelError:
                logger.warning("Panel asset sync unavailable", exc_info=True)
        async with self.database.session() as session:
            existing = {
                (row.kind, row.name): row
                for row in (await session.scalars(select(ServerAsset))).all()
            }
            for kind, name, version, source in rows:
                key = (kind, name)
                row = existing.get(key)
                if row is None:
                    session.add(
                        ServerAsset(
                            kind=kind,
                            name=name,
                            version=version,
                            source=source,
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    if version:
                        row.version = version
                    row.source = source
                    row.updated_at = datetime.now(UTC)
            await session.flush()
        return len(rows)

    async def _panel_rows(self) -> list[tuple[str, str, str | None, str]]:
        assert self.panel is not None
        result: list[tuple[str, str, str | None, str]] = []
        try:
            for item in await self.panel.list_files("/plugins"):
                if item.get("is_file") and str(item.get("name", "")).endswith(
                    (".jar", ".jar.disabled")
                ):
                    name, version = _asset_name(str(item["name"]))
                    result.append(("plugin", name, version, "panel"))
        except PanelError:
            logger.info("Panel plugin listing unavailable")
        for directory in ("/world/datapacks", "/world/datapacks/../datapacks"):
            try:
                for item in await self.panel.list_files(directory):
                    if item.get("is_file") and str(item.get("name", "")).endswith(".zip"):
                        name, version = _asset_name(str(item["name"]))
                        result.append(("datapack", name, version, "panel"))
                break
            except PanelError:
                continue
        return result

    async def clear(self) -> None:
        async with self.database.session() as session:
            await session.execute(delete(ServerAsset))

    async def list_assets(self, kind: str | None = None) -> list[ServerAsset]:
        async with self.database.session() as session:
            stmt = select(ServerAsset).order_by(ServerAsset.kind, ServerAsset.name)
            if kind:
                stmt = stmt.where(ServerAsset.kind == kind)
            return list((await session.scalars(stmt)).all())
