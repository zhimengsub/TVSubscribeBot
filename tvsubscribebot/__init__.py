import sys
import os

_package = os.path.dirname(__file__)
sys.path.extend([
    _package,
    os.path.join(_package, 'tv_subscriber'),
    os.path.join(_package, 'dumb_bot'),
])

from tvsubscribebot._tvsubscribebot import TVSubscribeBot
from tvsubscribebot._textsubmitter import TextSubmitter
from tvsubscribebot.dumb_bot.dumbbot import User, Chat, Update, Message
from tvsubscribebot.tv_subscriber.tvsubscriber import Channel, Event, Reservation

__all__ = (
    'User',
    'Chat',
    'Update',
    'Message',
    'TVSubscribeBot',
    'TextSubmitter',
    'Channel',
    'Event',
    'Reservation'
)
