"""
Teaming24 Gateway — central orchestrator for all inbound messages.

The Gateway ties together:
  - Channels (Telegram, Slack, Discord, WebChat)
  - Binding router (message → agent routing)
  - Session manager (conversation state)
  - Agent framework (LocalCrew / native runtime)
  - Payment gate (x402)
  - Hooks (lifecycle events)
  - Event broadcasting (WebSocket / SSE)

Usage::

    from teaming24.gateway import get_gateway

    gw = get_gateway()
    await gw.start()          # start all channels
    result = await gw.execute("hello", channel="webchat")
    await gw.stop()
"""

from teaming24.gateway.gateway import Gateway, get_gateway

__all__ = ["Gateway", "get_gateway"]
