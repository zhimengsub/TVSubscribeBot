import datetime
import pytz
from typing import Callable


def cast_time_cn(value: str) -> datetime.time:
    return cast_time(value, pytz.timezone('Asia/Shanghai'))


def cast_time_jp(value: str) -> datetime.time:
    return cast_time(value, pytz.timezone('Asia/Tokyo'))


def cast_time(value: str, timezone: pytz.tzinfo) -> datetime.time:
    # format: HHMMSS or HHMM
    try:
        # raise ValueError for wrong format
        return datetime.datetime.strptime(value, '%H%M').time().replace(tzinfo=timezone)
    except ValueError:
        # raise ValueError for wrong format
        return datetime.datetime.strptime(value, '%H%M%S').time().replace(tzinfo=timezone)


def cast_date(value: str) -> datetime.date:
    # format: yyyymmdd
    # raise ValueError for wrong format
    return datetime.datetime.strptime(value, '%Y%m%d').date()


def cast_bool_builder(default_value: bool) -> Callable[[str], bool]:
    def cast_bool(value: str) -> bool:
        if value.lower() in ['true', '1']:
            return True
        elif value.lower() in ['false', '0']:
            return False
        return default_value
    return cast_bool


def cast_ints(value: str) -> tuple[int, ...]:
    """value: single int, or int separated by ','"""
    return tuple(map(int, value.replace(' ', '').split(',')))