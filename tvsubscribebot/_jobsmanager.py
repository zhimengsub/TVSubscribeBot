import datetime
import logging

import pytz

from dumb_bot.dumbbot import Update
from dumb_bot.dumbbot.ext import ContextTypes
from tvsubscribebot._jobsmapping import JobsMapping
from tvsubscribebot.utils.consts import JOBKEY_THIS_UPDATE, KEY_JOB_MAPPING, KEY_SUBBED_EVENTS
from tvsubscriber import Event


class JobsManager:
    """把所有定时任务相关的函数单独提出来，方便pickle"""
    # def __init__(self, search_utils: SearchUtils, callbacks: HandlerCallbacks):
    def __init__(self):
        # self._search_utils = search_utils
        # self._callbacks: HandlerCallbacks = callbacks
        ...

    @staticmethod
    async def job_update_subbed_events(context: ContextTypes.DEFAULT_TYPE):
        """1. 清理已经播完的节目，因为不可能再出现在搜索结果中
        2. 从订单中获取用户手动订阅的节目
        """
        # TODO Test this function
        # 1. 清理
        subbed_events: list[Event] = context.user_data.get(KEY_SUBBED_EVENTS, [])
        for i, event in enumerate(subbed_events):
            start_datetime = datetime.datetime(event.startdate.year, event.startdate.month, event.startdate.day, event.starttime.hour, event.starttime.minute, event.starttime.second, tzinfo=pytz.timezone('Asia/Tokyo'))
            duration_delta = datetime.timedelta(minutes=event.duration)
            end_datetime = datetime.datetime.now(pytz.timezone('Asia/Tokyo'))
            if start_datetime + duration_delta <= end_datetime:
                # 已播完
                logging.debug('removed event ' + str(event) + ' from subbed_events for userid ' + str(context.job.user_id))
                subbed_events.pop(i)
                context.application.mark_data_for_update_persistence(context.job.user_id)
        # TODO 2. 新增
        #   从订单中获取用户手动订阅的节目,
        #   需要去重、实现Reservation和Event的比较，如果有多个匹配的节目则不添加

    @staticmethod
    async def job_check_daily(context: ContextTypes.DEFAULT_TYPE):
        """触发定时任务（定时触发或手动）"""
        # add conv for checking daily job and ask user's response
        job = context.job

        # daily_job_args = job.data[JOBKEY_DAILY_JOB_ARGS]
        # channelstr: str = daily_job_args['channelstr']
        # programstr: str = daily_job_args['programstr']
        # exclude_program: Optional[str] = daily_job_args['exclude_program']
        # detail: str = daily_job_args['detail']
        # start_time: Optional[datetime.time] = daily_job_args['start_time']

        # manually enter this conv handler
        update: Update = job.data[JOBKEY_THIS_UPDATE]
        # args = ' '.join([job.name, channelstr, programstr])
        # kwargs = []
        # kwargs += ['excludeProgram=' + exclude_program] if exclude_program is not None else []
        # kwargs += ['detail=' + detail]
        # kwargs += ['startTime=' + start_time.strftime('%H%M%S')] if start_time is not None else []
        # kwargs = ' '.join(kwargs)
        # cmd = ' '.join(['/checkdailyjob' + job.name, args, kwargs])
        jobs_mapping: JobsMapping = context.chat_data.get(KEY_JOB_MAPPING)

        jobid = jobs_mapping.get_outer_id(job.name)
        cmd = ' '.join(['/checkdailyjob' + job.name, str(jobid)])
        with update.message._unfrozen():
            update.message.text = cmd
        await context.application.process_update(update)

__all__ = (JobsManager,)
