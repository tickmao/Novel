"""A bounded, explicit subset of static Legado selectors."""

from __future__ import annotations

import json
import re

import regex
from jsonpath_ng.ext import parse as jsonpath
from lxml import etree, html


class UnsupportedRule(ValueError):
    pass


def check_static(value) -> None:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if re.search(r'@js:|<js>|java\.|javascript:|\{\{(?!\s*(?:key|page|searchKey|searchPage)\s*\}\})', text, re.I):
        raise UnsupportedRule('JavaScript or dynamic expression')


def document(text: str):
    if text.lstrip().startswith(('{', '[')):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return html.fromstring(text or '<html/>', parser=html.HTMLParser(no_network=True))


def visible_text(text: str) -> str:
    try:
        tree = html.fromstring(text, parser=html.HTMLParser(no_network=True))
        for item in tree.xpath('//script|//style|//noscript'):
            item.drop_tree()
        return tree.text_content()
    except (ValueError, etree.ParserError):
        return text


def _css(selector: str) -> tuple[str, int | None]:
    if any(token in selector for token in ('!', '##', '<js>', '@js:')):
        raise UnsupportedRule('Unsupported selector operator')
    index = None
    match = re.fullmatch(r'(class|tag|id)\.(.+?)(?:\.(-?\d+))?', selector)
    if match:
        kind, value, number = match.groups()
        index = int(number) if number else None
        if kind == 'class':
            selector = ''.join('.' + part for part in value.split())
        elif kind == 'id':
            selector = '#' + value
        else:
            selector = value
    return selector, index


def _select(context, expression: str) -> list:
    expression = expression.strip()
    if not expression:
        return [context]
    if isinstance(context, (dict, list)):
        if expression.startswith('@Json:'):
            expression = expression[6:]
        if isinstance(context, dict) and expression in context:
            result = context[expression]
            return result if isinstance(result, list) else [result]
        if not expression.startswith('$'):
            expression = '$.' + expression
        try:
            return [item for match in jsonpath(expression).find(context)
                    for item in (match.value if isinstance(match.value, list) else [match.value])]
        except Exception as exc:
            raise UnsupportedRule('Unsupported JSONPath') from exc
    if not isinstance(context, etree._Element):
        raise UnsupportedRule('Selector requires an element')
    if expression.startswith('@XPath:'):
        expression = expression[7:]
    if expression.startswith(('/', './', '(')):
        try:
            result = context.xpath(expression)
            return result if isinstance(result, list) else [result]
        except etree.XPathError as exc:
            raise UnsupportedRule('Unsupported XPath') from exc
    expression = expression.removeprefix('@css:')
    selector, index = _css(expression)
    try:
        found = context.cssselect(selector)
    except Exception as exc:
        raise UnsupportedRule('Unsupported CSS selector') from exc
    if index is not None:
        return [found[index]] if -len(found) <= index < len(found) else []
    return found


def nodes(context, rule: str) -> list:
    if not isinstance(rule, str) or len(rule) > 4096:
        raise UnsupportedRule('Invalid rule')
    check_static(rule)
    if '||' in rule:
        for part in rule.split('||'):
            result = nodes(context, part)
            if result:
                return result
        return []
    if '&&' in rule:
        return [item for part in rule.split('&&') for item in nodes(context, part)]
    if rule.startswith(('@XPath:', '@Json:', '$', '/', './', '(')):
        return _select(context, rule)
    parts = rule.removeprefix('@css:').split('@')
    result = [context]
    for part in parts:
        if part:
            result = [child for parent in result for child in _select(parent, part)]
    return result


def _string(value) -> str:
    if isinstance(value, etree._Element):
        return ''.join(value.itertext()).strip()
    return str(value).strip() if value is not None else ''


def replace_text(value: str, rule: str) -> str:
    check_static(rule)
    parts = rule.split('##')
    if len(parts) < 2:
        raise UnsupportedRule('Expected a regex replacement')
    pattern, replacement = parts[0], parts[1]
    replacement = re.sub(r'\$(\d+)', r'\\g<\1>', replacement)
    try:
        return regex.sub(pattern, replacement, value, timeout=0.2)
    except (TimeoutError, regex.error) as exc:
        raise UnsupportedRule('Invalid or slow replacement') from exc


def text(context, rule: str) -> str:
    check_static(rule)
    if '||' in rule:
        return next((value for part in rule.split('||') if (value := text(context, part))), '')
    if '&&' in rule:
        return '\n'.join(filter(None, (text(context, part) for part in rule.split('&&'))))
    pieces = rule.split('##')
    rule = pieces[0].strip()
    op = None
    if rule in ('text', 'textNodes', 'html', 'ownText', 'all'):
        selected, op = [context], rule
    elif '@' in rule and not rule.startswith(('@XPath:', '@Json:', '$', '/', './', '(')):
        rule = rule.removeprefix('@css:')
        selector, op = rule.rsplit('@', 1)
        selected = nodes(context, selector)
    else:
        selected = nodes(context, rule)
    values = []
    for item in selected:
        if op in ('html', 'all') and isinstance(item, etree._Element):
            values.append(etree.tostring(item, encoding='unicode', method='html'))
        elif op in ('ownText', 'textNodes') and isinstance(item, etree._Element):
            values.append('\n'.join(item.xpath('text()')))
        elif op and op != 'text' and isinstance(item, etree._Element):
            values.append(item.get(op, ''))
        else:
            values.append(_string(item))
    result = '\n'.join(filter(None, values)).strip()
    if len(pieces) > 1:
        result = replace_text(result, '##'.join(pieces[1:]) + ('##' if len(pieces) == 2 else ''))
    return result
