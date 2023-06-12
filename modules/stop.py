from typing import Union

from graia.ariadne import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.parser.twilight import (
    FullMatch,
    SpacePolicy,
    Twilight,
)
from graia.ariadne.model import Friend, Group
from graia.saya import Channel, Saya
from graia.saya.builtins.broadcast import ListenerSchema

SAYA = Saya.current()
CHANNEL = Channel.current()

dispatcher = Twilight(
    FullMatch("/subscribe").space(SpacePolicy.FORCE), FullMatch("remove")
)


@CHANNEL.use(
    ListenerSchema(
        listening_events=[FriendMessage, GroupMessage], inline_dispatchers=[dispatcher]
    )
)
async def subscribe_remove_handle(app: Ariadne, sender: Union[Friend, Group]):
    """删除一个任务"""
    await app.send_message(sender, f"start command")
