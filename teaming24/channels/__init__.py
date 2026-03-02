"""
Multi-channel messaging for Teaming24.

Provides a unified ChannelAdapter interface so agents can receive
messages from and respond to Telegram, Slack, Discord, and the
built-in WebChat GUI through a single pipeline.
"""

from teaming24.channels.base import ChannelAdapter, InboundMessage
from teaming24.channels.manager import ChannelManager
from teaming24.channels.router import Binding, BindingMatch, BindingRouter

__all__ = [
    "Binding",
    "BindingMatch",
    "BindingRouter",
    "ChannelAdapter",
    "ChannelManager",
    "InboundMessage",
]
