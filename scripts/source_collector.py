#!/usr/bin/env python3
"""
外部书源收集器
- 从GitHub仓库监控获取书源
- 爬取书源分享网站
- 处理大型书源数据文件
- 统一格式化和去重
"""

import json
import asyncio
import argparse
import re
import hashlib
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Set, Optional

try:
    import aiohttp
    import aiofiles
except ImportError:
    print("请先安装依赖: pip install aiohttp aiofiles")
    exit(1)

# 配置常量
DEFAULT_TIMEOUT = 30
CONCURRENCY = 5

# GitHub 书源仓库列表
GITHUB_REPOS = [
    "XIU2/yuedu",
    "aoaostar/legado-book-source",
    "shidahuilang/shuyuan",
    "vpei/Free-Novel-Source",
    "yeyulingfeng01/yuedu.github.io",
    "CNAD666/MyAction",
]

# 书源分享网站
SOURCE_WEBSITES = [
    "https://www.yckceo.com/yuedu/shuyuan/index.html",
    "https://legado.aoaostar.com/",
    "https://shuyuan.miaogongzi.net/",
]

class SourceCollector:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.collected_sources = []
        self.seen_urls = set()
        self.collection_stats = {
            'github': 0,
            'websites': 0,
            'files': 0,
            'duplicates': 0
        }

    def normalize_source(self, source: dict) -> Optional[dict]:
        """标准化书源格式"""
        try:
            # 必需字段检查
            if not source.get("bookSourceUrl") or not source.get("bookSourceName"):
                return None

            # 标准化字段
            normalized = {
                "bookSourceName": str(source.get("bookSourceName", "")).strip(),
                "bookSourceUrl": str(source.get("bookSourceUrl", "")).strip(),
                "bookSourceGroup": str(source.get("bookSourceGroup", "外部收集")).strip(),
                "enabled": bool(source.get("enabled", True)),
                "lastUpdateTime": int(source.get("lastUpdateTime", 0)),
                "bookSourceType": int(source.get("bookSourceType", 0)),
                "bookSourceComment": str(source.get("bookSourceComment", "")).strip(),
                "loginUrl": str(source.get("loginUrl", "")).strip(),
                "bookUrlPattern": str(source.get("bookUrlPattern", "")).strip(),
                "header": str(source.get("header", "")).strip(),
                "searchUrl": str(source.get("searchUrl", "")).strip(),
                "exploreUrl": str(source.get("exploreUrl", "")).strip(),
                "ruleSearch": source.get("ruleSearch", {}),
                "ruleExplore": source.get("ruleExplore", {}),
                "ruleBookInfo": source.get("ruleBookInfo", {}),
                "ruleToc": source.get("ruleToc", {}),
                "ruleContent": source.get("ruleContent", {}),
            }

            # URL 有效性检查
            parsed_url = urlparse(normalized["bookSourceUrl"])
            if not parsed_url.scheme or not parsed_url.netloc:
                return None

            return normalized

        except Exception as e:
            print(f"标准化书源失败: {e}")
            return None

    def is_duplicate(self, source: dict) -> bool:
        """检查是否重复"""
        url = source.get("bookSourceUrl", "")
        if url in self.seen_urls:
            self.collection_stats['duplicates'] += 1
            return True

        self.seen_urls.add(url)
        return False

    async def fetch_github_sources(self, session: aiohttp.ClientSession) -> List[dict]:
        """从GitHub仓库获取书源"""
        sources = []

        for repo in GITHUB_REPOS:
            try:
                print(f"正在获取 GitHub 仓库: {repo}")

                # 获取仓库文件列表
                api_url = f"https://api.github.com/repos/{repo}/contents"

                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if resp.status != 200:
                        print(f"  获取失败: HTTP {resp.status}")
                        continue

                    files = await resp.json()

                    # 查找书源文件
                    source_files = []
                    for file in files:
                        if file.get("type") == "file":
                            name = file.get("name", "").lower()
                            if any(keyword in name for keyword in ["书源", "shuyuan", "source", "legado"]):
                                if name.endswith((".json", ".txt")):
                                    source_files.append(file.get("download_url"))

                    # 下载并解析书源文件
                    for file_url in source_files[:3]:  # 限制每个仓库最多3个文件
                        try:
                            async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as file_resp:
                                if file_resp.status == 200:
                                    content = await file_resp.text()
                                    file_sources = self.parse_source_content(content)
                                    sources.extend(file_sources)
                                    print(f"  从 {file_url} 获取 {len(file_sources)} 个书源")
                        except Exception as e:
                            print(f"  下载文件失败 {file_url}: {e}")
                            continue

            except Exception as e:
                print(f"  获取仓库失败 {repo}: {e}")
                continue

            # 避免请求过快
            await asyncio.sleep(2)

        self.collection_stats['github'] = len(sources)
        return sources

    async def fetch_website_sources(self, session: aiohttp.ClientSession) -> List[dict]:
        """从书源分享网站获取书源"""
        sources = []

        for website in SOURCE_WEBSITES:
            try:
                print(f"正在获取网站: {website}")

                async with session.get(website, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                    if resp.status != 200:
                        print(f"  获取失败: HTTP {resp.status}")
                        continue

                    content = await resp.text()

                    # 查找页面中的书源链接
                    json_links = re.findall(r'href=["\']([^"\']*\.json[^"\']*)["\']', content)

                    for link in json_links[:5]:  # 限制每个网站最多5个链接
                        try:
                            full_url = urljoin(website, link)
                            async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as link_resp:
                                if link_resp.status == 200:
                                    link_content = await link_resp.text()
                                    link_sources = self.parse_source_content(link_content)
                                    sources.extend(link_sources)
                                    print(f"  从 {full_url} 获取 {len(link_sources)} 个书源")
                        except Exception as e:
                            print(f"  下载链接失败 {link}: {e}")
                            continue

            except Exception as e:
                print(f"  获取网站失败 {website}: {e}")
                continue

            # 避免请求过快
            await asyncio.sleep(3)

        self.collection_stats['websites'] = len(sources)
        return sources

    def parse_source_content(self, content: str) -> List[dict]:
        """解析书源内容"""
        sources = []

        try:
            # 尝试直接解析JSON
            data = json.loads(content)

            if isinstance(data, list):
                sources = data
            elif isinstance(data, dict):
                # 可能是包装格式
                if "sources" in data:
                    sources = data["sources"]
                elif "data" in data:
                    sources = data["data"]
                else:
                    sources = [data]

        except json.JSONDecodeError:
            # 尝试提取JSON片段
            json_pattern = r'\{[^{}]*"bookSourceUrl"[^{}]*\}'
            matches = re.findall(json_pattern, content)

            for match in matches:
                try:
                    source = json.loads(match)
                    sources.append(source)
                except:
                    continue

        return sources

    async def process_large_file(self, file_path: Path) -> List[dict]:
        """处理大型书源文件（如yiove_new.json）"""
        sources = []

        if not file_path.exists():
            print(f"文件不存在: {file_path}")
            return sources

        print(f"正在处理大型文件: {file_path}")

        try:
            # 分块读取大文件
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()

            # 解析内容
            file_sources = self.parse_source_content(content)

            # 随机采样（避免处理过多数据）
            import random
            if len(file_sources) > 1000:
                file_sources = random.sample(file_sources, 1000)
                print(f"  从 {len(file_sources)} 个书源中随机采样 1000 个")

            sources.extend(file_sources)
            self.collection_stats['files'] = len(sources)

        except Exception as e:
            print(f"处理大型文件失败: {e}")

        return sources

    async def collect_all_sources(self, large_files: List[Path] = None) -> List[dict]:
        """收集所有来源的书源"""
        all_sources = []

        # 创建HTTP会话
        connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # 1. GitHub 仓库
            print("=== 从 GitHub 仓库收集书源 ===")
            github_sources = await self.fetch_github_sources(session)
            all_sources.extend(github_sources)

            # 2. 书源网站
            print("\n=== 从书源网站收集书源 ===")
            website_sources = await self.fetch_website_sources(session)
            all_sources.extend(website_sources)

        # 3. 大型文件
        if large_files:
            print("\n=== 处理大型书源文件 ===")
            for file_path in large_files:
                file_sources = await self.process_large_file(file_path)
                all_sources.extend(file_sources)

        # 4. 标准化和去重
        print("\n=== 标准化和去重 ===")
        processed_sources = []

        for source in all_sources:
            normalized = self.normalize_source(source)
            if normalized and not self.is_duplicate(normalized):
                processed_sources.append(normalized)

        self.collected_sources = processed_sources
        return processed_sources

    def print_collection_stats(self):
        """打印收集统计"""
        print("\n=== 收集统计 ===")
        print(f"GitHub 仓库: {self.collection_stats['github']} 个")
        print(f"书源网站: {self.collection_stats['websites']} 个")
        print(f"大型文件: {self.collection_stats['files']} 个")
        print(f"重复过滤: {self.collection_stats['duplicates']} 个")
        print(f"最终收集: {len(self.collected_sources)} 个")

async def main():
    parser = argparse.ArgumentParser(description="外部书源收集器")
    parser.add_argument("--output", "-o", required=True, help="输出文件路径")
    parser.add_argument("--large-files", "-f", nargs="*", help="大型书源文件路径列表")
    parser.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT, help=f"超时时间（秒），默认 {DEFAULT_TIMEOUT}")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式：不保存文件")
    args = parser.parse_args()

    # 处理大型文件路径
    large_files = []
    if args.large_files:
        for file_path in args.large_files:
            path = Path(file_path)
            if path.exists():
                large_files.append(path)
            else:
                print(f"警告：文件不存在 {file_path}")

    print("开始收集外部书源...")
    print(f"超时设置：{args.timeout} 秒")
    print(f"大型文件：{len(large_files)} 个")
    print()

    # 执行收集
    collector = SourceCollector(args.timeout)
    sources = await collector.collect_all_sources(large_files)

    # 输出统计
    collector.print_collection_stats()

    # 保存结果
    if not args.dry_run and sources:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)

        print(f"\n收集的书源已保存到：{output_path}")
    elif args.dry_run:
        print("\n试运行模式：未保存文件")
    else:
        print("\n没有收集到有效书源")

    return 0

if __name__ == "__main__":
    exit(asyncio.run(main()))