"""Build immutable bundled catalog metadata, avoiding markdown scans at launch."""

from __future__ import annotations

import dataclasses
import json
import runpy
import sys
from pathlib import Path


def build(root: Path) -> Path:
    from octop.infra.agents.subagents.catalog import SubagentCatalog

    target = root / "catalog-index.json"
    target.unlink(missing_ok=True)
    catalog = SubagentCatalog(root)
    catalog.refresh()
    data = {
        "format": 1,
        "divisions": {key: dataclasses.asdict(value) for key, value in catalog._divisions.items()},
        "divisions_by_locale": {
            locale: {key: dataclasses.asdict(value) for key, value in rows.items()}
            for locale, rows in catalog._divisions_by_locale.items()
        },
        "subagents": {key: dataclasses.asdict(value) for key, value in catalog._subagents.items()},
    }
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return target


if __name__ == "__main__":
    runtime = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(runtime))
    runpy.run_path(str(runtime / "launch.py"))["_bootstrap"]()
    print(build(runtime / "packages/octop/infra/agents/subagents"))
