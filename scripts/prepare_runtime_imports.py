"""Apply audited, hash-pinned import fixes to the release staging tree only.

Do not patch a developer venv or a user's installed runtime. Dependency updates
must explicitly review these transformations; unrecognised source fails a build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ORIGINAL_SHA256 = "79fb112b0507d5be105197582ff429bb42e45855b16cdf912315eeb06c0914ab"
PATCHED_SHA256 = "217b00fb45937ff5eeddfe58885ef93462fc46032b5baa7f3e1de729108b5b79"
RELATIVE = "deepagents/middleware/filesystem.py"


def transform(source: str) -> str:
    start = source.index("try:\n    from langchain_openai import AzureChatOpenAI")
    end = source.index("\nif TYPE_CHECKING:", start)
    source = source[:start] + source[end:]
    source = source.replace("import mimetypes\n", "import mimetypes\nimport sys\n", 1)
    return source.replace(
        "    return isinstance(model, _OPENAI_FILE_MODEL_TYPES + _GOOGLE_FILE_MODEL_TYPES)",
        """    # A constructed provider model already has its SDK in sys.modules.
    # Preserve isinstance/subclass semantics without importing unused SDKs.
    model_types = []
    for module_name, names in (
        ("langchain_openai", ("AzureChatOpenAI", "ChatOpenAI")),
        ("langchain_google_genai", ("ChatGoogleGenerativeAI",)),
    ):
        module = sys.modules.get(module_name)
        if module is not None:
            for name in names:
                cls = getattr(module, name, None)
                if isinstance(cls, type):
                    model_types.append(cls)
    return isinstance(model, tuple(model_types))""",
        1,
    )


def prepare(packages: Path) -> dict[str, str]:
    path = packages / RELATIVE
    original = path.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    if before == PATCHED_SHA256:
        record = json.loads((packages / "xfriend-runtime-patches.json").read_text("utf-8"))
        if (
            record.get("original_sha256") != ORIGINAL_SHA256
            or record.get("patched_sha256") != before
        ):
            raise ValueError("Runtime patch audit does not match the pinned dependency")
        return dict(record)
    if before != ORIGINAL_SHA256:
        raise ValueError("deepagents source changed; review runtime import patch before packaging")
    patched = transform(original.decode("utf-8"))
    compile(patched, str(path), "exec")
    path.write_bytes(patched.encode("utf-8"))
    record = {
        "dependency": "deepagents==0.7.9",
        "path": RELATIVE,
        "original_sha256": before,
        "patched_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "change": "Defer non-PDF provider capability types until their SDK is loaded",
    }
    (packages / "xfriend-runtime-patches.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", type=Path)
    print(prepare(parser.parse_args().packages)["change"])
