from typing import TypeVar, Dict, Optional, Literal

from tvsubscriber import Event

RESULT_TEXT = str
EVENT_ID = TypeVar('EVENT_ID', bound=int)
EVENT_DICT = Dict[EVENT_ID, Optional[Event]]
JOB_NAME_ONCE = Literal['_job_once']
