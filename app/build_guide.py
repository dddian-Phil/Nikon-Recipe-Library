"""Render the project's simple Markdown guide as a standalone reading page."""
from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def inline(text):
    text = escape(text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)


def render(source):
    parts = []
    list_type = None
    table = False
    for line in source.splitlines() + ['']:
        line = line.strip()
        is_table = line.startswith('|')
        match = re.match(r'^(\d+\. |[-*] )(.*)', line)
        next_list = ('ol' if match.group(1)[0].isdigit() else 'ul') if match else None
        if list_type and next_list != list_type:
            parts.append(f'</{list_type}>')
            list_type = None
        if table and not is_table:
            parts.append('</tbody></table></div>')
            table = False
        if not line:
            continue
        if is_table:
            if re.fullmatch(r'[|\s:\-]+', line):
                continue
            cells = line.strip('|').split('|')
            tag = 'td' if table else 'th'
            row = '<tr>' + ''.join(f'<{tag}>' + inline(cell.strip()) + f'</{tag}>' for cell in cells) + '</tr>'
            if not table:
                parts.append('<div class="table-wrap"><table><thead>' + row + '</thead><tbody>')
                table = True
            else:
                parts.append(row)
        elif match:
            if not list_type:
                list_type = next_list
                parts.append(f'<{list_type}>')
            parts.append('<li>' + inline(match.group(2)) + '</li>')
        else:
            heading = re.match(r'^(#{1,6})\s+(.+)', line)
            if heading:
                level = len(heading.group(1))
                parts.append(f'<h{level}>' + inline(heading.group(2)) + f'</h{level}>')
            else:
                parts.append('<p>' + inline(line) + '</p>')
    return '\n'.join(parts)


STYLE = '''
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#191919;color:#dedede;font:16px/1.85 system-ui,"Microsoft YaHei",sans-serif}main{max-width:880px;margin:36px auto;padding:32px 44px 48px;background:#242424;border:1px solid #383838;border-radius:12px}h1{margin:0 0 24px;color:#fff;font-size:30px;line-height:1.4}h2{margin:34px 0 12px;padding-top:20px;border-top:1px solid #404040;color:#fff;font-size:21px;line-height:1.5}strong{color:#fff}p{margin:12px 0}li{margin:8px 0}ul,ol{padding-left:25px}code{padding:3px 6px;background:#333;border-radius:4px;color:#fff}table{width:100%;border-collapse:collapse;font-size:15px}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #414141}th{background:#333;color:#fff}.table-wrap{overflow:auto;margin:18px 0}@media(max-width:640px){main{margin:0;padding:24px 20px;border:0;border-radius:0}h1{font-size:26px}}
'''


def main():
    body = render((ROOT / 'docs/guide.md').read_text(encoding='utf-8'))
    page = '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>使用说明 · 尼康云创资料库</title><style>' + STYLE + '</style></head><body><main>' + body + '</main></body></html>\n'
    (ROOT / 'guide.html').write_text(page, encoding='utf-8')
    print('Generated guide.html')


if __name__ == '__main__':
    main()
