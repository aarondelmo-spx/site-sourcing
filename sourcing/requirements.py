"""
Expansion Requirements — structured intake for warehouse search specs.

Stores each requirement as a JSON file in data/requirements/{id}.json.
Provides Claude-powered NL parse (optional, degrades gracefully without API key).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


@dataclass
class ExpansionRequirement:
    project_name: str
    requirement_id: str                         # uuid4, links pipeline entries
    sqm_min: float
    sqm_max: float
    region_priority: List[str]                  # ordered ["Laguna", "Cavite", ...]
    budget_max_sqm_month: float                 # ₱/sqm/month (0 = no limit)
    dock_doors_min: int
    clear_height_min: float                     # metres
    peza_required: bool
    slex_max_km: float                          # 60 = no limit (Luzon only)
    power_requirement_kva: Optional[float] = None  # None = no constraint
    notes: str = ""
    created_at: str = ""

    @property
    def budget_max_total(self) -> Optional[float]:
        """Estimated max total monthly cost = budget_per_sqm × sqm_max."""
        if self.budget_max_sqm_month <= 0 or self.sqm_max <= 0:
            return None
        return self.budget_max_sqm_month * self.sqm_max

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExpansionRequirement":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def summary_line(self) -> str:
        """One-line summary for UI display."""
        parts = [f"**{self.project_name}**"]
        if self.sqm_min or self.sqm_max:
            parts.append(f"{self.sqm_min:,.0f}–{self.sqm_max:,.0f} sqm")
        if self.region_priority:
            parts.append(", ".join(self.region_priority[:2]) +
                         ("…" if len(self.region_priority) > 2 else ""))
        if self.budget_max_sqm_month > 0:
            parts.append(f"≤₱{self.budget_max_sqm_month:,.0f}/sqm/mo")
        if self.dock_doors_min > 0:
            parts.append(f"≥{self.dock_doors_min} docks")
        return "  ·  ".join(parts)


# ── Storage ───────────────────────────────────────────────────────────────────

def _req_dir(data_dir: str) -> str:
    p = os.path.join(data_dir, "requirements")
    os.makedirs(p, exist_ok=True)
    return p


def save_requirement(req: ExpansionRequirement, data_dir: str) -> None:
    path = os.path.join(_req_dir(data_dir), f"{req.requirement_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(req.to_dict(), f, indent=2, ensure_ascii=False)


def load_requirements(data_dir: str) -> List[ExpansionRequirement]:
    """Load all saved requirements, newest first."""
    d = _req_dir(data_dir)
    reqs = []
    for fn in os.listdir(d):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    reqs.append(ExpansionRequirement.from_dict(json.load(f)))
            except Exception:
                pass
    reqs.sort(key=lambda r: r.created_at, reverse=True)
    return reqs


def new_requirement(project_name: str = "New Search") -> ExpansionRequirement:
    """Create a blank requirement with sane defaults."""
    return ExpansionRequirement(
        project_name=project_name,
        requirement_id=str(uuid.uuid4()),
        sqm_min=0.0,
        sqm_max=10000.0,
        region_priority=[],
        budget_max_sqm_month=0.0,
        dock_doors_min=0,
        clear_height_min=0.0,
        peza_required=False,
        slex_max_km=60.0,
        power_requirement_kva=None,
        notes="",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Claude NL parse ───────────────────────────────────────────────────────────

_PARSE_SYSTEM = """\
You extract warehouse search requirements from natural language into structured JSON.

Return ONLY valid JSON — no markdown, no explanation, no code fences.

Fields to extract (use null if not mentioned):
{
  "project_name": "string — name the user gave, or a short descriptive name",
  "sqm_min": number or null,
  "sqm_max": number or null,
  "region_priority": ["ordered list of PH province/region names mentioned, e.g. Laguna, Cavite, NCR"],
  "budget_max_sqm_month": number or null (PHP per sqm per month; convert if total monthly given: total/sqm_max),
  "dock_doors_min": integer or null,
  "clear_height_min": number or null (metres; convert from feet if needed: ft × 0.3048),
  "peza_required": true/false/null,
  "slex_max_km": number or null (km from SLEX; 60 if not mentioned),
  "power_requirement_kva": number or null,
  "notes": "any extra context not captured in structured fields"
}

Philippine region name normalization:
- "Metro Manila" / "NCR" → "NCR"
- "South" / "south of Manila" → include "Laguna", "Cavite"
- "Laguna" → "Laguna"
- Mindanao / Cebu / Visayas → use the actual province name
"""

_PARSE_DEFAULTS = {
    "project_name": "New Search",
    "sqm_min": 0.0,
    "sqm_max": 10000.0,
    "region_priority": [],
    "budget_max_sqm_month": 0.0,
    "dock_doors_min": 0,
    "clear_height_min": 0.0,
    "peza_required": False,
    "slex_max_km": 60.0,
    "power_requirement_kva": None,
    "notes": "",
}


def parse_requirement_nl(text: str) -> tuple[dict, str | None]:
    """
    Parse a natural language requirement description using Claude.

    Returns:
        (parsed_dict, error_message)
        parsed_dict: structured fields merged with defaults (always complete)
        error_message: None on success, string on any error

    Never raises — always returns a usable dict even on failure.
    """
    if not _ANTHROPIC_KEY:
        return dict(_PARSE_DEFAULTS), "ANTHROPIC_API_KEY not set — using manual form."

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=512,
            system=_PARSE_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = msg.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)

        # Merge with defaults, apply null → default fallback
        result = dict(_PARSE_DEFAULTS)
        for k, default in _PARSE_DEFAULTS.items():
            v = parsed.get(k)
            if v is None:
                result[k] = default
            else:
                result[k] = v

        # Coerce types
        result["sqm_min"] = float(result["sqm_min"] or 0)
        result["sqm_max"] = float(result["sqm_max"] or 10000)
        result["budget_max_sqm_month"] = float(result["budget_max_sqm_month"] or 0)
        result["dock_doors_min"] = int(result["dock_doors_min"] or 0)
        result["clear_height_min"] = float(result["clear_height_min"] or 0)
        result["slex_max_km"] = float(result["slex_max_km"] or 60)
        result["peza_required"] = bool(result["peza_required"])
        if not isinstance(result["region_priority"], list):
            result["region_priority"] = []

        return result, None

    except json.JSONDecodeError as e:
        return dict(_PARSE_DEFAULTS), f"Claude returned malformed JSON: {e}"
    except Exception as e:
        return dict(_PARSE_DEFAULTS), f"Parse error: {e}"
