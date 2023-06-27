import inspect
from pathlib import Path
from typing import Coroutine, TYPE_CHECKING, Union, TypeVar
from typing import Dict, Optional, List
from uuid import uuid4

import httpx
import pymongo.errors
from loguru import logger
from dumbbot.ptbcontrib.ptbcontrib.ptb_jobstores import PTBMongoDBJobStore
from telegram.ext import Job

from dumb_bot.dumbbot import DumbApplication, DumbBot, Chat
from dumb_bot.dumbbot import Update, StringArgConverter, ChainCommandHandler
from dumb_bot.dumbbot.ext import ApplicationBuilder, PicklePersistence, PersistenceInput
from dumb_bot.dumbbot.ext import MessageHandler, ConversationHandler, ContextTypes, filters
from tv_subscriber.tvsubscriber import ApiException
from tv_subscriber.tvsubscriber import NETWORK_NAMES
from tv_subscriber.tvsubscriber import TVSubscriber, Event, Reservation
from ._jobsmanager import JobsManager
from ._jobsmapping import JobsMapping, JOB_NAME
from .utils.cache import CacheManager
from .utils.casts import *
from .utils.consts import DB_CACHE, PERSISTENCE_PKL, PERSISTENCE_UPDATE_INTERVAL, DB_JOBSTORE, KEY_SUBBED_EVENTS, \
    INT_UPDATE_SUBBED_EVENTS, JOB_UPDATE_SUBBED_EVENT_PREFIX
from .utils.consts import JOBKEY_THIS_UPDATE, JOBKEY_DAILY_JOB_ARGS
from .utils.consts import KEY_JOB_MAPPING, KEY_JOB_NAME_ONCE, KEY_LAST_DAILY_JOB_ARGS, KEY_UNHANDLED_MATCHES
from .utils.errors import BadResultException
from .utils.handlercallbacks import HandlerCallbacks
from .utils.searchutils import SearchUtils
from .utils.types import RESULT_TEXT, JOB_NAME_ONCE, EVENT_DICT

if TYPE_CHECKING:
    from dumb_bot.dumbbot.ext import Application

__all__ = (
    'TVSubscribeBot',
)

SUCCESSED_IDS = TypeVar('SUCCESSED_IDS', bound=list[int])
FAILED_IDS = TypeVar('FAILED_IDS', bound=list[int])
ALREADY_SUBBED_IDS = TypeVar('ALREADY_SUBBED_IDS', bound=list[int])


