# TVSubscribeBot
片源录制机器人

封装了基于字符串解析的通用机器人交互框架[command_handler](https://github.com/barryZZJ/command_handler)，本工具仍为通用框架，需要进一步接入具体的机器人。

需要python>=3.7，否则可选参数的解析顺序可能会出错。

## Requirments
- requirements of dumb_bot
  - `pip install python-telegram-bot[webhooks]`

- requirements for scheduled jobs ability
  - `pip install git+https://github.com/python-telegram-bot/ptbcontrib.git@main`
  - `python-telegram-bot[job-queue]~=20.0`
  - `SQLAlchemy==1.4.46`
  - `pymongo>=4.1,<5`
  - [Set up a Mongodb server](https://www.mongodb.com/docs/v6.0/tutorial/install-mongodb-on-ubuntu/) listening at 27017 (default port)

- requirements of tvsubscriber:
  - pydantic

## Usage example

see `example_bot.py` and `example_submitter.py`

for supported commands, see `TODO.md`
