import re
from typing import Dict, Optional, Iterable, List, Set
import inspect
import pytz
from loguru import logger

from command_handler import Application, Update, Chat, User, CommandHandler, MessageHandler, \
    ConversationHandler, ContextTypes, filters, Message, StringArgConverter
from tvsubscriber import ApiException
from tvsubscriber import NETWORK_NAMES, NETWORKS
from tvsubscriber import TVSubscriber, Channel, Event, Reservation

from utils.cache import CacheManager
from utils.casts import *
from utils.errors import BadResultException
from utils.widthConv import convertline

DEFAULT_USER = User(-1)
DEFAULT_CHAT = Chat(-1)

__all__ = (
    'TVSubscribeBot',
)


class TVSubscribeBot:
    _END = ConversationHandler.END
    _NOW_MATCHED = 1  # 输出单次检查结果，询问用户订阅哪些，回复订阅结果

    def __init__(self, timezone=pytz.timezone('Asia/Shanghai')):
        # TODO 保存定时任务记录
        self.timezone = timezone

        self._app = Application(self.timezone)
        self._cache = CacheManager()
        # TODO 所有频道/节目查找全去找cache，同时缓存epgtoken
        #  开启额外线程定时刷新cache（可以尝试设置expire）
        #  channel cache每天刷新epgtoken
        #  event cache 每小时刷新一次，同时要清理已经播完的节目（保留正在播的）
        #  可以基于jobqueue

        # define handlers
        cmd_handlers_simple = CommandHandler(
            '/sub',
            sub_command_handlers=[
                CommandHandler('help', self._help),
                CommandHandler('login', self._login),
                CommandHandler('search', self._search),
                # CommandHandler('list', self.task_list),
                # CommandHandler('edit', self.task_edit),
                # CommandHandler('disable', self.task_disable),
                # CommandHandler('enable', self.task_enable),
                # CommandHandler('remove', self.task_remove),
                # CommandHandler('check', self.task_check),
                # CommandHandler('refresh_cache', self.refresh_cache)
            ]
        )

        # /sub now
        conv_sub_now = ConversationHandler(
            entry_points=[
                CommandHandler(
                    '/sub',
                    sub_command_handlers=[
                        CommandHandler('now', self._now)
                    ]
                )
            ],
            states={
                self._NOW_MATCHED: [
                    MessageHandler(filters.Regex(r'^[\d,]+$'), self._subscribe)
                ],
            },
            fallbacks=[
                CommandHandler(
                    '/sub',
                    sub_command_handlers=[
                        CommandHandler('cancel', self._cancel_subscribe)
                    ]
                ),
                MessageHandler(filters.ALL, self._resend_input_ids_prompt),
            ]
        )

        # /sub daily
        # TODO

        # interactive subscription
        # TODO

        # set backup handler
        backup_handler = CommandHandler('/sub', self._help)
        # register handlers
        self._app.add_handler(cmd_handlers_simple)
        self._app.add_handler(conv_sub_now)
        self._app.add_handler(backup_handler)

        self._ids = {
            'update': 0,
            'message': 0,
        }

        self._last_matched_events: Dict[Chat, List[Event]] = {}
        self._last_reservations: Dict[Chat, List[Reservation]] = {}
        self._last_successed_ids: Dict[Chat, List[int]] = {}
        self._last_failed_ids: Dict[Chat, List[int]] = {}

        self._ret_msg: Optional[str] = None  # msg to be returned

        self._subscribers: Dict[Chat, TVSubscriber] = {}

        # TODO consider wrap dynamic model from pydantic
        #  https://docs.pydantic.dev/latest/usage/models/#dynamic-model-creation
        self._usages = {
            '_help': StringArgConverter('/sub help - 显示此帮助'),
            '_login': StringArgConverter(
                '/sub login <username> <password> - 登陆',
                username=(str,),
                password=(str,)
            ),
            '_search': StringArgConverter(
                '/sub search <channel> <program> '
                '[excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 搜索节目',
                channel=(str,),
                program=(str,),
                excludeProgram=(str, None),
                detail=(str, '*'),
                startDate=(datetime.date, None, cast_date),
                startTime=(datetime.time, None, cast_time),
                findFirstMatch=(bool, False, cast_bool_builder(False)),
            ),
            '_now': StringArgConverter(
                '/sub now <channel> <program> '
                '[excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 执行单次订阅任务',
                channel=(str,),
                program=(str,),
                excludeProgram=(str, None),
                detail=(str, '*'),
                startDate=(datetime.date, None, cast_date),
                startTime=(datetime.time, None, cast_time),
                findFirstMatch=(bool, False, cast_bool_builder(False)),
            ),
            '_daily': StringArgConverter(
                '/sub daily <channel> <program> '
                '[excludeProgram] [detail] [checkTime] [days] [startTime] - 添加每日定时检查任务',
                channel=(str,),
                program=(str,),
                excludeProgram=(str, None),
                detail=(str, '*'),
                checkTime=(datetime.time, lambda: datetime.datetime.now(self.timezone).time(), cast_time),
                days=(tuple[int],
                      tuple(range(0, 6+1)),
                      cast_ints),
                startTime=(datetime.time, None, cast_time),
            ),
            '_list': StringArgConverter('/sub list - 查看已添加的定时任务'),
            '_check': StringArgConverter(
                '/sub check <ids> - 手动触发定时任务',
                id=(list[int], cast_ints)
            ),
            '_edit': StringArgConverter(
                '/sub edit <id> [channel] [program] '
                '[excludeProgram] [detail] [checkTime] [days] [startTime] - 修改单个定时任务',
                id=(int, ),
                channel=(str, None),
                program=(str, None),
                excludeProgram=(str, None),
                detail=(str, '*'),
                checkTime=(datetime.time, lambda: datetime.datetime.now(self.timezone).time(), cast_time),
                days=(tuple[int],
                      tuple(range(0, 6 + 1)),
                      cast_ints),
                startTime=(datetime.time, None, cast_time),
            ),
            '_disable': StringArgConverter(
                '/sub disable <ids> - 禁用一个定时任务',
                id=(list[int], cast_ints)
            ),
            '_enable': StringArgConverter(
                '/sub enable <ids> - 恢复一个定时任务',
                id=(list[int], cast_ints)
            ),
            '_remove': StringArgConverter(
                '/sub remove <ids> - 删除一个定时任务',
                id=(list[int], cast_ints)
            ),
            '_start': StringArgConverter('/sub start - 交互式添加定时任务'),
            '_subscribe': StringArgConverter(
                '输入id，为单个数字，或多个用","隔开的数字',
                ids=(list[int], cast_ints)
            )
        }

    # public methods
    def handle_msg(
        self,
        msg: str,
        date: datetime.datetime = None,
        chat: Chat = DEFAULT_CHAT,
        from_user: User = DEFAULT_USER,
    ) -> Optional[str]:
        """处理输入字符串，返回响应字符串。None表示无格式匹配"""
        # TODO 作为子线程运行，在查找期间如果有新的指令匹配则提示请等待上次运行结束，并添加强行终止线程指令
        date = date or datetime.datetime.now(self.timezone)
        self._ret_msg = None
        self._app.process_update(Update(
            self._next_update_id,
            Message(
                self._next_message_id,
                date=date,
                chat=chat,
                from_user=from_user,
                text=msg
            )
        ))
        # 空字符串表示不匹配任何指令格式，无操作
        return self._ret_msg

    def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._set_retmsg(self._help_msg())

    # commands
    def _login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub login <username> <password>"""
        if len(context.args) != 2:
            self._set_retmsg('StringArgConverter:\n' + self._usages['login'])
            return

        username, password = context.args
        subscriber = TVSubscriber()
        try:
            res = subscriber.login(username, password)
        except ApiException as e:
            self._set_retmsg(str(e))
            return
        curr_chat = update.message.chat
        self._subscribers[curr_chat] = subscriber
        self._set_retmsg(res['information'])

    def _search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub search <channel> <program> [excludeProgram] [detail] [startDate] [startTime] [findFirstMatch]"""
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            self._set_retmsg(usage.usage)
            return

        subscriber = self._subscribers.get(update.message.chat)
        if not subscriber or not subscriber.is_online():
            self._set_retmsg('请先登录！')
            return

        try:
            channel, program, \
            excludeProgram, detail, startDate, startTime, findFirstMatch = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            self._set_retmsg('参数错误！\n' + str(e))
            return

        try:
            channels = self._find_channels(subscriber, channel)
        except ApiException as e:
            self._set_retmsg(str(e))
            return

        if len(channels) < 5:
            logger.info('matched channels\n' + '\n'.join(str(ch) for ch in channels))
        else:
            logger.info('matched channels found {}', len(channels))

        if excludeProgram == '':
            excludeProgram = None

        events = []
        try:
            for channel in channels:
                logger.info('searching ' + channel.service)
                matches = self._find_programs(
                    subscriber,
                    channel,
                    program,
                    excludeProgram,
                    detail,
                    startDate,
                    startTime,
                    findFirstMatch
                )
                logger.info('matched events found {}', len(matches))
                events.extend(matches)
                if len(matches) > 0 and findFirstMatch:
                    break
        except (ApiException, BadResultException) as e:
            self._set_retmsg(str(e))
            return

        if len(events) == 0:
            self._set_retmsg('没有找到匹配的节目！')
            return

        self._set_retmsg(self._make_matched_prompt(events))

    def _now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/sub now <channel> <program> [excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 执行单次订阅任务"""
        # if not match, return END (nomatch), else return NOW_MATCHED
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            self._set_retmsg(usage.usage)
            return self._END

        subscriber = self._subscribers.get(update.message.chat)
        if not subscriber or not subscriber.is_online():
            self._set_retmsg('请先登录！')
            return self._END

        try:
            channel, program, \
            excludeProgram, detail, startDate, startTime, findFirstMatch = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            self._set_retmsg('参数错误！\n' + str(e))
            return self._END

        try:
            channels = self._find_channels(subscriber, channel)
        except ApiException as e:
            self._set_retmsg(str(e))
            return self._END

        if len(channels) < 5:
            logger.info('matched channels\n' + '\n'.join(str(ch) for ch in channels))
        else:
            logger.info('matched channels found {}', len(channels))

        if excludeProgram == '':
            excludeProgram = None

        events = []
        try:
            for channel in channels:
                logger.info('searching ' + channel.service)
                matches = self._find_programs(subscriber, channel, program,
                                              excludeProgram, detail, startDate, startTime, findFirstMatch)
                logger.info('matched events found {}', len(matches))

                events.extend(matches)
                if len(matches) > 0 and findFirstMatch:
                    break
        except (ApiException, BadResultException) as e:
            self._set_retmsg(str(e))
            return self._END

        # if len(events) < 5:
        #     logger.info('total matched events\n' + '\n'.join(str(ev) for ev in events))
        # else:
        logger.info('total matched events found {}', len(events))

        if len(events) == 0:
            self._set_retmsg('没有找到匹配的节目！')
            return self._END
        # TODO 过滤掉已订阅的节目，也存个cache比较好，定期更新
        self._set_retmsg(self._make_matched_prompt(events) + '\n\n' + self._input_ids_prompt())
        self._last_matched_events[update.message.chat] = events
        return self._NOW_MATCHED

    def _watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        args:
            channel_keyword: str,
            program: str,
            contab='0 10 * * *'
        """
        # TODO check is logged in before every op
        ...

    # conversation states
    def _subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """用户回复要订阅哪些。可由单次查询/定时任务触发/手动触发等方式调用。
        参数为int或 int separated by ','
        """
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        args = [match.group() for match in context.matches]
        if not usage.check_arg_len(args):
            self._set_retmsg(usage.usage)
            return self._END

        subscriber = self._subscribers.get(update.message.chat)
        if not subscriber or not subscriber.is_online():
            self._set_retmsg('请先登录！')
            return self._END

        events = self._last_matched_events.get(update.message.chat)
        if not events:
            self._set_retmsg('无法获取本次搜索结果！')
            return self._END

        try:
            (ids,) = usage.parse_args(args)
            assert all(0 <= id <= len(events) for id in ids)
        except (AssertionError, SyntaxError, TypeError, ValueError) as e:
            self._set_retmsg('序号错误，请重新输入！\n' + str(e))
            return
        if any(id == 0 for id in ids):
            ids = [0]
        else:
            # 去重+排序
            ids = sorted(set(ids))

        # dict[id, event]
        avail_event_dict = {i + 1: event for i, event in enumerate(events)}

        # last_reservations = self._last_reservations.get(update.message.chat)
        # last_successed_ids = self._last_successed_ids.get(update.message.chat)
        last_failed_ids = self._last_failed_ids.get(update.message.chat)
        if last_failed_ids:
            avail_event_dict = {id: avail_event_dict[id] for id in last_failed_ids}

        is_sub_all = ids[0] == 0
        reservations = []
        successed_ids = []
        failed_ids = []
        errors = []
        # 注意序号的换算
        if not is_sub_all:
            # 根据用户输入序号筛选
            avail_event_dict = {id: avail_event_dict[id] for id in ids}

        for id, event in avail_event_dict.items():
            try:
                res = self._do_subscribe(subscriber, event)
                reservations.append(res)
                successed_ids.append(id)
            except ApiException as e:
                failed_ids.append(id)
                errors.append(str(e))

        # 记录已订阅成功的id，重试时跳过，注意处理0.
        self._last_reservations[update.message.chat] = reservations
        self._last_successed_ids[update.message.chat] = successed_ids
        self._last_failed_ids[update.message.chat] = failed_ids

        try:
            userinfo = subscriber.get_userinfo()
            self._ret_msg = '余额：' + userinfo.wallet + '元\n'
        except ApiException:
            self._ret_msg = '余额获取失败\n'

        if successed_ids:
            self._ret_msg += '预约成功：' + ', '.join(map(str, successed_ids)) + '\n'

        if failed_ids:
            # 如果有失败，可再次输入需要重新预约的序号
            self._ret_msg += '预约失败：' + ', '.join(f'{id}（{e}）' for id, e in zip(failed_ids, errors)) + '\n' + self._input_ids_prompt() + '\n（成功的节目将被跳过。）'
            return

        return self._END

    # private methods
    def _cancel_interactive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ...

    def _cancel_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        # 退出会话、删除上次的匹配结果、
        del self._last_matched_events[update.message.chat]
        return self._END

    def _find_channels(self, subscriber: TVSubscriber, keyword: str) -> list[Channel]:
        """
        Only look up cache, need to manually refresh if result is not desired.

        注意catch ApiException
        """
        # / around keyword (after stripping quotations) means word boundary
        keyword = keyword.replace('*', '%').replace('?', '_')
        matches = self._cache.find_channels(keyword)

        if len(matches) == 0:
            # cache miss
            logger.info('cache miss! Please manually refresh if needed.')
            # channel keyword not exist
            # return []
            # TODO change to cache only.
            self._refresh_channel_cache(subscriber)
            matches = self._cache.find_channels(keyword)
        else:
            logger.info('cache hit!')

        # found matched channels, update their epgtoken
        logger.info('updating epgtoken')
        self._update_epgtoken(subscriber, matches)
        return matches

    def _find_programs(
        self,
        subscriber: TVSubscriber,
        channel: Channel,
        program: str,
        excludeProgram: Optional[str],
        detail: str,
        startDate: Optional[datetime.date],
        startTime: Optional[datetime.time],
        findFirstMatch: bool,
    ) -> list[Event]:
        """
        注意catch ApiException
        """
        # TODO this is time consuming, change to cache.
        #  if cache, no BadResultException is need.
        events = subscriber.get_epgs(channel.sid, channel.network, channel.epgtoken, channel.tsid, timeout=None)
        try:
            assert len(events) > 0, f'{channel.service}的节目列表为空！'
        except AssertionError as e:
            raise BadResultException(e)

        # search strings
        pat_program = self._make_pattern(convertline(program))
        pat_exclude_program = self._make_pattern(convertline(excludeProgram)) if isinstance(excludeProgram, str) else None
        pat_detail = self._make_pattern(convertline(detail))

        matches = []
        for i, event in enumerate(events):
            if (
                pat_program.search(convertline(event.event_name)) and
                pat_detail.search(convertline(event.event_text + '\n' + event.event_ext_text)) and
                (pat_exclude_program is None or not pat_exclude_program.search(convertline(event.event_name))) and
                (not isinstance(startDate, datetime.date) or startDate == event.startdate) and
                (not isinstance(startTime, datetime.time) or startTime == event.starttime)
            ):
                matches.append(event)
                if findFirstMatch:
                    break
        return matches

    def _help_msg(self):
        msg = ['Usage:']
        for key, usage in self._usages.items():
            msg.append(usage.text)
        return '\n'.join(msg)

    @staticmethod
    def _input_ids_prompt() -> str:
        return "请输入需要订阅的序号，0表示全选，多个序号必须用英文逗号','隔开。\n输入/sub cancel终止订阅。"

    @staticmethod
    def _make_matched_prompt(events: List[Event]) -> str:
        """提示匹配到的节目，并询问用户订阅哪些。
        :param ask_input: 是否询问用户需要订阅哪些。
        """
        msgs = [['共找到' + str(len(events)) + '个结果']]
        for i, event in enumerate(events):
            msg = [
                '序号：' + str(i+1),
                '频道：' + event.service,
                '所属网络：' + NETWORK_NAMES[event.network],
                '播出时间：' + event.startdate.strftime(event.__FORMAT_STARTDATE__) + ' ' + event.starttime.strftime(event.__FORMAT_STARTTIME__) + f'（{event.week_text}）',
                '节目：' + event.event_name,
                '节目说明：' + event.event_text,
                '时长：' + str(event.duration) + '分钟',
                '价格：' + str(event.price) + '元',
                '分辨率：' + event.resolution,
            ]
            msgs.append(msg)

        return '\n\n'.join('\n'.join(msg) for msg in msgs)

    @staticmethod
    def _make_pattern(keyword: str) -> re.Pattern:
        """把'*代表任意字符，?代表单个字符'的匹配规则替换为正则表达式"""
        keyword = re.escape(keyword)
        keyword = keyword.replace(r'\*', '.*').replace(r'\?', '.')
        return re.compile(keyword)

    @property
    def _next_message_id(self):
        self._ids['message'] += 1
        return self._ids['message']

    @property
    def _next_update_id(self):
        self._ids['update'] += 1
        return self._ids['update']

    def _refresh_channel_cache(self, subscriber: TVSubscriber):
        # TODO automatic update channel cache every week (channels shouldn't be changing to much)
        self._cache.refresh_table()
        channels = []
        for network in NETWORK_NAMES.keys():
            channels.extend(subscriber.get_channels(network))
        self._cache.insert_channels(channels)

    def _resend_input_ids_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._set_retmsg('输入错误！' + self._input_ids_prompt())
        return self._NOW_MATCHED

    def _set_retmsg(self, msg:str):
        self._ret_msg = msg

    def _show_sub_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        ...
        return self._END

    @staticmethod
    def _do_subscribe(subscriber: TVSubscriber, program: Event) -> Reservation:
        return subscriber.subscribe(program.sid, program.eid, program.tsid, program.onid, program.price, program.network, program.reservetoken)

    @staticmethod
    def _update_epgtoken(subscriber: TVSubscriber, target_channels: List[Channel]):
        targets: Dict[NETWORKS, List[Channel]] = {}
        for target_channel in target_channels:
            # note: 不能用频道名作为索引，因为频道名不唯一
            # 按network把target_channel分组
            targets.setdefault(target_channel.network, []).append(target_channel)

        for network, target in targets.items():
            seen = []
            target_len = len(target)
            for channel in subscriber.get_channels(network):
                # 比较方式是除了epgtoken外其他都相同
                try:
                    target_channel = target.pop(target.index(channel))
                except ValueError:
                    continue
                target_channel.epgtoken = channel.epgtoken
                seen.append(target_channel)
                if len(seen) == target_len:
                    break



