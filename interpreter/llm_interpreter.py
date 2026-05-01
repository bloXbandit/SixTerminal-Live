"""
llm_interpreter.py — Translate natural language schedule edit instructions
into structured JSON edit commands using Claude (Anthropic) or OpenAI models.

The LLM never touches the schedule file directly.
It only produces a JSON list of edit commands that the edit engine applies.

Supported actions (must match edit_engine.py):
  rename_activity, update_duration, update_activity_id,
  add_activity, delete_activity, add_relation, delete_relation,
  rename_wbs, add_wbs, move_activity_wbs,
  bulk_rename, bulk_update_duration,
  set_constraint, clear_constraint

Supported models: claude, gpt-4.1-mini, gpt-4.1-nano, gpt-5.4-mini
"""
import os
import json
import re
from typing import List, Dict, Any, Optional, Tuple

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


# Supported model configurations
MODELS = {
    "claude": {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "label": "Claude (Anthropic)",
    },
    "gpt-4.1-mini": {
        "provider": "openai",
        "model_id": "gpt-4.1-mini",
        "label": "GPT-4.1 Mini (OpenAI)",
    },
    "gpt-4.1-nano": {
        "provider": "openai",
        "model_id": "gpt-4.1-nano",
        "label": "GPT-4.1 Nano (OpenAI)",
    },
    "gpt-5.4-mini": {
        "provider": "openai",
        "model_id": "gpt-5.4-mini",
        "label": "GPT-5.4 Mini (OpenAI)",
    },
}

DEFAULT_MODEL = "gpt-4.1-mini"


SYSTEM_PROMPT = """You are a Primavera P6 schedule editing assistant for Six Terminal Live.

Your job is to translate natural language schedule edit instructions into a JSON list of edit commands.

RULES:
1. Always respond with ONLY a valid JSON array of edit command objects. No explanation, no markdown, no extra text.
2. Each command must have an "action" key. All other keys depend on the action.
3. If the instruction is ambiguous, produce the safest interpretation and add a "note" key explaining your assumption.
4. If the instruction cannot be translated into supported actions, return: [{"action": "error", "message": "explanation"}]
5. Durations are always in DAYS in the JSON output (the engine converts to hours internally).
6. Relation types: use "fs" (Finish to Start), "ss" (Start to Start), "ff" (Finish to Finish), "sf" (Start to Finish).

SUPPORTED ACTIONS AND THEIR REQUIRED KEYS:

rename_activity:
  {"action": "rename_activity", "activity_id": "A1000", "new_name": "New Name"}
  OR by name: {"action": "rename_activity", "target_name": "Old Name", "new_name": "New Name", "apply_to_all": false}

update_duration:
  {"action": "update_duration", "activity_id": "A1000", "new_duration_days": 5}
  OR by name: {"action": "update_duration", "target_name": "Install Drywall", "new_duration_days": 5, "apply_to_all": true}

update_activity_id:
  {"action": "update_activity_id", "activity_id": "A1000", "new_activity_id": "A1000-REV"}

add_activity:
  {"action": "add_activity", "activity_id": "A1099", "name": "Owner Punch Walk", "wbs_name": "Closeout", "duration_days": 3, "activity_type": "Task Dependent"}

delete_activity:
  {"action": "delete_activity", "activity_id": "A1000"}

add_relation:
  {"action": "add_relation", "predecessor_id": "A1000", "successor_id": "A1010", "type": "fs", "lag_days": 0}
  OR by name: {"action": "add_relation", "predecessor_name": "Pour Slab", "successor_name": "Frame Walls", "type": "fs"}

delete_relation:
  {"action": "delete_relation", "predecessor_id": "A1000", "successor_id": "A1010"}

rename_wbs:
  {"action": "rename_wbs", "wbs_name": "Structure", "new_name": "Structural Steel & Concrete"}

add_wbs:
  {"action": "add_wbs", "name": "Finishes", "code": "FIN", "parent_name": "Interior"}

move_activity_wbs:
  {"action": "move_activity_wbs", "activity_id": "A1040", "wbs_name": "Finishes"}

bulk_rename:
  {"action": "bulk_rename", "pattern": "Level (\\d+)", "replacement": "Floor \\1"}

bulk_update_duration:
  {"action": "bulk_update_duration", "pattern": "Install Drywall", "new_duration_days": 5}

set_constraint:
  {"action": "set_constraint", "activity_id": "A1000", "constraint_type": "Start On Or After", "constraint_date": "2026-06-01"}

clear_constraint:
  {"action": "clear_constraint", "activity_id": "A1000"}

EXAMPLES:

User: "Change the duration of Install Drywall to 5 days"
Response: [{"action": "update_duration", "target_name": "Install Drywall", "new_duration_days": 5, "apply_to_all": true}]

User: "Rename WBS node 'Structure' to 'Structural Steel & Concrete'"
Response: [{"action": "rename_wbs", "wbs_name": "Structure", "new_name": "Structural Steel & Concrete"}]

User: "Add a new activity A1099 called Owner Punch Walk under the Closeout WBS, 3 days, FS from Substantial Completion"
Response: [
  {"action": "add_activity", "activity_id": "A1099", "name": "Owner Punch Walk", "wbs_name": "Closeout", "duration_days": 3},
  {"action": "add_relation", "predecessor_name": "Substantial Completion", "successor_id": "A1099", "type": "fs"}
]

User: "Tie all Level 1 MEP rough-in activities to the Level 2 slab pour as FS predecessors"
Response: [{"action": "add_relation", "predecessor_name": "Level 2 Slab Pour", "successor_name": "Level 1 MEP Rough-In", "type": "fs", "note": "Applied to all activities matching 'Level 1 MEP Rough-In' — verify activity IDs before importing"}]
"""


