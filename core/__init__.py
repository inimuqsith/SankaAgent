"""SankaAgent core runtime.

Houses the agent execution loop adapted from GenericAgent. Plan-2 uses this
package for the LLM turn loop without tools, memory, or gateway code.
"""

from __future__ import annotations

__all__ = ["agent_loop"]
