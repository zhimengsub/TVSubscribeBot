import json
import sqlite3
from pathlib import Path
from typing import Optional

from tvsubscribebot.tv_subscriber.tvsubscriber.models import Channel, Event
from tvsubscribebot.utils.consts import DB_CACHE, CACHE_JSON
from tvsubscribebot.utils.widthConv import convertline


class CacheManager:
    def __init__(self, dbfile: Path = DB_CACHE):
        self.dbfile = dbfile
        # self.jsonfile = jsonfile
        self.conn: Optional[sqlite3.Connection] = None
        self._isclosed: bool = True
        self.create_table_channels()

    def __getstate__(self):
        # Define what gets pickled (object's state)
        state = {k: self.__dict__[k] for k in ['dbfile', '_isclosed']}
        return state

    def __setstate__(self, state):
        # Restore the object's state from the unpickled dictionary
        self.__dict__.update(state)

    # channels
    def connect(self):
        if not self._isclosed:
            return
        self._isclosed = False
        self.conn = sqlite3.connect(str(self.dbfile))
        self.c = self.conn.cursor()

    def close(self):
        if self._isclosed:
            return
        self.conn.close()
        self._isclosed = True

    def create_table_channels(self):
        self.connect()
        # note: 频道名不唯一，甚至同一个network下频道名也可能不唯一
        self.c.execute("""CREATE TABLE IF NOT EXISTS 
            channels (
                service TEXT NOT NULL, 
                network TEXT NOT NULL, 
                sid TEXT NOT NULL, 
                tsid TEXT, 
                PRIMARY KEY(service, network, sid)
            );""")
        self.conn.commit()
        self.close()

    def refresh_table_channels(self):
        #! epgtoken可能会随时间变化，导致cache失效，故不保存
        self.connect()
        self.c.execute('DROP TABLE IF EXISTS channels')
        self.create_table_channels()
        self.conn.commit()
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

    # def create_table_subbed_events(self):
    #     self.connect()
    #     self.c.execute("""CREATE TABLE IF NOT EXISTS
    #         subbed_events (
    #             eid TEXT NOT NULL,
    #             sid TEXT NOT NULL,
    #             tsid TEXT NOT NULL,
    #             onid TEXT NOT NULL,
    #             price NUMERIC NOT NULL,
    #             network TEXT NOT NULL,
    #             PRIMARY KEY(eid, sid, tsid, onid, price, network)
    #         );""")
    #     self.conn.commit()
    #     self.close()
    #
    # def refresh_table_subbed_events(self):
    #     self.connect()
    #     self.c.execute('DROP TABLE IF EXISTS subbed_events')
    #     self.create_table_subbed_events()
    #     self.conn.commit()
    #     self.close()
    #
    # def insert_subbed_events(self, events: list[Event]):
    #     self.connect()
    #     for event in events:
    #         self.c.execute(f"""INSERT OR REPLACE INTO
    #             subbed_events(eid, sid, tsid, onid, price, network) VALUES
    #             ('{event.eid}', '{event.sid}', '{event.tsid}', '{event.onid}', '{event.price}', '{event.network}');""")
    #     self.conn.commit()
    #     self.close()

    # def is_subbed_events(self, event: Event) -> bool:
    #     self.connect()
    #     cur = self.c.execute(f"SELECT (eid, sid, tsid, onid, price, network) from channels WHERE convertline(service) LIKE '{keyword}'")
    #     res = []
    #     for row in cur:
    #         res.append(Channel(service=row[0], network=row[1], sid=row[2], tsid=row[3] or None, epgtoken=None))
    #     self.close()
    #     return res

