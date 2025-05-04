from typing import TypeVar, Dict, Optional, Literal

from tvsubscriber import Event

USER_ID = TypeVar('USER_ID', bound=int)
RESULT_TEXT = str
EVENT_ID = TypeVar('EVENT_ID', bound=int)
EVENT_DICT = Dict[EVENT_ID, Optional[Event]]
JOB_NAME_ONCE = Literal['_job_once']
