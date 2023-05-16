# TVSubscribeBot
片源录制机器人

逻辑：设置要录制的节目名和所在的频道，指定轮询时间，到时间后搜索该节目，如果存在则询问用户是否要录制，然后进行录制。

## 指令

`<...>`表示必须参数

`[...=xxx]`表示可选参数，默认值为`xxx`

- 添加定时预约任务

解析crontab参考库 https://pypi.org/project/crontab/

`/subscribe add <channel keyword> <program keyword> [contab='0 10 * * *']`

> `<channel keyword>` 和 `<program keyword>` 不一定要输入全名，作为关键字进行匹配
>
> 获取频道信息需要指定所在地区(`network`参数)，频道所在地区是固定的，因此可以提前建一个cache保存频道所在的地区信息。
> 
> 匹配频道时先找本地cache，没有的话调直接遍历所有`network`，再进行查找。
> 
> 如果频道出现多个匹配则报错，提示用户匹配到的所有频道名。
> 
> 如果`<program keyword>`有匹配，则提示用户节目全名，否则提示没有匹配到，但均视为添加成功
> 
> 成功后把这条记录加入一个配置文件（记录Channel对象和节目名）
> 
> 并且开始执行定时任务（触发定时任务时从该频道搜索是否有匹配的节目，如果有则提示用户是否预约（见后文））


- 添加单次预约节目

`/subscribe once <channel keyword> <program keyword>`

> 频道关键字匹配同上
>
> 节目关键字如果有多个匹配，则直接触发检查预约事件，每个匹配询问一次用户是否订阅（见后文），如果无匹配则报错


- 查看已添加的定时任务
  `/subscribe list`

```
bot
1. フジテレビ テレビアニメ「鬼滅の刃」刀鍛冶の里編[字][解][デ] 0 10 * * *
2. ...
```

> 格式：`index` `event.network` `event.event_name` `crontab`

- 修改任务的触发时间

`/subscribe contab <index> <contab>`

- 暂停一个任务

`/subscribe disable <index>`

- 继续一个任务

`/subscribe enable <index>`

- 删除一个任务

`/subscribe remove <index>`


- 手动发起一次查询 

`/subscribe check <index>`

- 触发检查预约事件（定时任务触发或手动触发）

```
bot
是否预约节目 [是/否/暂不决定]：
播出时间：2023/05/14 23:15:00
频道：フジテレビ
节目：テレビアニメ「鬼滅の刃」刀鍛冶の里編[字][解][デ]
第六話『柱になるんじゃないのか！』
价格：3.5

user
是
(如果是非预期的其他回复则再次询问）

bot
预约成功 余额：xxx
/
预约失败 (错误信息从ApiException获取)
```

> 逻辑类似rss，用数据库之类的维护匹配到的节目的预约情况，
> 
> "是"则记为已预约；"否"则作为排除项；"暂不决定"则不预约但也不排除，下次触发定时任务时会再次提示。


备注：

1. TVSubscriber使用了git submodule，其中的改动需要推送到[TVSubscriber](https://github.com/zhimengsub/TVSubscriber)。

   使用参考：https://git-scm.com/book/zh/v2/Git-%E5%B7%A5%E5%85%B7-%E5%AD%90%E6%A8%A1%E5%9D%97

2. [crontab语法说明](https://www.runoob.com/linux/linux-comm-crontab.html)

3. 还要添加一些权限管理，在配置文件里指定对部分QQ号开放。


- (可以先不急着做) 交互式注册：`/subscribe start`

交互期间输入`/subscribe stop`中断交互过程

```
user
/subscribe start

bot
请输入频道名

user
フジテレビ

----------------
如果匹配到多个频道

bot
关东广域
1. 081 フジテレビ
CS110
2. 307 フジテレビＯＮＥ
3. 308 フジテレビＴＷＯ
4. 309 フジテレビＮＥＸＴ
CS124
5. 613 フジテレビＮＥＸＴ
6. 614 フジテレビＯＮＥ
7. 615 フジテレビＴＷＯ
请输入序号

user
1
----------------

bot
请输入节目名

user
鬼滅の刃
[如果匹配不到则提示重新输入]

bot
请输入更新检查时间（使用crontab语法）

user
0 10 * * *
（每天上午10点）

bot
成功
```