from typing import List, Callable, Coroutine

from telegram import Chat, User, Update

from tvsubscribebot.utils.types import RESULT_TEXT


class HandlerCallbacks:
    def __init__(self):
        self._callbacks: List[Callable[[RESULT_TEXT, Chat, User], Coroutine]] = []

    def register_callback(self, func: Callable[[RESULT_TEXT, Chat], Coroutine]) -> Callable[[RESULT_TEXT, Chat], Coroutine]:
        """Register coroutine callback for handling result text, can be used as a decorator."""
        self._callbacks.append(func)
        return func

    async def notify_handle_result(self, text: RESULT_TEXT, update: Update):
        for callback in self._callbacks:
            await callback(text, update.effective_chat, update.effective_user)
