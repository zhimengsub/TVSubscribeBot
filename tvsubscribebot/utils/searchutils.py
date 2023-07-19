import re
from typing import Dict, Optional, List

from loguru import logger

from tvsubscribebot.tv_subscriber.tvsubscriber import NETWORK_NAMES, NETWORKS
from tvsubscribebot.tv_subscriber.tvsubscriber import TVSubscriber, Channel, Event
from .cache import CacheManager
from .casts import *
from .errors import BadResultException
from .widthConv import convertline


class SearchUtils:
    def __init__(self, cache: CacheManager):
        self._cache: CacheManager = cache

    def find_channels(self, subscriber: TVSubscriber, keyword: str) -> list[Channel]:
        """
        Only look up cache, need to manually refresh if result is not desired.

        注意catch ApiException
        """
        # / around keyword (after stripping quotations) means word boundary
        keyword = keyword.replace('*', '%').replace('?', '_')
        matches = self._cache.find_channels(keyword)

        if len(matches) == 0:
            # cache miss
            # channel keyword not exist
            # return []
            # TODO change to cache only.
            # logger.info('cache miss! Please manually refresh if needed.')
            logger.info('cache miss! refresh then search again.')
            self._refresh_channel_cache(subscriber)
            logger.debug('cache refreshed.')
            matches = self._cache.find_channels(keyword)
        else:
            logger.info('cache hit!')

        # found matched channels, update their epgtoken
        logger.info('updating epgtoken')
        self._update_epgtoken(subscriber, matches)
        return matches

    def find_programs(
        self,
        subscriber: TVSubscriber,
        channel: Channel,
        program: str,
        exclude_program: Optional[str],
        detail: str,
        start_date: Optional[datetime.date],
        start_time: Optional[datetime.time],
        find_first_match: bool,
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
        pat_exclude_program = self._make_pattern(convertline(exclude_program)) if isinstance(exclude_program, str) else None
        pat_detail = self._make_pattern(convertline(detail))

        matches = []
        for i, event in enumerate(events):
            if (
                pat_program.search(convertline(event.event_name)) and
                pat_detail.search(convertline(event.event_text + '\n' + event.event_ext_text)) and
                (pat_exclude_program is None or not pat_exclude_program.search(convertline(event.event_name))) and
                (not isinstance(start_date, datetime.date) or start_date == event.startdate) and
                (not isinstance(start_time, datetime.time) or start_time == event.starttime)
            ):
                matches.append(event)
                if find_first_match:
                    break
        return matches

    # utils
    @staticmethod
    def _make_pattern(keyword: str) -> re.Pattern:
        """把'*代表任意字符，?代表单个字符'的匹配规则替换为正则表达式"""
        keyword = re.escape(keyword)
        keyword = keyword.replace(r'\*', '.*').replace(r'\?', '.')
        return re.compile(keyword)

    def _refresh_channel_cache(self, subscriber: TVSubscriber):
        self._cache.refresh_table_channels()
        channels = []
        for network in NETWORK_NAMES.keys():
            channels.extend(subscriber.get_channels(network))
        self._cache.insert_channels(channels)

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
