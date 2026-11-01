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
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f7f6f2;color:#39372f;font:16px/1.9 "Segoe UI","Microsoft YaHei UI",system-ui,sans-serif}main{max-width:850px;margin:0 auto;padding:60px 36px 80px}h1,h2{font-family:"Songti SC","SimSun",serif;font-weight:400;color:#292823;line-height:1.5}h1{margin:24px 0 32px;font-size:34px}h2{margin:40px 0 15px;padding-top:25px;border-top:1px solid #dedcd4;font-size:24px}strong{font-weight:600;color:#292823}a{color:#39372f;text-underline-offset:4px}p{margin:14px 0}li{margin:10px 0}ul,ol{padding-left:25px}code{padding:3px 6px;background:#eeece5;border-radius:4px;font-size:14px}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:12px 14px;border-bottom:1px solid #dedcd4}th{font-weight:500;color:#292823;background:#eeece5}.table-wrap{overflow:auto;margin:20px 0}.note{padding:18px 22px;background:#eeece5;border-radius:8px}@media(max-width:640px){main{padding:28px 20px 50px}h1{font-size:28px}h2{font-size:22px}}
'''


def main():
    body = render((ROOT / 'docs/guide.md').read_text(encoding='utf-8'))
    page = '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>使用说明 · 尼康云创资料库</title><style>' + STYLE + '</style></head><body><main>' + body + '</main></body></html>\n'
    (ROOT / 'guide.html').write_text(page, encoding='utf-8')
    print('Generated guide.html')


if __name__ == '__main__':
    main()
