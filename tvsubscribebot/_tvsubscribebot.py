import re
from pathlib import Path
from typing import Dict, Optional, List, TypeVar, Coroutine
import inspect

import httpx
import pytz
from loguru import logger

from dumb_bot.dumbbot import DumbApplication, Update, Chat, StringArgConverter, ChainCommandHandler
from dumb_bot.dumbbot.ext import MessageHandler, ConversationHandler, ContextTypes, filters, ApplicationBuilder, PicklePersistence, PersistenceInput
from dumbbot import DumbBot
from tvsubscriber import ApiException
from tvsubscriber import NETWORK_NAMES, NETWORKS
from tvsubscriber import TVSubscriber, Channel, Event, Reservation

from .utils.cache import CacheManager
from .utils.casts import *
from .utils.consts import CACHE_DB, PERSISTENCE, PERSISTENCE_UPDATE_INTERVAL
from .utils.errors import BadResultException
from .utils.widthConv import convertline

RESULT_TEXT = str
EVENT_ID = TypeVar('EVENT_ID', bound=int)

__all__ = (
    'TVSubscribeBot',
)

class TVSubscribeBot:
    """This class have to run in main thread."""
    _END = ConversationHandler.END
    _NOW_MATCHED = 1  # 输出单次检查结果，询问用户订阅哪些，回复订阅结果

    def __init__(
        self,
        persistence_filepath: Path = PERSISTENCE,
        update_interval: float = PERSISTENCE_UPDATE_INTERVAL,
        timezone: pytz.tzinfo = pytz.timezone('Asia/Shanghai'),
        dbfile: Path = CACHE_DB
    ):
        # see https://github.com/python-telegram-bot/python-telegram-bot/wiki/Making-your-bot-persistent
        # TODO 注意timezone对于定时任务的影响
        self.timezone = timezone

        self.persistence = PicklePersistence(
            persistence_filepath,
            store_data=PersistenceInput(callback_data=False),
            update_interval=update_interval,
            single_file=False,
        )
        self._app: DumbApplication = ApplicationBuilder()\
            .application_class(DumbApplication)\
            .bot(DumbBot())\
            .persistence(self.persistence)\
            .post_init(self._initialize)\
            .build()

        # persistent bot data (loaded at self._initialize)

        # TODO store/load schedule tasks into bot_data
        # TODO serialize app's jobqueue need APScheduler's logic: https://github.com/python-telegram-bot/ptbcontrib/tree/main/ptbcontrib/ptb_sqlalchemy_jobstore

        # cache
        self._cache = CacheManager(dbfile)
        # TODO 所有频道/节目查找全去找cache，同时缓存epgtoken
        #  基于jobqueue, 定时刷新cache（可以尝试设置expire）
        #  channel cache 每天刷新 epgtoken, 如果epgtoken不能用则手动刷新
        #  event cache 每小时刷新一次，同时要清理已经播完的节目（保留正在播的）
        #  subscribed cache，通过本机器人订阅的手动加入cache，另外定期拉取userinfo更新

        # define handlers
        cmd_handlers_simple = ChainCommandHandler(
            '/sub',
            sub_command_handlers=[
                ChainCommandHandler('help', self._help),
                ChainCommandHandler('login', self._login),
                ChainCommandHandler('search', self._search),
                # ChainCommandHandler('list', self.task_list),
                # ChainCommandHandler('edit', self.task_edit),
                # ChainCommandHandler('disable', self.task_disable),
                # ChainCommandHandler('enable', self.task_enable),
                # ChainCommandHandler('remove', self.task_remove),
                # ChainCommandHandler('check', self.task_check),
                # ChainCommandHandler('refresh_cache', self.refresh_cache)
            ]
        )

        # /sub now
        conv_sub_now = ConversationHandler(
            entry_points=[
                ChainCommandHandler(
                    '/sub',
                    sub_command_handlers=[
                        ChainCommandHandler('now', self._now)
                    ]
                )
            ],
            states={
                self._NOW_MATCHED: [
                    MessageHandler(filters.Regex(r'^[\d,]+$'), self._subscribe)
                ],
            },
            fallbacks=[
                ChainCommandHandler(
                    '/sub',
                    sub_command_handlers=[
                        ChainCommandHandler('cancel', self._cancel_subscribe)
                    ]
                ),
                MessageHandler(filters.ALL, self._resend_input_ids_prompt),
            ],
            persistent=True,
            name='conv_sub_now',
        )
        # TODO /sub daily
        # TODO use chat_data to persistent scheduled tasks (call _app.mark_data_for_update_persistence)
        #     see https://github.com/python-telegram-bot/python-telegram-bot/wiki/Storing-bot%2C-user-and-chat-related-data

        # TODO interactive subscription

        # set backup handler
        backup_handler = ChainCommandHandler('/sub', self._help)
        # register handlers
        self._app.add_handler(cmd_handlers_simple)
        self._app.add_handler(conv_sub_now)
        self._app.add_handler(backup_handler)

        self._callbacks: List[Callable[[RESULT_TEXT, Chat], Coroutine]] = []

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
            '_userinfo': StringArgConverter('/sub userinfo - 查看当前账户信息'),
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

    async def _initialize(self, application: DumbApplication):
        # load persisted bot data
        print(application.chat_data)
        print(application.user_data)
        ...

    # public utils
    def listen_forever(self, listen: str = "127.0.0.1", port: int = 18888):
        """Start server"""
        logger.info('listening at {}:{}', listen, port)
        self._app.run(listen, port)

    def register_callback(self, func: Callable[[RESULT_TEXT, Chat], Coroutine]) -> Callable[[RESULT_TEXT, Chat], Coroutine]:
        """Register coroutine callback for handling result text, can be used as a decorator."""
        self._callbacks.append(func)
        return func

    # handlers
    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        usages = [usage.text for usage in self._usages.values() if usage.text.startswith('/sub')]
        await self._notify_handle_result('\n'.join(usages), update)

    async def _login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub login <username> <password>"""
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._notify_handle_result(usage.usage, update)
            return

        username, password = context.args
        subscriber = TVSubscriber()
        try:
            res = subscriber.login(username, password)
        except (ApiException, httpx.ConnectError) as e:
            await self._notify_handle_result(str(e), update)
            return
        context.user_data['subscriber'] = subscriber
        context.application.mark_data_for_update_persistence(update.effective_user.id)
        await self._notify_handle_result(res['information'], update)

    async def _search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub search <channel> <program> [excludeProgram] [detail] [startDate] [startTime] [findFirstMatch]"""
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._notify_handle_result(usage.usage, update)
            return

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._notify_handle_result('请先登录！', update)
            return

        try:
            channel, program, \
            excludeProgram, detail, startDate, startTime, findFirstMatch = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._notify_handle_result('参数错误！\n' + str(e), update)
            return

        try:
            channels = self._find_channels(subscriber, channel)
        except (ApiException, httpx.ConnectError) as e:
            await self._notify_handle_result(str(e), update)
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
            await self._notify_handle_result(str(e), update)
            return

        if len(events) == 0:
            await self._notify_handle_result('没有找到匹配的节目！', update)
            return

        await self._notify_handle_result(self._make_matched_prompt(events), update)

    async def _now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/sub now <channel> <program> [excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 执行单次订阅任务"""
        # if not match, return END (nomatch), else return NOW_MATCHED
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._notify_handle_result(usage.usage, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._notify_handle_result('请先登录！', update)
            return self._END

        try:
            channel, program, \
            excludeProgram, detail, startDate, startTime, findFirstMatch = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._notify_handle_result('参数错误！\n' + str(e), update)
            return self._END

        try:
            channels = self._find_channels(subscriber, channel)
        except (ApiException, httpx.ConnectError) as e:
            await self._notify_handle_result(str(e), update)
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
            await self._notify_handle_result(str(e), update)
            return self._END

        # if len(events) < 5:
        #     logger.info('total matched events\n' + '\n'.join(str(ev) for ev in events))
        # else:
        logger.info('total matched events found {}', len(events))

        if len(events) == 0:
            await self._notify_handle_result('没有找到匹配的节目！', update)
            return self._END
        _last_matched_events: Dict[EVENT_ID, Event] = context.chat_data.setdefault('_last_matched_events', {})
        for ind, event in enumerate(events):
            _last_matched_events[ind + 1] = event

        # TODO 标记所有已订阅的节目
        await self._notify_handle_result(self._make_matched_prompt(events) + '\n\n' + self._input_ids_prompt(), update)
        return self._NOW_MATCHED

    async def _watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        args:
            channel_keyword: str,
            program: str,
            contab='0 10 * * *'
        """
        # TODO check is logged in before every op
        ...

    # conversation handlers
    async def _subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """用户回复要订阅哪些。可由单次查询/定时任务触发/手动触发等方式调用。
        参数为int或 int separated by ','
        """
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        args = [match.group() for match in context.matches]
        if not usage.check_arg_len(args):
            await self._notify_handle_result(usage.usage, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._notify_handle_result('请先登录！', update)
            return self._END

        _last_matched_events: Dict[EVENT_ID, Optional[Event]] = context.chat_data.get('_last_matched_events')
        if not _last_matched_events:
            await self._notify_handle_result('无法获取本次搜索结果！', update)
            return self._END

        try:
            (ids,) = usage.parse_args(args)
            assert all(0 <= id <= len(_last_matched_events) for id in ids)
        except (AssertionError, SyntaxError, TypeError, ValueError) as e:
            await self._notify_handle_result('序号错误，请重新输入！\n' + str(e), update)
            return
        if any(id == 0 for id in ids):
            ids = [0]
        else:
            # 去重+排序
            ids = sorted(set(ids))

        should_sub_all = ids[0] == 0
        reservations = []
        successed_ids = []
        failed_ids = []
        errors = []
        if should_sub_all:
            avail_events = _last_matched_events
        else:
            # 根据用户输入序号筛选
            avail_events = {id: _last_matched_events[id] for id in ids}

        for id, event in avail_events.items():
            if event is None:
                # this is a previously successfully subscribed event
                continue
            try:
                reservation = self._do_subscribe(subscriber, event)
                reservations.append(reservation)
                successed_ids.append(id)
            except (ApiException, httpx.ConnectError) as e:
                failed_ids.append(id)
                errors.append(str(e))

        try:
            userinfo = subscriber.get_userinfo()
            result_text = '余额：' + userinfo.wallet + '元\n'
        except (ApiException, httpx.ConnectError):
            result_text = '余额获取失败\n'

        if successed_ids:
            result_text += '预约成功：' + ', '.join(map(str, successed_ids)) + '\n'
            # remove event if success
            for id in successed_ids:
                _last_matched_events[id] = None

        if failed_ids:
            # 如果有失败，可再次输入需要重新预约的序号
            result_text += '预约失败：' + ', '.join(f'{id}（{e}）' for id, e in zip(failed_ids, errors)) + '\n' + self._input_ids_prompt() + '\n（成功的节目将被跳过。）'

        await self._notify_handle_result(result_text, update)

        if failed_ids:
            # stay in current state if any failed
            return
        # clear data if all success
        context.chat_data.pop('_last_matched_events')
        return self._END

    # conversation state commands
    async def _cancel_interactive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ...

    async def _cancel_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        # 退出会话、删除上次的匹配结果、
        context.chat_data.pop('_last_matched_events')
        await self._notify_handle_result('已终止', update)
        return self._END

    async def _resend_input_ids_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._notify_handle_result('输入错误！' + self._input_ids_prompt(), update)
        return self._NOW_MATCHED

    # private utils
    @staticmethod
    def _do_subscribe(subscriber: TVSubscriber, program: Event) -> Reservation:
        return subscriber.subscribe(program.sid, program.eid, program.tsid, program.onid, program.price, program.network, program.reservetoken)

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

    def _refresh_channel_cache(self, subscriber: TVSubscriber):
        self._cache.refresh_table()
        channels = []
        for network in NETWORK_NAMES.keys():
            channels.extend(subscriber.get_channels(network))
        self._cache.insert_channels(channels)

    async def _notify_handle_result(self, text: RESULT_TEXT, update: Update):
        for callback in self._callbacks:
            await callback(text, update.effective_chat)

    def _show_sub_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        ...
        return self._END

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
