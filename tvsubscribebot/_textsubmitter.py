import random

from dumb_bot.dumbbot import Message, UpdateGenerator, User, Chat
from tvsubscribebot.utils.casts import *

DEFAULT_USER = User(0, 'dummy', False, username='dummyuser')
DEFAULT_CHAT = Chat(0, Chat.PRIVATE, username='dummyuser')


class TextSubmitter:
    _MESSAGE_ID_MAX = 100000

    """dispatches text message to TVSubscribeBot, run this in a separate thread from TVSubscribeBot."""
    def __init__(self, listen: str = "127.0.0.1", port: int = 18888, timezone=pytz.timezone('Asia/Shanghai')):
        self._ids = {
            'message': random.randint(0, self._MESSAGE_ID_MAX),
        }
        self._update_generator = UpdateGenerator(listen, port)
        self.timezone = timezone

    def submit_text(
        self,
        text: str,
        date: datetime.datetime = None,
        chat: Chat = DEFAULT_CHAT,
        from_user: User = DEFAULT_USER,
        message_id: int = None,
    ) -> bool:
        """处理输入字符串，发送给application.
        返回值为发送是否成功。
        """
        date = date or datetime.datetime.now(self.timezone)
        message_id = message_id if message_id is not None else self._next_message_id()
        message = Message(message_id, date, chat, from_user, text=text)
        # push update, handled by callbacks
        return self._update_generator.automatic(message)

    def _next_message_id(self):
        self._ids['message'] = (self._ids['message'] + 1) % self._MESSAGE_ID_MAX
        return self._ids['message']
