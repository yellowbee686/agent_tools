"""Adapter registry — list every supported agent here."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Union

from ..core.adapter import Adapter, AdapterRegistry, AdapterRoot, MultiRootAdapter
from .cc.adapter import CCAdapter
from .codex.adapter import CodexAdapter


RootInput = Union[Path, AdapterRoot]
RootsInput = Union[RootInput, Sequence[RootInput]]


def _as_root_specs(value: RootsInput) -> tuple[AdapterRoot, ...]:
    if isinstance(value, AdapterRoot):
        return (value,)
    if isinstance(value, Path):
        return (AdapterRoot(path=value),)
    specs: list[AdapterRoot] = []
    for item in value:
        if isinstance(item, AdapterRoot):
            specs.append(item)
        else:
            specs.append(AdapterRoot(path=item))
    return tuple(specs)


def _adapter_for_roots(
    adapter_cls: type[Adapter], roots: tuple[AdapterRoot, ...]
) -> Adapter:
    adapters: list[Adapter] = []
    for spec in roots:
        adapter = adapter_cls(root=spec.path)
        adapter.root_source = spec.source
        adapter.root_label = spec.label or str(spec.path)
        adapters.append(adapter)
    return MultiRootAdapter(adapters)


def build_registry(
    *,
    codex_root: RootsInput,
    cc_root: RootsInput,
) -> AdapterRegistry:
    """Construct the default adapter registry from CLI configuration."""
    return AdapterRegistry(
        [
            _adapter_for_roots(CodexAdapter, _as_root_specs(codex_root)),
            _adapter_for_roots(CCAdapter, _as_root_specs(cc_root)),
        ]
    )
