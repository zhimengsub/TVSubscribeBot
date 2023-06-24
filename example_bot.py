import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
from tvsubscribebot import TVSubscribeBot, Chat

bot = TVSubscribeBot(update_interval=60)


@bot.register_callback
async def on_handled(result: str, chat: Chat):
    """chat can be used for telling users apart"""
    print(result)

bot.listen_forever()
