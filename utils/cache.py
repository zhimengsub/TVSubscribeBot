import sqlite3
from typing import Optional

from .consts import CACHE
# from TVSubscriber import NETWORKS
from tvsubscriber.models import Channel
from .widthConv import convertline


class CacheManager:
    def __init__(self):
        self.conn: Optional[sqlite3.Connection] = None
        self._isclosed: bool = True
        self.create_table()

    def connect(self):
        if not self._isclosed:
            return
        self._isclosed = False
        self.conn = sqlite3.connect(str(CACHE))
        self.c = self.conn.cursor()

    def close(self):
        if self._isclosed:
            return
        self.conn.close()
        self._isclosed = True

    def create_table(self):
        # note: 频道名不唯一，甚至同一个network下频道名也可能不唯一
        self.connect()
        self.c.execute("""CREATE TABLE IF NOT EXISTS 
            channels (
                service TEXT NOT NULL, 
                network TEXT NOT NULL, 
                sid TEXT NOT NULL, 
                tsid TEXT, 
                PRIMARY KEY(service, network, sid)
            )""")
        self.conn.commit()
        self.close()

    def refresh_table(self):
        #! epgtoken可能会随时间变化，导致cache失效，故不保存
        self.connect()
        self.c.execute('DROP TABLE IF EXISTS channels')
        self.conn.commit()
        self.create_table()
        self.close()

    def insert_channels(self, channels: list[Channel]):
        self.connect()
        for channel in channels:
            self.c.execute(f"""INSERT OR REPLACE INTO 
                channels(service, network, sid, tsid) VALUES
                ('{channel.service}', '{channel.network}', '{channel.sid}', '{channel.tsid or ''}');""")
        self.conn.commit()
        self.close()

    def find_channels(self, keyword: str) -> list[Channel]:
        """
        return: list[(network, Channel)]
        """
        # / around keyword (after stripping quotations) means word boundary
        keyword = convertline(keyword)
        if not keyword.startswith('/'):
            keyword = '%' + keyword
        else:
            keyword = keyword.lstrip('/')

        if not keyword.endswith('/'):
            keyword = keyword + '%'
        else:
            keyword = keyword.rstrip('/')

        self.connect()
        self.conn.create_function("convertline", 1, convertline)
        cur = self.c.execute(f"SELECT service, network, sid, tsid from channels WHERE convertline(service) LIKE '{keyword}'")
        res = []
        for row in cur:
            res.append(Channel(service=row[0], network=row[1], sid=row[2], tsid=row[3] or None, epgtoken=None))
        self.close()
        return res


