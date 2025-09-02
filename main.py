import asyncio
import datetime
import os
import traceback
from pathlib import Path
from typing import Any, Tuple

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain

# 保存新闻的目录
SAVED_NEWS_DIR = Path("data", "plugins_data", "astrbot_plugin_daily_60s_news", "news")
SAVED_NEWS_DIR.mkdir(parents=True, exist_ok=True)


@register(
    "daily_60s_news",
    "MskTmi",
    "这是 AstrBot 的一个每日60s新闻插件。支持定时发送和命令发送",
    "1.0.4",
)
class Daily60sNewsPlugin(Star):
    """
    AstrBot 每日60s新闻插件，支持定时推送和命令获取。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.news_type = self.config.news_type
        self.news_path = SAVED_NEWS_DIR
        self.groups = self.config.groups
        self.push_time = self.config.push_time
        logger.info(f"插件配置: {self.config}")
        # 启动定时任务
        self._monitoring_task = asyncio.create_task(self._daily_task())

    @filter.command_group("新闻")
    def mnews(self):
        """新闻命令分组"""
        pass

    @mnews.command("news", alias={"早报", "新闻"})
    async def daily_60s_news(self, event: AstrMessageEvent):
        """
        在当前聊天页面获取今日60s新闻（根据配置类型返回文本或图片）,
        别名：早报，新闻
        """
        if self.news_type == "text":
            news_content, _ = await self._get_text_news()
            yield event.plain_result(news_content)
        else:
            news_path, _ = await self._get_image_news()
            yield event.image_result(news_path)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mnews.command("status")
    async def check_status(self, event: AstrMessageEvent):
        """
        检查插件状态（仅管理员）
        """
        sleep_time = self._calculate_sleep_time()
        hours = int(sleep_time / 3600)
        minutes = int((sleep_time % 3600) / 60)

        yield event.plain_result(
            f"每日60s新闻插件正在运行\n"
            f"推送时间: {self.push_time}\n"
            f"默认新闻格式: {'文本' if self.news_type == 'text' else '图片'}\n"
            f"距离下次推送还有: {hours}小时{minutes}分钟"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mnews.command("clean")
    async def clean_news(self, event: AstrMessageEvent):
        """
        清理过期新闻文件（仅管理员）
        """
        await self._delete_expired_news_files()
        yield event.plain_result(
            f"{event.get_sender_name()}: 过期({self.config.save_days}前)新闻文件已清理。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mnews.command("push")
    async def push_news(self, event: AstrMessageEvent):
        """
        手动向目标群组推送今日60s新闻（仅管理员）
        """
        await self._send_daily_news_to_groups()
        yield event.plain_result(f"{event.get_sender_name()}:已成功向群组推送新闻")

    @mnews.command("text")
    async def push_text_news(self, event: AstrMessageEvent):
        """
        在当前聊天页面获取今日60s新闻-文字
        """
        news_content, _ = await self._get_text_news()
        yield event.plain_result(news_content)

    @mnews.command("image")
    async def push_image_news(self, event: AstrMessageEvent):
        """
        在当前聊天页面获取今日60s新闻-图片
        """
        news_path, _ = await self._get_image_news()
        yield event.image_result(news_path)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mnews.command("update_news")
    async def update_news_files(self, event: AstrMessageEvent):
        """
        强制更新新闻文件（仅管理员）
        """
        text_content = await self._update_news_files()
        yield event.plain_result(
            f"{event.get_sender_name()}:今日新闻文件已更新,文字新闻简略内容:\n{text_content[:50]}..."
        )

    async def terminate(self):
        """插件卸载时调用"""
        if self._monitoring_task:
            self._monitoring_task.cancel()
        logger.info("每日60s新闻插件: 定时任务已停止")

    async def _update_news_files(self):
        logger.info("开始强制更新新闻文件...")
        text_path, _ = self._get_news_file_path(news_type="text")
        text_content, _ = await self._download_news(path=text_path, news_type="text")
        image_path, _ = self._get_news_file_path(news_type="image")
        await self._download_news(path=image_path, news_type="image")
        return text_content

    def _file_exists(self, path: str) -> bool:
        """
        判断新闻文件是否存在
        """
        return os.path.exists(path)

    def _get_news_file_path(self, news_type: str) -> Tuple[str, str]:
        """
        获取今日新闻文件的绝对路径和文件名
        :param news_type: 'text' 或 'image'
        :return: (文件绝对路径, 文件名)
        """
        current_date = datetime.datetime.now().strftime("%Y%m%d")
        name = f"{current_date}.txt" if news_type == "text" else f"{current_date}.jpeg"
        path = os.path.join(self.news_path, name)
        logger.info(f"mnews path: {path}")
        return path, name

    async def _get_text_news(self) -> Tuple[str, bool]:
        """
        获取文本新闻内容，若本地无则下载
        :return: (新闻内容, 是否成功)
        """
        path, _ = self._get_news_file_path(news_type="text")
        if self._file_exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = f.read()
            return data, True
        else:
            return await self._download_news(path, news_type="text")

    async def _get_image_news(self) -> Tuple[str, bool]:
        """
        获取图片新闻路径，若本地无则下载
        :return: (图片路径, 是否成功)
        """
        path, _ = self._get_news_file_path(news_type="image")
        if self._file_exists(path):
            return path, True
        else:
            return await self._download_news(path, news_type="image")

    async def _download_news(self, path: str, news_type: str) -> Tuple[Any, bool]:
        """
        下载今日新闻（文本或图片），失败自动重试
        :param path: 保存路径
        :param news_type: 'text' 或 'image'
        :return: (内容或路径, 是否成功)
        """
        retries = 3
        timeout = 5
        url_type = "text" if news_type == "text" else "image-proxy"
        date = datetime.datetime.now().strftime("%Y-%m-%d")

        # 读取配置里的 news_api，没有则用默认
        base_api = getattr(self.config, "news_api", None) or "https://60s-api.viki.moe/v2/60s"

        for attempt in range(retries):
            try:
                url = f"{base_api}?date={date}&encoding={url_type}"
                logger.info(f"开始下载新闻文件:{url}...")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=timeout) as response:
                        if response.status == 200:
                            content = await response.read()
                            with open(path, "wb") as f:
                                f.write(content)
                            if news_type == "text":
                                return content.decode("utf-8"), True
                            else:
                                return path, True
                        else:
                            raise Exception(f"API返回错误代码: {response.status}")
            except Exception as e:
                logger.error(f"[mnews] 请求失败，正在重试 {attempt + 1}/{retries} 次: {e}")
                if attempt == retries - 1:
                    logger.error(f"[mnews] 请求新闻接口失败: {e}")
                    content = f"接口报错，请联系管理员:{e}"
                    return content, False
                await asyncio.sleep(1)

    async def _send_daily_news_to_groups(self):
        """
        推送新闻到所有目标群组
        """
        for target in self.config.groups:
            try:
                if self.news_type == "text":
                    news_content, _ = await self._get_text_news()
                    message_chain = MessageChain().message(news_content)
                    logger.info(f"[每日新闻] 推送文本新闻: {news_content[:50]}...")
                    await self.context.send_message(target, message_chain)
                else:
                    news_path, _ = await self._get_image_news()
                    message_chain_text = (
                        MessageChain().message("早上好呀, 这是今天的新闻速报\nq(≧▽≦q)")
                    )
                    await self.context.send_message(target, message_chain_text)
                    
                    message_chain_img = (
                        MessageChain().file_image(news_path)
                    )
                    await self.context.send_message(target, message_chain_img)
                    
                    logger.info(f"[每日新闻] 推送图片新闻: {news_path}")
                    
                logger.info(f"[每日新闻] 已向{target}推送定时新闻。")
                await asyncio.sleep(2)  # 防止推送过快
            except Exception as e:
                error_message = str(e) if str(e) else "未知错误"
                logger.error(f"[每日新闻] 推送新闻失败: {error_message}")
                # 可选：记录堆栈跟踪信息
                logger.exception("详细错误信息：")
                await asyncio.sleep(2)  # 防止推送过快

    def _calculate_sleep_time(self) -> float:
        """
        计算距离下次推送的秒数
        :return: 距离下次推送的秒数
        """
        now = datetime.datetime.now()
        hour, minute = map(int, self.push_time.split(":"))
        next_push = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_push <= now:
            next_push += datetime.timedelta(days=1)
        return (next_push - now).total_seconds()

    async def _delete_expired_news_files(self):
        """
        删除过期新闻文件
        """
        save_days = self.config.save_days
        if save_days <= 0:
            raise ValueError("保存天数不能小于0")
        for filename in os.listdir(self.news_path):
            try:
                file_date = datetime.datetime.strptime(filename[:8], "%Y%m%d").date()
                if (datetime.date.today() - file_date).days >= save_days:
                    file_path = os.path.join(self.news_path, filename)
                    os.remove(file_path)
            except Exception:
                continue

    async def _daily_task(self):
        """
        定时任务主循环，定时推送新闻
        """
        while True:
            try:
                sleep_time = self._calculate_sleep_time()
                logger.info(f"[每日新闻] 下次推送将在 {sleep_time / 3600:.2f} 小时后")
                await asyncio.sleep(sleep_time)
                await self._update_news_files()
                await self._delete_expired_news_files()
                await self._send_daily_news_to_groups()
                await asyncio.sleep(60)  # 避免重复推送
            except Exception as e:
                logger.error(f"[每日新闻] 定时任务出错: {e}")
                traceback.print_exc()
                await asyncio.sleep(300)
