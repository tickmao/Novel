#!/usr/bin/env python3
"""
书源清洗脚本
- 去除表情符号
- 去除括号及内容
- 转换特殊字符（圆圈数字、全角字符等）
- 规范名称和分组
- 清理多余空格
- 可选：按评分自动分组（精选/标准/备用）+ 排序
"""

import json
import re
import time
import argparse
import unicodedata
from pathlib import Path

# 表情符号正则（覆盖常见 emoji 范围）
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F100-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE
)

# 先移除装饰字符，再做 NFKC，避免 ㊣ 被转换成“正”
SPECIAL_SYMBOLS = re.compile(
    r'[★☆✦✧⭐🌟💫🔥💥✨🎉🎊📚📖📕📗📘📙👍👎👏🙏💪'
    r'❤️💕💖💗💙💚💛✅❌⭕❗❓'
    r'①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
    r'㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟'
    r'⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻'
    r'ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ～~丨|｜👁🔰🎨📻📥💠'
    r'◎▪™〽㊣●○◆◇■□▲△▼▽🈲'
    r']+'
)

# 括号及内容（中文括号、英文括号、方括号、尖括号）
BRACKET_PATTERN = re.compile(r'[（(【\[<〖{][^）)】\]>〗}]*[）)】\]>〗}]')

LEADING_NOISE = re.compile(r'^源社区出品[-:：]?\s*|^[+\-#.·~_=]+\s*')
LEADING_SINGLE_LETTER = re.compile(r'^[A-Za-z]\s+(?=[A-Za-z0-9\u4e00-\u9fff])')
TRAILING_AUTHOR_TAG = re.compile(r'\s*#\S+$')
TRAILING_DOMAIN = re.compile(r'[_\s-]*(?:[a-z0-9-]+\.)+[a-z]{2,}$', re.IGNORECASE)
TRAILING_VERSION = re.compile(r'[-_. ]+v?\d+(?:\.\d+){0,2}$', re.IGNORECASE)
TRAILING_CN_NUMBER = re.compile(r'(?<=[\u4e00-\u9fff])[-_. ]*\d{1,3}$')
TRAILING_SYMBOLS = re.compile(r'[._\-~·]+$')
MEANINGFUL_NAME = re.compile(r'[A-Za-z0-9\u4e00-\u9fff]')

DIRECT_SUFFIXES = [
    '自制', '备用', '自用', '待正文', '需要VIP',
    '手机版', 'TV版', '电脑版',
    '精品', '优质', '稳定版', '高速版', '纯净版',
    '旧版', '新版', '修复版', '优化版', '增强版', '精简版', '测试版',
    '完整版', '完全版', '最新版',
    '排行榜', '排行', '榜单', '分类', '发现', '下载',
    '完本', '全本', '全书',
    '共享API', 'Web共享API', 'web共享API', 'API',
    'app', 'APP',
]

# 分组排序顺序
GROUP_ORDER = {"精选": 0, "标准": 1, "备用": 2}

# 分组名称映射（原始 -> 标准）
GROUP_MAPPING = {
    "🌟 抓包": "抓包",
    "🎉 精选": "精选",
    "🔰 正版": "正版",
    "💠 综合": "综合",
    "📥 下载": "下载",
    "📚 出版": "出版",
    "🎨 漫画": "漫画",
    "📻 有声": "有声",
    "抓包": "抓包",
    "精选": "精选",
    "正版": "正版",
    "综合": "综合",
    "下载": "下载",
    "出版": "出版",
    "漫画": "漫画",
    "有声": "有声",
}


