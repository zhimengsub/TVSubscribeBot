from pathlib import Path

ROOT = Path(__file__).parents[1]

DB_CACHE = ROOT / 'data' / 'cache.db'

DB_JOBSTORE = 'mongodb://127.0.0.1:27017/admin?retryWrites=true&w=majority'

CACHE_JSON = ROOT / 'data' / 'config.json'

PERSISTENCE_PKL = ROOT / 'data' / 'bot_pkl'

PERSISTENCE_UPDATE_INTERVAL = 60.0

# ids for job
JOB_UPDATE_SUBBED_EVENT_PREFIX = 'job_update_subbed_events_user'
# keys for job's data
JOBKEY_DAILY_JOB_ARGS = '_jobdata_daily_job_args'
JOBKEY_THIS_UPDATE = '_jobdata_this_update'

# keys for bot_data / user_data / chat_data
KEY_JOB_MAPPING = '_job_mapping'
KEY_UNHANDLED_MATCHES = '_unhandled_matches'  # 考虑到可能出现定时任务打断当前会话/多个定时任务并发的情况，并且需要正确应对用户输入，把定时任务触发时搜索到的结果推入一个未处理list，标上jobid。
KEY_LAST_DAILY_JOB_ARGS = '_last_daily_job_args'
KEY_JOB_NAME_ONCE = '_job_once'  # a job that corresponds to results from 'cmd_now'
KEY_SUBBED_EVENTS = '_subbed_events'

# repeat job intervals in seconds
INT_UPDATE_SUBBED_EVENTS = 3600

