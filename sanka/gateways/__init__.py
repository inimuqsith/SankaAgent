"""Gateway adalah appendage: transport masuk keluar core, tanpa bocor ke engine."""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points

from ..engine.config import ConfigError

log = logging.getLogger(__name__)

BUILTIN = {
    "whatsapp": "sanka.gateways.whatsapp.gateway:WhatsAppGateway",
    "cli": "sanka.gateways.cli.gateway:CliGateway",
}


def ensure_schemas(db) -> list[str]:
    """Buat tabel khusus gateway (murni SQLAlchemy, tanpa koneksi transport)."""
    done: list[str] = []
    for module_name in ("sanka.gateways.whatsapp.schema",):
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # extra gateway belum terpasang
            continue
        module.ensure_schema(db)
        done.append(module_name)
    return done


def load_gateway(name: str):
    """Kelas gateway: builtin dulu, lalu entry point `sanka.gateways`."""
    target = BUILTIN.get(str(name))
    if target is None:
        try:
            found = entry_points(group="sanka.gateways")
        except Exception:
            found = []
        for entry in found:
            if entry.name == name:
                target = entry.value
                break
    if target is None:
        raise ConfigError(f"gateway '{name}' tidak dikenal")
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(
            f"gateway '{name}' butuh extra transport (pip install -e '.[{name}]'): {exc}"
        ) from exc
    return getattr(module, attribute)


def build_gateways(
    config,
    *,
    db,
    history,
    windows,
    only: tuple[str, ...] = (),
    force: tuple[str, ...] = (),
) -> list:
    """Bangun gateway aktif dari gateway.yaml; `only`/`force` dipakai console."""
    found: list = []
    names = list(config.gateways)
    for name in only:
        if name not in names:
            names.append(name)
    for name in names:
        settings = config.gateways.get(name)
        enabled = bool(settings and settings.enabled) or name in force
        if not enabled:
            continue
        if only and name not in only:
            continue
        cls = load_gateway(name)
        found.append(cls(config, db, history, windows, settings=settings))
    if not found:
        raise ConfigError("tidak ada gateway aktif; isi gateway.yaml")
    return found