def calculate_quality_score(source: dict) -> int:
    """计算书源质量评分（满分约 60）"""
    score = 0

    # 基础状态 (0-7)
    if source.get('enabled', True):
        score += 5
    if source.get('enabledExplore'):
        score += 2

    # 响应时间 (0-15)
    rt = source.get('respondTime', 99999)
    if rt < 1000:
        score += 15
    elif rt < 3000:
        score += 12
    elif rt < 5000:
        score += 8
    elif rt < 10000:
        score += 4

    # 规则完整性 (0-20)
    if source.get('searchUrl'):
        score += 4
    if source.get('ruleSearch') or source.get('searchRule'):
        score += 4
    if source.get('ruleToc') or source.get('tocRule'):
        score += 4
    if source.get('ruleContent') or source.get('contentRule'):
        score += 6
    if source.get('exploreUrl'):
        score += 2

    # 更新时间 (0-10)
    last = source.get('lastUpdateTime', 0)
    if last:
        days = max(0, (time.time() * 1000 - last) / 86400000)
        if days < 30:
            score += 10
        elif days < 90:
            score += 7
        elif days < 180:
            score += 4
        elif days < 365:
            score += 2

    # 权重 (0-5)
    score += min(source.get('weight', 0) // 100, 5)

    return score


def get_grade_group(score: int) -> str:
    """根据评分返回分组名称"""
    if score >= 45:
        return "精选"
    elif score >= 40:
        return "标准"
    else:
        return "备用"


def clean_spaces(text: str) -> str:
    """清理空格"""
    if not text:
        return ""
    # 去除首尾空格
    text = text.strip()
    # 多个空格合并为一个
    text = re.sub(r'\s+', ' ', text)
    return text


def is_usable_name(text: str) -> bool:
    """判断名称是否仍然可用，避免清洗后变空。"""
    normalized = clean_spaces(text)
    return len(normalized) >= 2 and bool(MEANINGFUL_NAME.search(normalized))


def apply_if_usable(text: str, pattern: re.Pattern, replacement: str = "") -> str:
    """仅在替换后仍有可读名称时应用规则。"""
    candidate = clean_spaces(pattern.sub(replacement, text))
    return candidate if is_usable_name(candidate) else text


def strip_decorations(text: str) -> str:
    """移除装饰性内容，但尽量保留主体名称。"""
    if not text:
        return ""

    text = EMOJI_PATTERN.sub("", text)
    text = SPECIAL_SYMBOLS.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    text = BRACKET_PATTERN.sub("", text)
    text = clean_spaces(text)
    text = LEADING_NOISE.sub("", text)
    text = TRAILING_SYMBOLS.sub("", text)

    return clean_spaces(text)


def normalize_source_name(name: str) -> str:
    """
    清洗书源名称。

    目标是去掉装饰、署名、版本号、括号说明等噪音，但不把名称洗空。
    """
    original = clean_spaces(name)
    if not original:
        return ""

    text = strip_decorations(original)
    fallback = text or original

    for pattern in (
        LEADING_SINGLE_LETTER,
        TRAILING_AUTHOR_TAG,
        TRAILING_DOMAIN,
        TRAILING_VERSION,
        TRAILING_CN_NUMBER,
    ):
        text = apply_if_usable(text, pattern)

    if '/' in text:
        aliases = [clean_spaces(part) for part in re.split(r'[/｜|]', text) if clean_spaces(part)]
        if len(aliases) > 1 and is_usable_name(aliases[0]):
            text = aliases[0]

    for suffix in DIRECT_SUFFIXES:
        while text.endswith(suffix):
            candidate = clean_spaces(text[:-len(suffix)])
            if not is_usable_name(candidate):
                break
            text = candidate

    text = clean_spaces(TRAILING_SYMBOLS.sub("", text))
    return text if is_usable_name(text) else fallback


def normalize_group(group: str) -> str:
    """规范化分组名称"""
    if not group:
        return ""

    # 先尝试直接映射
    if group in GROUP_MAPPING:
        return GROUP_MAPPING[group]

    # 清洗后再映射
    cleaned = clean_spaces(strip_decorations(group))
    if cleaned in GROUP_MAPPING:
        return GROUP_MAPPING[cleaned]

    return cleaned


def clean_source(source: dict, grade: bool = False) -> dict:
    """清洗单个书源"""
    # 清洗名称
    if "bookSourceName" in source:
        source["bookSourceName"] = normalize_source_name(source["bookSourceName"])

    # 按评分分组（覆盖原有分组）
    if grade:
        score = calculate_quality_score(source)
        source["bookSourceGroup"] = get_grade_group(score)
    # 仅清洗分组
    elif "bookSourceGroup" in source:
        source["bookSourceGroup"] = normalize_group(source["bookSourceGroup"])

    # 清洗备注（保留内容，只去表情）
    if "bookSourceComment" in source and source["bookSourceComment"]:
        # 备注可能包含使用说明，只去除开头的表情
        comment = source["bookSourceComment"]
        # 只清理开头的表情符号
        comment = re.sub(r'^[\s]*' + EMOJI_PATTERN.pattern, '', comment)
        source["bookSourceComment"] = comment.strip()

    return source


def clean_sources(sources: list, grade: bool = False) -> list:
    """批量清洗书源"""
    return [clean_source(s, grade) for s in sources]


def sort_sources(sources: list) -> list:
    """按分组和名称排序"""
    return sorted(sources, key=lambda s: (
        GROUP_ORDER.get(s.get("bookSourceGroup", ""), 99),
        s.get("bookSourceName", "")
    ))


def main():
    parser = argparse.ArgumentParser(description="书源清洗脚本")
    parser.add_argument("--input", "-i", required=True, help="输入文件路径")
    parser.add_argument("--output", "-o", required=True, help="输出文件路径")
    parser.add_argument("--grade", "-g", action="store_true", help="按评分自动分组（精选/标准/备用）+ 排序")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"错误：输入文件不存在 {input_path}")
        return 1

    # 读取书源
    with open(input_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    print(f"读取书源：{len(sources)} 个")
    if args.grade:
        print("启用评分分组 + 排序模式")

    # 清洗
    cleaned = clean_sources(sources, grade=args.grade)

    # 排序（仅在 grade 模式下）
    if args.grade:
        cleaned = sort_sources(cleaned)

    # 输出
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    print(f"清洗完成，输出到：{output_path}")

    # 统计
    groups = {}
    for s in cleaned:
        g = s.get("bookSourceGroup", "未分组")
        groups[g] = groups.get(g, 0) + 1

    print("\n分组统计：")
    for g, count in sorted(groups.items(), key=lambda x: GROUP_ORDER.get(x[0], 99)):
        print(f"  {g or '未分组'}: {count}")

    return 0


if __name__ == "__main__":
    exit(main())
