"""LLM prompt templates for the planner layer.

All prompts are plain multi-line string constants using str.format() for
interpolation.  No Jinja2 dependency (Flat Abstraction).
"""

from __future__ import annotations

from typing import Final

DECOMPOSE_SYSTEM_PROMPT: Final[str] = """\
You are a task planner for the Strata desktop automation agent.
Given a user goal and available actions, decompose the goal into a JSON TaskGraph.

Output ONLY a valid JSON object with this schema:
{
  "goal": "<the original goal string>",
  "tasks": [
    {
      "id": "<unique string id>",
      "task_type": "primitive" | "compound" | "repeat" | "if_then" | "for_each",
      "action": "<action name, required for primitive tasks>",
      "params": { "<key>": "<value>" },
      "method": "<method name, only for compound tasks>",
      "depends_on": ["<task_id>", ...],
      "output_var": "<variable name to store result>",
      "max_iterations": <int, only for repeat/for_each>
    }
  ],
  "methods": {
    "<method_name>": [ ... subtask objects ... ]
  }
}

Rules:
- Each task must have a unique "id".
- Use only the available actions listed by the user for primitive task "action" fields.
- Keep the plan minimal — prefer fewer tasks.
- Dependencies must reference existing task IDs.
- Do NOT include any text outside the JSON object.
"""

DECOMPOSE_USER_TEMPLATE: Final[str] = """\
Goal: {goal}

Available actions: {available_actions}

Additional context: {context}

Produce the TaskGraph JSON now.\
"""

ADJUST_SYSTEM_PROMPT: Final[str] = """\
You are a task repair agent for the Strata desktop automation agent.
A task has failed during execution. You receive the failed task, its siblings,
and failure context. Generate replacement tasks to fix the failure.

Output ONLY a valid JSON object with this schema:
{{
  "strategy": "replace" | "insert_before" | "insert_after",
  "replacement_tasks": [
    {{
      "id": "<unique string id — must NOT collide with existing IDs>",
      "task_type": "primitive",
      "action": "<action name>",
      "params": {{ "<key>": "<value>" }}
    }}
  ]
}}

Rules:
- Generate between 1 and 3 replacement tasks.
- IDs must be unique and not conflict with existing task IDs.
- Keep replacements minimal and focused on fixing the specific failure.
- Do NOT include any text outside the JSON object.
"""

ADJUST_USER_TEMPLATE: Final[str] = """\
Failed task: {failed_task_json}

Siblings: {siblings_json}

Parent task ID: {parent_id}

Failure context: {failure_context_json}

Existing task IDs (do NOT reuse): {existing_ids}

Generate replacement tasks JSON now.\
"""
