"""Controlled runtime for the Question Evolution Agent.

The package deliberately orchestrates registered project entry points only.  It
does not contain question-generation, routing, scoring, or prompt logic.
"""

from .task import AgentTask, TaskValidationError, load_agent_task

__all__ = ["AgentTask", "TaskValidationError", "load_agent_task"]
