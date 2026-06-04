# ==============================================================================
# Copyright 2026 Catskills Research Company
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Sequential shot workflow steps (checklist + button gating)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    callout: str
    button_label: str


# May be run again to refresh from session/disk cache (checkmark stays).
REPEATABLE_STEPS: frozenset[str] = frozenset({"optimize", "compile"})


WORKFLOW_STEPS: tuple[WorkflowStep, ...] = (
    WorkflowStep("optimize", "Optimize control sliders for best Q_net", "Optimize"),
    WorkflowStep(
        "compile",
        "Pre-render shot video + voice (required before record/review)",
        "Compile",
    ),
    WorkflowStep("rec_start", "Start MP4 frame capture", "Rec Start"),
    WorkflowStep(
        "review",
        "Play or Step through numbered callouts (after compile)",
        "",
    ),
    WorkflowStep(
        "rec_save",
        "Save MP4 (numbered narration subtitles on export)",
        "Rec Save",
    ),
)


def step_index(step_id: str) -> int:
    for i, s in enumerate(WORKFLOW_STEPS):
        if s.step_id == step_id:
            return i
    raise KeyError(step_id)


def can_run_step(step_id: str, completed: set[str]) -> tuple[bool, str]:
    """Return whether ``step_id`` may run and a short reason if not."""
    if step_id == "review":
        return False, "Use Play, Step, or Back."
    if step_id in completed and step_id not in REPEATABLE_STEPS:
        return False, "This step is already complete."
    if step_id == "rec_save":
        if "rec_start" not in completed:
            return False, "Complete “Rec Start” first."
        return True, ""
    idx = step_index(step_id)
    if idx > 0:
        prior = WORKFLOW_STEPS[idx - 1]
        if prior.step_id == "review":
            prior_id = WORKFLOW_STEPS[idx - 2].step_id if idx >= 2 else "compile"
            if prior_id not in completed:
                return False, f"Complete “{WORKFLOW_STEPS[idx - 2].button_label}” first."
        elif prior.step_id not in completed:
            label = prior.button_label or "Review"
            return False, f"Complete “{label}” first."
    return True, ""
