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
    FullMatch("/subscribe").space(SpacePolicy.FORCE),
    FullMatch("list").space(SpacePolicy.FORCE),
)


@CHANNEL.use(
    ListenerSchema(
        listening_events=[FriendMessage, GroupMessage], inline_dispatchers=[dispatcher]
    )
)
async def subscribe_list_handle(app: Ariadne, sender: Union[Friend, Group]):
    """添加单次预约节目

    `/subscribe once <channel keyword> <program keyword>`

    节目关键字如果有多个匹配，则直接触发检查预约事件，每个匹配询问一次用户是否订阅，如果无匹配则报错
    """
    await app.send_message(sender, f"subscribe list")
