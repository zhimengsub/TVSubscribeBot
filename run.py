import os

from dotenv import load_dotenv
from graia.ariadne.entry import Ariadne, WebsocketClientConfig, config
from graia.broadcast import Broadcast
from graia.saya import Saya
from graia.saya.builtins.broadcast import BroadcastBehaviour
from graia.saya.event import SayaModuleInstalled
from loguru import logger

load_dotenv()

application = Ariadne(
    config(
        int(os.environ["ACCOUNT"]),
        os.environ["VERIFY_KEY"],
        WebsocketClientConfig(os.environ["HOST"]),
    )
)
broadcast: Broadcast = application.broadcast
saya = Saya(broadcast)

saya.install_behaviours(BroadcastBehaviour(broadcast))


@broadcast.receiver(SayaModuleInstalled)
async def module_listener(event: SayaModuleInstalled):
    logger.success(f"{event.module}::模块加载成功!!!")


with saya.module_context():
    saya.require("modules.add")
    saya.require("modules.once")


def main():
    Ariadne.launch_blocking()


if __name__ == "__main__":
    main()
