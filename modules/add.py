from typing import Union

from graia.ariadne import Ariadne
from graia.ariadne.event.message import FriendMessage, GroupMessage
from graia.ariadne.message.parser.twilight import (
    FullMatch,
    ParamMatch,
    RegexResult,
    SpacePolicy,
    Twilight,
    WildcardMatch,
)
from graia.ariadne.model import Friend, Group
from graia.saya import Channel, Saya
from graia.saya.builtins.broadcast import ListenerSchema

SAYA = Saya.current()
CHANNEL = Channel.current()

dispatcher = Twilight(
    FullMatch("/subscribe").space(SpacePolicy.FORCE),
    FullMatch("add").space(SpacePolicy.FORCE),
    "channel" @ ParamMatch().space(SpacePolicy.FORCE),
    "program" @ ParamMatch(),
    "crontab" @ WildcardMatch(optional=True),
)


@CHANNEL.use(
    ListenerSchema(
        listening_events=[FriendMessage, GroupMessage],
        inline_dispatchers=[dispatcher],
    )
)
async def subscribe_add(
    app: Ariadne,
    sender: Union[Group, Friend],
    channel: RegexResult,
    program: RegexResult,
    crontab: RegexResult,
):
    """
    <channel keyword> 和 <program keyword> 不一定要输入全名，作为关键字进行匹配
    获取频道信息需要指定所在地区(network参数)，频道所在地区是固定的，因此可以提前建一个cache保存频道所在的地区信息。
    匹配频道时先找本地cache，没有的话调直接遍历所有network，再进行查找。
    如果频道出现多个匹配则报错，提示用户匹配到的所有频道名。
    如果<program keyword>有匹配，则提示用户节目全名，否则提示没有匹配到，但均视为添加成功
    成功后把这条记录加入一个配置文件（记录Channel对象和节目名）
    并且开始执行定时任务（触发定时任务时从该频道搜索是否有匹配的节目，如果有则提示用户是否预约（见后文））
    """
    channel = str(channel.result)
    program = str(program.result)
    crontab = str(crontab.result or "0 10 * * *")
    await app.send_message(
        sender, f"channel={channel}\nprogram={program}\ncrontab={crontab}"
    )
