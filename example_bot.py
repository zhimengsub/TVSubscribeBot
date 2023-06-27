import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
from tvsubscribebot import TVSubscribeBot, Chat, User

bot = TVSubscribeBot(update_interval=6000)


@bot.register_callback
async def on_handled(result: str, chat: Chat, user: User):
    """chat can be used for telling users apart"""
    # TODO change callback param to be chat and user
    print(result)

bot.listen_forever()
