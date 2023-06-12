from typing import Union

from graia.ariadne import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.parser.twilight import (
    FullMatch,
    ParamMatch,
    RegexResult,
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
    FullMatch("crontab").space(SpacePolicy.FORCE),
    "index" @ ParamMatch().space(SpacePolicy.FORCE),
    "crontab" @ ParamMatch(),
)


@CHANNEL.use(
    ListenerSchema(
        listening_events=[FriendMessage, GroupMessage], inline_dispatchers=[dispatcher]
    )
)
async def subscribe_crontab_handle(
    app: Ariadne, sender: Union[Friend, Group], index: RegexResult, crontab: RegexResult
):
    """修改任务的触发时间"""
    index = str(index.result)
    crontab = str(crontab.result)
    await app.send_message(sender, f"index={index}\ncrontab={crontab}")