def _build_context_summary(project_summary: Optional[str]) -> str:
    if not project_summary:
        return ""
    return f"\n\nCurrent schedule context:\n{project_summary}"


def interpret(
    instruction: str,
    project_summary: Optional[str] = None,
    model_key: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Translate a natural language instruction into a list of edit commands.

    Args:
        instruction:     Natural language edit instruction from the user.
        project_summary: Optional schedule context string to include in the prompt.
        model_key:       One of the keys in MODELS dict (e.g. "gpt-4.1-mini", "claude").
                         Defaults to DEFAULT_MODEL.
        api_key:         API key to use. If None, falls back to environment variables.

    Returns:
        (commands: list of dicts, raw_response: str)

    Raises:
        RuntimeError if no LLM API is available or configured.
    """
    user_message = instruction.strip()
    if project_summary:
        user_message += _build_context_summary(project_summary)

    # Resolve model config
    model_cfg = MODELS.get(model_key)
    if model_cfg is None:
        # Try matching by provider name
        for k, v in MODELS.items():
            if v["provider"] == model_key:
                model_cfg = v
                break
    if model_cfg is None:
        model_cfg = MODELS[DEFAULT_MODEL]

    provider = model_cfg["provider"]
    model_id = model_cfg["model_id"]

    # --- Anthropic ---
    if provider == "anthropic":
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not resolved_key:
            raise RuntimeError("Anthropic API key not set. Enter your key in the settings panel or set ANTHROPIC_API_KEY.")
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        client = anthropic.Anthropic(api_key=resolved_key)
        response = client.messages.create(
            model=model_id,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw_response = response.content[0].text
        return _parse_commands(raw_response), raw_response

    # --- OpenAI ---
    if provider == "openai":
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not resolved_key:
            raise RuntimeError("OpenAI API key not set. Enter your key in the settings panel or set OPENAI_API_KEY.")
        if not _OPENAI_AVAILABLE:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        client = OpenAI(api_key=resolved_key)
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=2048,
        )
        raw_response = response.choices[0].message.content
        return _parse_commands(raw_response), raw_response

    raise RuntimeError(f"Unknown provider '{provider}' for model '{model_key}'.")


def _parse_commands(raw: str) -> List[Dict[str, Any]]:
    """
    Extract JSON array from LLM response.
    Handles cases where the model wraps JSON in markdown code blocks.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()

    # Find the JSON array
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM returned invalid JSON: {e}\nRaw: {raw[:500]}")

    raise ValueError(f"LLM response did not contain a JSON array.\nRaw: {raw[:500]}")
