import databases
import ormar
import sqlalchemy

DATABASE_URL = "sqlite:///db.sqlite"
database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()


class DataModel(ormar.Model):
    ...


engine = sqlalchemy.create_engine(DATABASE_URL)
metadata.create_all(engine)
