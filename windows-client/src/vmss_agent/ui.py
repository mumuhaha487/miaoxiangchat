"""Compatibility import for the hidden Agent host.

The visible Windows interface is the shared Web desktop in :mod:`vmss_agent.desktop`.
"""

from .worker import AgentHost


AgentWindow = AgentHost