class TVSubscribeBot:
    """This class have to run in main thread."""
    _END = ConversationHandler.END
    _NOW_MATCHES = 1  # 输出单次检查结果，询问用户订阅哪些，回复订阅结果
    _DAILY_MACHES = 2  # 输出每日任务添加时的检查结果，询问用户是否添加任务
    _CHECK_DAILY_JOB_MACHES = 3  # 触发定时任务时的检查结果，询问用户是否添加任务

    def __init__(
        self,
        persistence_filepath: Path = PERSISTENCE_PKL,
        update_interval: float = PERSISTENCE_UPDATE_INTERVAL,
        dbfile_jobstore: str = DB_JOBSTORE,
        dbfile_cache: Path = DB_CACHE,
    ):
        # TODO 检查所有更新chat_data的地方，都要手动加上mark for update
        self.persistence = PicklePersistence(
            persistence_filepath,
            store_data=PersistenceInput(callback_data=False),
            update_interval=update_interval,
            single_file=False,
        )
        self._app: Union[DumbApplication, Application] = ApplicationBuilder() \
            .application_class(DumbApplication) \
            .bot(DumbBot()) \
            .persistence(self.persistence) \
            .post_init(self._initialize) \
            .build()

        # https://github.com/python-telegram-bot/ptbcontrib/blob/main/ptbcontrib/ptb_jobstores/README.md
        self._app.job_queue.scheduler.add_jobstore(
            PTBMongoDBJobStore(
                application=self._app,
                host=dbfile_jobstore,
            )
        )

        # cache
        self._cache = CacheManager(dbfile_cache)
        # TODO 优化：所有频道/节目查找全去找cache，同时缓存epgtoken
        #  基于jobqueue, 定时刷新cache（可以尝试设置expire）
        #  channel cache 每天刷新 epgtoken, 如果epgtoken不能用则手动刷新
        #  event cache 每小时刷新一次，同时要清理已经播完的节目（保留正在播的）

        # define handlers
        self.cmd_handlers_simple = ChainCommandHandler(
            '/sub',
            sub_command_handlers=[
                ChainCommandHandler('help', self._cmd_help),
                ChainCommandHandler('login', self._cmd_login),
                ChainCommandHandler('search', self._cmd_search),
                ChainCommandHandler('list', self._cmd_list),
                # ChainCommandHandler('edit', self.task_edit),
                # ChainCommandHandler('disable', self.task_disable),
                # ChainCommandHandler('enable', self.task_enable),
                ChainCommandHandler('remove', self._cmd_remove),
                ChainCommandHandler('userinfo', self._cmd_userinfo),
                # ChainCommandHandler('refresh_cache', self.refresh_cache)
            ]
        )

        # /sub now
        self.conv_sub_now = ConversationHandler(
            entry_points=[
                ChainCommandHandler('/sub', sub_command_handlers=[
                    ChainCommandHandler('now', self._cmd_now)
                ])
            ],
            states={
                self._NOW_MATCHES: [
                    MessageHandler(filters.Regex(r'^[\d,]+$'), self._callback_subscribe_now)
                ],
            },
            fallbacks=[
                MessageHandler(filters.Regex(r'^/sub cancel(?: (now))?$'), self._cancel_conversation),
                MessageHandler(~filters.Regex(r'^/sub cancel \d+$'), self._prompt_resend),
            ],
            persistent=True,
            name='conv_sub_now',
        )

        # /sub daily
        self.conv_sub_daily = ConversationHandler(
            entry_points=[
                ChainCommandHandler('/sub', sub_command_handlers=[
                        ChainCommandHandler('daily', self._cmd_daily)
                ])
            ],
            states={
                self._DAILY_MACHES: [
                    MessageHandler(filters.Regex(r'^[是否]$'), self._daily_job_confirmed)
                ]
            },
            fallbacks=[
                MessageHandler(filters.Regex(r'^/sub cancel(?: (daily))?$'), self._cancel_conversation),
                MessageHandler(~filters.Regex(r'^/sub cancel \d+$'), self._prompt_resend),
            ],
            persistent=True,
            name='conv_sub_daily',
        )

        # /sub check
        # 如果有未解决的任务，则不允许进入下一步。防止抢了其他定时job的handler
        self.conv_sub_check = ConversationHandler(
            entry_points=[
                ChainCommandHandler('/sub', sub_command_handlers=[
                    ChainCommandHandler('check', self._cmd_check)
                ])
            ],
            states={
                self._CHECK_DAILY_JOB_MACHES: [
                    MessageHandler(filters.Regex(fr'(^[\d,]+)(?: (\d+))?$'), self._callback_subscribe_daily_job)
                ]
            },
            fallbacks=[
                MessageHandler(filters.Regex(fr'^/sub cancel(?: (\d+))?$'), self._cancel_conversation),
                MessageHandler(filters.ALL, self._prompt_resend),
            ],
            persistent=True,
            name='conv_check',
        )

        # TODO interactive subscription

        # set backup handler
        self.backup_handler = ChainCommandHandler('/sub', self._cmd_help)

        self._app.add_handler(self.cmd_handlers_simple)
        self._app.add_handler(self.conv_sub_now)
        self._app.add_handler(self.conv_sub_daily)
        self._app.add_handler(self.conv_sub_check)
        self._app.add_handler(self.backup_handler)

        debug_handler = MessageHandler(filters.Regex('debug'), self._debug)
        self._app.add_handler(debug_handler)

        # store handlers created by job, for user removal
        self._conv_check_daily_jobs: dict[JOB_NAME, ConversationHandler] = {}

        self._callbacks: HandlerCallbacks = HandlerCallbacks()

        self._search_utils = SearchUtils(self._cache)

    # app 相关
    async def _initialize(self, application: 'Application'):
        # load persisted bot data
        print(application.chat_data)
        print(application.user_data)
        ...

    def listen_forever(self, listen: str = "127.0.0.1", port: int = 18888):
        """Start server"""
        logger.info('listening at {}:{}', listen, port)
        try:
            self._app.run(listen, port)
        except pymongo.errors.ServerSelectionTimeoutError:
            logger.error('无法连接到jobstore！')

    # public utils
    def register_callback(self, func: Callable[[RESULT_TEXT, Chat], Coroutine]) -> Callable[[RESULT_TEXT, Chat], Coroutine]:
        """Register coroutine callback for handling result text, can be used as a decorator."""
        return self._callbacks.register_callback(func)

    # handlers
    async def _debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        data={
             'sid': '1056',
             'tsid': '32740',
             'onid': '32740',
             'eid': '27472',
             'service': 'フジテレビ',
             'startdate': '2023/05/14',
             'starttime': '23:15:00',
             'timestamp': 1684077300,
             'week': '0',
             'week_text': '日',
             'duration': 30,
             'event_name': 'テレビアニメ「鬼滅の刃」刀鍛冶の里編[字][解][デ]',
             'event_text': '第六話『柱になるんじゃないのか！』',
             'event_ext_text': 'ご案内\n【公式ＨＰ】\nhttps://www.fujitv.co.jp/kimetsu\n番組内容\n＜前回のあらすじ＞\n４体に分裂した半天狗の猛攻に苦戦する炭治郎たち。しかし、禰豆子の血の力により燃えて赤くなった刀を振るい、炭治郎は３体の頸（くび）を同時に斬ることに成功する。玄弥が残りもう一体の鬼の頸を斬っていたことに気づく炭治郎だったが、鬼の頸を持つ玄弥は姿が変わっており……。\n\n第六話『柱になるんじゃないのか！』は５月１４日（日）２３時１５分放送！\n番組内容２\n遊郭での任務を終えた炭治郎たちの次なる物語を描く「刀鍛冶の里編」。炭治郎が向かう先は、刀鍛冶の里。鬼殺隊最強の剣士≪柱≫である、霞柱・時透無一郎と恋柱・甘露寺蜜璃との再会、忍びよる鬼の影。炭治郎たちの新たな戦いが始まる。\n出演者\n竈門炭治郎（かまど・たんじろう）：\u3000花江夏樹\u3000\n竈門禰豆子（かまど・ねずこ）※：\u3000鬼頭明里\u3000\n時透無一郎（ときとう・むいちろう）：\u3000河西健吾\u3000\n甘露寺蜜璃（かんろじ・みつり）：\u3000花澤香菜\u3000\n不死川玄弥（しなずがわ・げんや）：\u3000岡本信彦\u3000\n\n半天狗（はんてんぐ）：\u3000古川登志夫\u3000\n玉壺（ぎょっこ）：\u3000鳥海浩輔\u3000\n\n※禰豆子の「禰」は「ネ＋爾」が正しい表記。\nスタッフ\n【主題歌】\n＜オープニングテーマ＞\nＭＡＮ\u3000ＷＩＴＨ\u3000Ａ\u3000ＭＩＳＳＩＯＮ×ｍｉｌｅｔ\u3000『絆ノ奇跡』\u3000\n＜エンディングテーマ＞\nｍｉｌｅｔ×ＭＡＮ\u3000ＷＩＴＨ\u3000Ａ\u3000ＭＩＳＳＩＯＮ\u3000『コイコガレ』\u3000\n\n【原作】\n吾峠呼世晴（集英社ジャンプ\u3000コミックス刊）\u3000\n【監督】\n外崎春雄\u3000\n【キャラクターデザイン・総作画監督】\n松島晃\u3000\n【脚本制作】\nｕｆｏｔａｂｌｅ\nスタッフ２\n【サブキャラクターデザイン】\n佐藤美幸、梶山庸子、菊池美花\u3000\n【プロップデザイン】\n小山将治\u3000\n【美術監督】\n衛藤功二\u3000\n【撮影監督】\n寺尾優一\u3000\n【３Ｄ監督】\n西脇一樹\u3000\n【色彩設計】\n大前祐子\u3000\n【編集】\n神野学\u3000\n【音楽】\n梶浦由記、椎名豪\u3000\n【アニメーション制作】\nｕｆｏｔａｂｌｅ\u3000\n【製作】\nアニプレックス、集英社、ｕｆｏｔａｂｌｅ\n',
             'category': 'anime',
             'resolution': '1080i',
             'network': 'Kanto',
             'price': 3.5,
             'reservetoken': 'f9baeab748ee25d6420521c4f7b0242c'
         }
        event = Event(**data)
        subbed_events: list[Event] = context.user_data.setdefault(KEY_SUBBED_EVENTS, [])
        subbed_events.append(event)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        usages = [usage.text for usage in self._usages.values()]
        await self._callbacks.notify_handle_result('\n'.join(usages), update)

    async def _cmd_login(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub login <username> <password>"""
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._callbacks.notify_handle_result(usage.usage, update)
            return

        username, password = context.args
        subscriber = TVSubscriber()
        try:
            res = subscriber.login(username, password)
        except (ApiException, httpx.ConnectError) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return
        context.user_data['subscriber'] = subscriber
        context.application.mark_data_for_update_persistence(user_ids=update.effective_user.id)
        await self._callbacks.notify_handle_result(res['information'], update)

        # register jobs
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        context.job_queue.run_repeating(
            callback=JobsManager.job_update_subbed_events,
            interval=INT_UPDATE_SUBBED_EVENTS,
            name=JOB_UPDATE_SUBBED_EVENT_PREFIX + str(user_id),
            chat_id=chat_id,
            user_id=user_id,
            job_kwargs=dict(replace_existing=True, id=JOB_UPDATE_SUBBED_EVENT_PREFIX + str(user_id)),
        )

    async def _cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub search <channel> <program> [excludeProgram] [detail] [startDate] [startTime] [findFirstMatch]"""
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._callbacks.notify_handle_result(usage.usage, update)
            return

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return

        try:
            channel, program, \
            excludeProgram, detail, startDate, startTime, findFirstMatch = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result('参数错误！\n' + str(e), update)
            return

        try:
            channels = self._search_utils.find_channels(subscriber, channel)
        except (ApiException, httpx.ConnectError) as e:
            await self._callbacks.notify_handle_result(str(e), update)
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
                matches = self._search_utils.find_programs(
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
            await self._callbacks.notify_handle_result(str(e), update)
            return

        if len(events) == 0:
            await self._callbacks.notify_handle_result('没有找到匹配的节目！', update)
            return

        # 标记所有已订阅的节目，防止重复搜索
        subbed_events: list[Event] = context.user_data.get(KEY_SUBBED_EVENTS, [])
        # 已订阅的节目仍会显示，且有“已订阅”字样。
        await self._callbacks.notify_handle_result(
            self._prompt_matched_events(events, subbed_events),
            update)

    async def _cmd_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """/sub now <channel> <program> [excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 执行单次订阅任务"""
        # if not match, return END (nomatch), else return NOW_MATCHED
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._callbacks.notify_handle_result(usage.usage, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return self._END

        channelstr: str
        programstr: str
        exclude_program: Optional[str]
        detail: str
        start_date: Optional[datetime.date]
        start_time: Optional[datetime.time]
        find_first_match: bool
        try:
            channelstr, programstr, \
            exclude_program, detail, start_date, start_time, find_first_match = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result('参数错误！\n' + str(e), update)
            return self._END

        try:
            channels = self._search_utils.find_channels(subscriber, channelstr)
        except (ApiException, httpx.ConnectError) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return self._END

        if len(channels) < 5:
            logger.info('matched channels\n' + '\n'.join(str(ch) for ch in channels))
        else:
            logger.info('matched channels found {}', len(channels))

        if exclude_program == '':
            exclude_program = None

        events = []
        try:
            for channel in channels:
                logger.info('searching ' + channel.service)
                matches = self._search_utils.find_programs(
                    subscriber,
                    channel,
                    programstr,
                    exclude_program,
                    detail,
                    start_date,
                    start_time,
                    find_first_match
                )
                logger.info('matched events found {}', len(matches))
                events.extend(matches)
                if len(matches) > 0 and find_first_match:
                    break
        except (ApiException, BadResultException) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return self._END

        logger.info('total matched events found {}', len(events))

        # 标记所有已订阅的节目，防止重复搜索
        subbed_events: list[Event] = context.user_data.get(KEY_SUBBED_EVENTS, [])
        # 已订阅的节目不会显示
        events = list(filter(lambda ev: ev not in subbed_events, events))

        logger.info('total matched events after filter found {}', len(events))

        if len(events) == 0:
            await self._callbacks.notify_handle_result('没有找到匹配的节目！', update)
            return self._END

        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.setdefault(KEY_UNHANDLED_MATCHES, {})
        last_matched_events: EVENT_DICT = unhandled_matches.setdefault(KEY_JOB_NAME_ONCE, {})
        for i, event in enumerate(events):
            last_matched_events[i + 1] = event

        context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)

        await self._callbacks.notify_handle_result(
            self._prompt_matched_events(events) + '\n\n' +
                self._prompt_input_ids_now,
            update)
        return self._NOW_MATCHES

    async def _cmd_daily(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub daily <channel> <program> [excludeProgram] [detail] [checkTime] [days] [startTime] - 添加每日定时检查任务
        """
        # if not match, return END (nomatch), else return _DAILY_MATCHES
        # TODO add category?
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._callbacks.notify_handle_result(usage.usage, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return self._END

        channelstr: str
        programstr: str
        exclude_program: Optional[str]
        detail: str
        check_time: datetime.time
        days: tuple[int]
        start_time: Optional[datetime.time]
        try:
            channelstr, programstr, \
            exclude_program, detail, check_time, days, start_time = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result('参数错误！\n' + str(e), update)
            return self._END

        # first prompt result, add job after user respond
        try:
            channels = self._search_utils.find_channels(subscriber, channelstr)
        except (ApiException, httpx.ConnectError) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return self._END

        if len(channels) < 5:
            logger.info('matched channels\n' + '\n'.join(str(ch) for ch in channels))
        else:
            logger.info('matched channels found {}', len(channels))

        if exclude_program == '':
            exclude_program = None

        events = []
        try:
            for channel in channels:
                logger.info('searching ' + channel.service)
                matches = self._search_utils.find_programs(
                    subscriber,
                    channel,
                    programstr,
                    exclude_program,
                    detail,
                    start_date=None,
                    start_time=start_time,
                    find_first_match=False
                )
                logger.info('matched events found {}', len(matches))
                events.extend(matches)
        except (ApiException, BadResultException) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return self._END

        logger.info('total matched events found {}', len(events))

        context.chat_data[KEY_LAST_DAILY_JOB_ARGS] = dict(
            channelstr=channelstr,
            programstr=programstr,
            exclude_program=exclude_program,
            detail=detail,
            check_time=check_time,
            days=days,
            start_time=start_time,
        )

        context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)

        # 标记所有已订阅的节目，防止重复搜索
        subbed_events: list[Event] = context.user_data.get(KEY_SUBBED_EVENTS, [])
        # 已订阅的节目仍会显示，且有“已订阅”字样。
        await self._callbacks.notify_handle_result(
            '当前匹配结果：\n' +
                self._prompt_matched_events(events, subbed_events) +
                '\n\n' +
                self._prompt_job_confirm,
            update)
        return self._DAILY_MACHES

    async def _cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """定时触发/手动触发单个任务检查"""
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._callbacks.notify_handle_result(usage.text, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return self._END

        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.get(KEY_UNHANDLED_MATCHES, {})
        if len(unhandled_matches) > 0:
            await self._callbacks.notify_handle_result('请先处理未完成的订阅任务！', update)
            return self._END

        try:
            (jobid,) = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result(usage.text + '\n' + str(e), update)
            return self._END

        jobs_mapping: JobsMapping = context.chat_data.get(KEY_JOB_MAPPING, JobsMapping())
        if (inner_id := jobs_mapping.get_inner_id(jobid)) is None:
            await self._callbacks.notify_handle_result('找不到jobid' + str(jobid), update)
            return self._END

        job_name = inner_id
        try:
            # get args from job's data
            job: Job = context.job_queue.get_jobs_by_name(job_name)[0]
        except IndexError:
            await self._callbacks.notify_handle_result('找不到job_name' + job_name, update)
            return self._END

        job.data[JOBKEY_THIS_UPDATE] = update

        daily_job_args = job.data[JOBKEY_DAILY_JOB_ARGS]
        channelstr: str = daily_job_args['channelstr']
        programstr: str = daily_job_args['programstr']
        exclude_program: Optional[str] = daily_job_args['exclude_program']
        detail: str = daily_job_args['detail']
        start_time: Optional[datetime.time] = daily_job_args['start_time']

        # prompt result
        try:
            channels = self._search_utils.find_channels(subscriber, channelstr)
        except (ApiException, httpx.ConnectError) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return self._END

        if len(channels) < 5:
            logger.info('matched channels\n' + '\n'.join(str(ch) for ch in channels))
        else:
            logger.info('matched channels found {}', len(channels))

        if exclude_program == '':
            exclude_program = None

        events = []
        try:
            for channel in channels:
                logger.info('searching ' + channel.service)
                matches = self._search_utils.find_programs(
                    subscriber,
                    channel,
                    programstr,
                    exclude_program,
                    detail,
                    start_date=None,
                    start_time=start_time,
                    find_first_match=False
                )
                logger.info('matched events found {}', len(matches))

                events.extend(matches)
        except (ApiException, BadResultException) as e:
            await self._callbacks.notify_handle_result(str(e), update)
            return self._END

        logger.info('total matched events found {}', len(events))

        # 标记所有已订阅的节目，防止重复搜索
        subbed_events: list[Event] = context.user_data.get(KEY_SUBBED_EVENTS, [])
        # 已订阅的节目不会显示
        events = list(filter(lambda ev: ev not in subbed_events, events))

        logger.info('total matched events after filter found {}', len(events))

        if len(events) == 0:
            await self._callbacks.notify_handle_result('定时任务jobid: ' + str(jobid) + ' 没有找到匹配的节目！', update)
            return self._END

        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.setdefault(KEY_UNHANDLED_MATCHES, {})
        last_matched_events: EVENT_DICT = unhandled_matches.setdefault(inner_id, {})
        for i, event in enumerate(events):
            last_matched_events[i + 1] = event

        context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)

        await self._callbacks.notify_handle_result(
            'jobid: ' + str(jobid) +
                self._prompt_matched_events(events) + '\n\n' +
                self._prompt_input_ids_daily_job,
            update)
        return self._CHECK_DAILY_JOB_MACHES

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub list - 查看已添加的定时任务"""
        jobs_mapping: JobsMapping = context.chat_data.get(KEY_JOB_MAPPING, JobsMapping())
        if len(jobs_mapping) == 0:
            await self._callbacks.notify_handle_result('无定时任务', update)
            return

        msgs = []
        for jobid, job_name in jobs_mapping.items():
            try:
                job = context.job_queue.get_jobs_by_name(job_name)[0]
            except IndexError:
                continue
            jobmsg = self._prompt_job_info(
                jobid=jobid,
                enabled=True,
                **job.data[JOBKEY_DAILY_JOB_ARGS]
            )
            msgs.append(jobmsg)
        await self._callbacks.notify_handle_result('\n\n'.join(msgs), update)

    async def _cmd_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub edit <jobid> [channel] [program] '
            #     '[excludeProgram] [detail] [checkTime] [days] [startTime] - 修改单个定时任务"""
        ...

    async def _cmd_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub disable <jobids> - 禁用(多个)定时任务"""
        ...

    async def _cmd_enable(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub enable <jobids> - 恢复(多个)定时任务"""
        ...

    async def _cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub remove <jobids> - 删除(多个)定时任务"""
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages[currfunc]
        if not usage.check_arg_len(context.args):
            await self._callbacks.notify_handle_result(usage.text, update)
            return self._END

        # 避免删除正在执行的会话，先处理完所有定时任务
        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.get(KEY_UNHANDLED_MATCHES, {})
        if len(unhandled_matches) > 0:
            await self._callbacks.notify_handle_result('请先处理未完成的订阅任务！', update)
            return self._END

        try:
            jobids: list[int]
            (jobids,) = usage.parse_args(context.args)
        except (SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result(usage.text + '\n' + str(e), update)
            return self._END

        jobs_mapping: JobsMapping = context.chat_data.get(KEY_JOB_MAPPING, JobsMapping())
        retmsg = []
        for jobid in jobids:
            msg = 'jobid ' + str(jobid) + ': '
            if (inner_id := jobs_mapping.get_inner_id(jobid)) is None:
                msg += '删除失败，找不到jobid'
            else:
                job_name = inner_id
                jobs = context.job_queue.get_jobs_by_name(job_name)
                if len(jobs) == 0:
                    msg += '删除失败，job_queue中找不到job_name' + job_name
                else:
                    # remove job
                    try:
                        jobs[0].schedule_removal()
                        # remove handler
                        try:
                            handler = self._conv_check_daily_jobs.pop(job_name)
                            context.application.remove_handler(handler)
                        except KeyError:
                            pass
                        # remove from jobs mapping
                        jobs_mapping.remove_by_inner(job_name)
                        msg += '删除成功'
                    except Exception as e:
                        msg += '删除失败，' + str(e)
            retmsg.append(msg)

        retmsg = '\n'.join(retmsg)
        await self._callbacks.notify_handle_result(retmsg, update)

    async def _cmd_userinfo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/sub userinfo - 查看当前账户信息"""
        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return self._END

        userinfo = subscriber.get_userinfo()
        retmsgs = (
            '用户名：' + userinfo.username,
            '邮箱：' + userinfo.email,
            '余额：' + userinfo.wallet,
        )
        retmsg = '\n'.join(retmsgs)
        await self._callbacks.notify_handle_result(retmsg, update)

    # conversation handler callbacks
    async def _callback_subscribe_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """用户回复要订阅哪些。对应单次查询。
        参数为int或 int separated by ','
        """
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages_private[currfunc]
        args = [match.group() for match in context.matches]
        if not usage.check_arg_len(args):
            await self._callbacks.notify_handle_result(usage.text, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return self._END

        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.get(KEY_UNHANDLED_MATCHES, {})

        last_matched_events: EVENT_DICT = unhandled_matches.get(KEY_JOB_NAME_ONCE)
        if not last_matched_events:
            await self._callbacks.notify_handle_result('无法获取本次搜索结果！', update)
            return self._END

        try:
            (ids,) = usage.parse_args(args)
            assert all(0 <= id <= len(last_matched_events) for id in ids)
        except (AssertionError, SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result('序号错误，请重新输入！\n' + str(e), update)
            return

        successed_ids, failed_ids, already_subbed_ids, result_text = self._util_subscribe(ids, last_matched_events, subscriber)

        # 已预约过的视为预约成功
        successed_ids.extend(already_subbed_ids)

        if successed_ids:
            subbed_events: list[Event] = context.user_data.setdefault(KEY_SUBBED_EVENTS, [])
            subbed_events.extend(last_matched_events[id] for id in successed_ids)
            # mark event if success
            for id in successed_ids:
                last_matched_events[id] = None
            context.application.mark_data_for_update_persistence(
                chat_ids=update.effective_chat.id,
                user_ids=update.effective_user.id,
            )

        if failed_ids:
            # stay in current state if any failed
            return

        # clear data if all success (or cancled)
        unhandled_matches.pop(KEY_JOB_NAME_ONCE)

        await self._callbacks.notify_handle_result(result_text, update)

        return self._END

    async def _callback_subscribe_daily_job(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """用户回复要订阅哪些。对应定时任务触发/手动触发。
        参数为int或 int separated by ',' 和 jobid
        """
        # 如果只有一个触发任务，则直接输入订阅序号，否则需要输入jobid和序号。
        currfunc = inspect.currentframe().f_code.co_name
        usage = self._usages_private[currfunc]
        args = context.match.groups()  # only retrieve those in brackets
        if not usage.check_arg_len(args):
            await self._callbacks.notify_handle_result(usage.usage, update)
            return self._END

        subscriber: TVSubscriber = context.user_data.get('subscriber')
        if subscriber is None or not subscriber.is_online():
            await self._callbacks.notify_handle_result('请(重新)登录！', update)
            return self._END

        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.get(KEY_UNHANDLED_MATCHES, {})
        jobs_mapping: JobsMapping = context.chat_data.get(KEY_JOB_MAPPING, JobsMapping())
        try:
            # 检查未处理list是否只有一个，有多个则提示需要输入jobid。
            ids, outer_id = usage.parse_args(args)
            if outer_id is None:
                if len(unhandled_matches) > 1:
                    await self._callbacks.notify_handle_result('有多个待处理结果，请输入jobid！', update)
                    return
                outer_id = jobs_mapping.get_any_outer_id()

            inner_id = jobs_mapping.get_inner_id(outer_id)
            last_matched_events: EVENT_DICT = unhandled_matches.get(inner_id)
            if outer_id is None or inner_id is None or not last_matched_events:
                await self._callbacks.notify_handle_result('无法获取jobid' + str(outer_id) + '的搜索结果！', update)
                return self._END
            assert all(0 <= id <= len(last_matched_events) for id in ids)
        except (AssertionError, SyntaxError, TypeError, ValueError) as e:
            await self._callbacks.notify_handle_result('参数错误，请重新输入！\n' + str(e), update)
            return

        successed_ids, failed_ids, already_subbed_ids, result_text = self._util_subscribe(ids, last_matched_events, subscriber)

        # 已预约过的视为预约成功
        successed_ids.extend(already_subbed_ids)

        if successed_ids:
            subbed_events: list[Event] = context.user_data.setdefault(KEY_SUBBED_EVENTS, [])
            subbed_events.extend(last_matched_events[id] for id in successed_ids)
            # mark event if success
            for id in successed_ids:
                last_matched_events[id] = None
            context.application.mark_data_for_update_persistence(
                chat_ids=update.effective_chat.id,
                user_ids=update.effective_user.id,
            )

        if failed_ids:
            # stay in current state if any failed
            return

        # clear data if all success
        unhandled_matches.pop(inner_id)

        await self._callbacks.notify_handle_result(result_text, update)

        return self._END

    async def _daily_job_confirmed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        # 是否添加任务？(是/否)
        if update.effective_message.text == '否':
            await self._callbacks.notify_handle_result('已取消', update)
            context.chat_data.pop(KEY_LAST_DAILY_JOB_ARGS)
            return self._END

        try:
            last_daily_job_args = context.chat_data.get(KEY_LAST_DAILY_JOB_ARGS, {})
            for key in ['channelstr', 'programstr', 'exclude_program', 'detail', 'check_time', 'days', 'start_time']:
                assert key in last_daily_job_args
        except AssertionError as e:
            await self._callbacks.notify_handle_result('无法获取用户定时任务参数！\n' + str(e), update)
            return self._END

        context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)
        job_name = uuid4().hex
        jobs_mapping = context.chat_data.setdefault(KEY_JOB_MAPPING, JobsMapping())
        jobs_mapping.insert(job_name)
        outer_id = jobs_mapping.get_outer_id(job_name)
        # 为避免多个job同时触发的时候解决handle冲突，入口指令添加 job_name
        # 一个job只需要一个handler反复用即可，触发多次则新的进度覆盖旧的
        conv_check_daily_job = ConversationHandler(
            allow_reentry=True,  # 如果同一个job连续触发多次，允许覆盖掉旧的
            entry_points=[
                ChainCommandHandler(
                    '/checkdailyjob' + job_name,
                    callback=self._cmd_check)
            ],
            states={
                self._CHECK_DAILY_JOB_MACHES: [
                    MessageHandler(filters.Regex(fr'(^[\d,]+)(?: ({outer_id}))?$'), self._callback_subscribe_daily_job)
                ]
            },
            fallbacks=[
                MessageHandler(filters.Regex(fr'^/sub cancel(?: ({outer_id}))?$'), self._cancel_conversation),
                MessageHandler(filters.ALL, self._prompt_resend),
            ],
            persistent=True,
            name=job_name,
        )
        # store conv handler for future removal
        self._conv_check_daily_jobs[conv_check_daily_job.name] = conv_check_daily_job
        check_time: datetime.time = last_daily_job_args.get('check_time')
        days: tuple[int] = last_daily_job_args.get('days')

        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        job: Job = context.job_queue.run_daily(
            callback=JobsManager.job_check_daily,
            time=check_time,
            days=days,
            data={
                JOBKEY_DAILY_JOB_ARGS: last_daily_job_args,
                JOBKEY_THIS_UPDATE: update,
            },
            name=job_name,
            chat_id=chat_id,
            user_id=user_id,
            job_kwargs=dict(replace_existing=True, id=job_name),
        )
        context.application.add_handler(conv_check_daily_job)

        context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)
        jobid = jobs_mapping.get_outer_id(job.name)
        await self._callbacks.notify_handle_result(
            '已添加，jobid: ' +
                str(jobid),
            update)
        context.chat_data.pop(KEY_LAST_DAILY_JOB_ARGS)
        return self._END

    # conversation state commands
    async def _cancel_interactive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ...

    async def _cancel_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        # 退出会话、删除所在会话的匹配结果
        # 考虑如果没输jobid，而有多个unhandled matches的情况
        unhandled_matches: Dict[Union[JOB_NAME, JOB_NAME_ONCE], EVENT_DICT] = context.chat_data.get(KEY_UNHANDLED_MATCHES, {})
        if len(unhandled_matches) == 0:
            # nothing to cancel, maybe 'daily'. just end conversation.
            await self._callbacks.notify_handle_result(
                '已终止会话', update)
            return self._END

        jobs_mapping: JobsMapping = context.chat_data.get(KEY_JOB_MAPPING, JobsMapping())
        suffix = context.match.group(1)
        if suffix is None:
            # does not have suffix jobid, 'daily' or 'now'
            if len(unhandled_matches) > 1:
                await self._callbacks.notify_handle_result('有多个待处理结果，请指定要取消的任务！\n参数为：<jobid>、now、daily', update)
                return
            else:
                # cancels now or jobid
                job_name = list(unhandled_matches.keys())[0]
                suffix = 'now' if job_name == KEY_JOB_NAME_ONCE else str(jobs_mapping.get_outer_id(job_name, ''))

        if not suffix:
            # suffix is empty. not likely but in case for robustness
            pass
        elif suffix == 'now':
            # cancels cmd 'now'
            try:
                unhandled_matches.pop(KEY_JOB_NAME_ONCE)
                context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)
            except KeyError:
                pass
        else:
            # cancels daily job with jobid
            jobid = int(suffix)
            job_name = jobs_mapping.get_inner_id(jobid)

            # make sure user input jobid is valid
            try:
                assert job_name is not None
                unhandled_matches.pop(job_name)
                context.application.mark_data_for_update_persistence(chat_ids=update.effective_chat.id)
            except (AssertionError, KeyError):
                await self._callbacks.notify_handle_result(self._prompt_jobid_not_found(suffix), update)
                return

        await self._callbacks.notify_handle_result('已终止会话' + (('jobid ' + suffix) if suffix.isdigit() else suffix if suffix else ''), update)
        return self._END

    async def _prompt_resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._callbacks.notify_handle_result('输入错误，请重新输入或取消会话', update)

    # private utils
    def _util_subscribe(
        self,
        ids: list[int],
        last_matched_events: EVENT_DICT,
        subscriber: TVSubscriber
    ) -> tuple[SUCCESSED_IDS, FAILED_IDS, ALREADY_SUBBED_IDS, RESULT_TEXT]:
        if any(id == 0 for id in ids):
            ids = [0]
        else:
            # 去重+排序
            ids = sorted(set(ids))

        should_sub_all = ids[0] == 0
        reservations = []
        successed_ids = []
        already_subbed_ids = []
        failed_ids = []
        errors = []
        # last_matched_events 不包含本次之前已订阅的节目
        if should_sub_all:
            avail_events = last_matched_events
        else:
            # 根据用户输入序号筛选
            avail_events = {id: last_matched_events[id] for id in ids}

        for id, event in avail_events.items():
            if event is None:
                # this is a successfully subscribed event
                continue
            try:
                reservation = self._do_subscribe(subscriber, event)
                reservations.append(reservation)
                successed_ids.append(id)
            except (ApiException, httpx.ConnectError) as e:
                if '已经预约过' in str(e):
                    already_subbed_ids.append(id)
                else:
                    failed_ids.append(id)
                    errors.append(str(e))

        try:
            userinfo = subscriber.get_userinfo()
            result_text = '余额：' + userinfo.wallet + '元\n'
        except (ApiException, httpx.ConnectError):
            result_text = '余额获取失败\n'

        if successed_ids:
            result_text += '预约成功：' + ', '.join(map(str, successed_ids)) + '\n'

        if already_subbed_ids:
            result_text += '已预约过：' + ', '.join(map(str, already_subbed_ids)) + '\n'

        if failed_ids:
            # 如果有失败，可再次输入需要重新预约的序号
            result_text += '预约失败：' + ', '.join(
                f'{id}（{e}）' for id, e in
                zip(failed_ids, errors)) + '\n' + self._prompt_input_ids_now + '\n（成功的节目将被跳过。）'

        return successed_ids, failed_ids, already_subbed_ids, result_text

    @staticmethod
    def _do_subscribe(subscriber: TVSubscriber, program: Event) -> Reservation:
        return subscriber.subscribe(program.sid, program.eid, program.tsid, program.onid, program.price, program.network, program.reservetoken)

    # prompts
    @property
    def _prompt_input_ids_now(self) -> str:
        return (
            "请输入需要订阅的序号，0表示全选，多个序号必须用英文逗号','隔开。\n"
            "输入/sub cancel或/sub cancel now终止订阅。"
        )

    @property
    def _prompt_input_ids_daily_job(self) -> str:
        return (
            "请输入需要订阅的序号和jobid，二者用空格隔开，如1,2 1\n" 
            "序号可以用0表示全选，多个序号必须用英文逗号','隔开。\n" 
            "如果只有一个未处理定时任务则可省略jobid\n" 
            "输入/sub cancel或/sub cancel <jobid>取消本次订阅。"
        )

    @property
    def _prompt_job_confirm(self) -> str:
        return (
            '是否添加任务？（是/否）\n'
            '输入/sub cancel或/sub cancel <jobid>终止此会话。'
        )

    def _prompt_jobid_not_found(self, jobid: str) -> str:
        return '找不到jobid: ' + jobid

    def _prompt_job_info(self, **kwargs) -> str:
        items = []
        for key, val in kwargs.items():
            if key == 'enabled':
                val = '已启用' if val else '已禁用'
            elif key == 'check_time':
                val = val.strftime('%H:%M:%S')
            elif key == 'start_time':
                val = val.strftime('%H:%M:%S') if val else '无'
            elif key == 'exclude_program':
                val = '' if val is None else val
            else:
                val = str(val)
            items.append(': '.join((key, val)))
        return '\n'.join(items)

    @staticmethod
    def _prompt_matched_events(
        events: List[Event],
        subbed_events_for_hint: Optional[List[Event]] = None,
    ) -> str:
        """提示匹配到的节目
        """
        msgs = ['共找到' + str(len(events)) + '个结果']
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
            if subbed_events_for_hint is not None and events in subbed_events_for_hint:
                msg = ['【已订阅】'] + msg
            msgs.append('\n'.join(msg))

        return '\n\n'.join(msgs)

    def _show_sub_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        ...
        return self._END

    @property
    def _usages(self):
        #  https://docs.pydantic.dev/latest/usage/models/#dynamic-model-creation
        return {
            '_cmd_help': StringArgConverter('/sub help - 显示此帮助'),
            '_cmd_login': StringArgConverter(
                '/sub login <username> <password> - 登陆',
                username=(str,),
                password=(str,)
            ),
            '_cmd_search': StringArgConverter(
                '/sub search <channel> <program> '
                '[excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 搜索节目',
                channel=(str,),
                program=(str,),
                excludeProgram=(str, None),
                detail=(str, '*'),
                startDate=(datetime.date, None, cast_date),  # None表示不限制
                startTime=(datetime.time, None, cast_time_jp),  # None表示不限制
                findFirstMatch=(bool, False, cast_bool_builder(False)),
            ),
            '_cmd_now': StringArgConverter(
                '/sub now <channel> <program> '
                '[excludeProgram] [detail] [startDate] [startTime] [findFirstMatch] - 执行单次订阅任务',
                channel=(str,),
                program=(str,),
                excludeProgram=(str, None),
                detail=(str, '*'),
                startDate=(datetime.date, None, cast_date),
                startTime=(datetime.time, None, cast_time_jp),
                findFirstMatch=(bool, False, cast_bool_builder(False)),
            ),
            '_cmd_daily': StringArgConverter(
                '/sub daily <channel> <program> '
                '[excludeProgram] [detail] [checkTime] [days] [startTime] - 添加每日定时检查任务',
                channel=(str,),
                program=(str,),
                excludeProgram=(str, None),
                detail=(str, '*'),
                checkTime=(datetime.time, datetime.datetime.now(pytz.timezone('Asia/Shanghai')).time, cast_time_cn),
                days=(tuple[int],
                      tuple(range(0, 6 + 1)),
                      cast_ints),
                startTime=(datetime.time, None, cast_time_jp),
            ),
            '_cmd_check': StringArgConverter(
                '/sub check <jobid> - 手动触发单个定时任务',
                jobid=(int,)
            ),
            '_cmd_userinfo': StringArgConverter('/sub userinfo - 查看当前账户信息'),
            '_cmd_list': StringArgConverter('/sub list - 查看已添加的定时任务'),
            # '_cmd_edit': StringArgConverter(
            #     '/sub edit <jobid> [channel] [program] '
            #     '[excludeProgram] [detail] [checkTime] [days] [startTime] - 修改单个定时任务',
            #     jobid=(int,),
            #     channel=(str, None),
            #     program=(str, None),
            #     excludeProgram=(str, None),
            #     detail=(str, '*'),
            #     checkTime=(datetime.time, None, cast_time_cn),  # 注意None表示不改变原值
            #     days=(tuple[int], None, cast_ints),
            #     startTime=(datetime.time, None, cast_time_jp),
            # ),
            # '_cmd_disable': StringArgConverter(
            #     '/sub disable <jobids> - 禁用(多个)定时任务',
            #     jobids=(list[int], cast_ints)
            # ),
            # '_cmd_enable': StringArgConverter(
            #     '/sub enable <jobids> - 恢复(多个)定时任务',
            #     jobids=(list[int], cast_ints)
            # ),
            '_cmd_remove': StringArgConverter(
                '/sub remove <jobids> - 删除(多个)定时任务',
                jobids=(list[int], cast_ints)
            ),
            # '_cmd_start': StringArgConverter('/sub start - 交互式添加定时任务'),
        }

    @property
    def _usages_private(self):
        return {
            '_subscribe_now': StringArgConverter(
                '输入序号，为单个数字，或多个用","隔开的数字',
                ids=(list[int], cast_ints)
            ),
            '_subscribe_daily_job': StringArgConverter(
                '输入"序号 jobid"，序号为单个数字，或多个用","隔开的数字，jobid为单个数字',
                ids=(list[int], cast_ints),
                jobid=(int, None)
            ),
        }
