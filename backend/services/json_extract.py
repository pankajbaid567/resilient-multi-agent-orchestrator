"""Pull the first valid JSON object out of model output.

Models wrap JSON in prose or markdown fences, and some providers' JSON modes emit artefacts
(Bedrock gpt-oss in JSON-object mode returns "{\\n{...}" with a doubled opening brace).
Trying a decode at every "{" and keeping the first dict handles all of these.
"""

from __future__ import annotations

import json
import re
from typing import Any

_DECODER = json.JSONDecoder()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object found in ``text``, or None."""
    raw = text or ""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1), raw] if fenced else [raw]
    for candidate in candidates:
        for match in re.finditer(r"\{", candidate):
            try:
                value, _end = _DECODER.raw_decode(candidate, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None
