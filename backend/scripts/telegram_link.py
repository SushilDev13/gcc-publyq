"""Find the group(s) @Publyqbot is in and (optionally) save the right chat ID.

Telegram only reveals a group's chat ID through updates the bot receives.
After adding the bot to the group, send `/start@Publyqbot` there, then:

    .venv/bin/python -m backend.scripts.telegram_link            # list groups the bot can see
    .venv/bin/python -m backend.scripts.telegram_link --write    # save the single group's ID to .env

Only GROUP_CHAT_ID in .env is changed; the token is never printed.
"""
import argparse
import re
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    env = dotenv_values(ROOT / '.env')
    tok = (env.get('BOT_TOKEN') or '').strip()
    if not tok:
        print('BOT_TOKEN missing in .env')
        return 2
    base = f'https://api.telegram.org/bot{tok}'
    me = httpx.get(f'{base}/getMe', timeout=15).json()
    if not me.get('ok'):
        print('Bot token rejected by Telegram.')
        return 2
    upd = httpx.get(f'{base}/getUpdates', params={'limit': 100}, timeout=15).json().get('result', [])
    groups: dict[int, dict] = {}
    for u in upd:
        for key in ('message', 'my_chat_member', 'channel_post'):
            m = u.get(key) or {}
            ch = m.get('chat') or {}
            if ch.get('type') in ('group', 'supergroup'):
                g = groups.setdefault(ch['id'], {'title': ch.get('title'), 'type': ch['type'], 'status': None})
                if key == 'my_chat_member':
                    g['status'] = (m.get('new_chat_member') or {}).get('status')
            if m.get('migrate_to_chat_id'):      # group upgraded to supergroup: the old ID stops working
                groups.pop(ch.get('id'), None)
                groups.setdefault(m['migrate_to_chat_id'], {'title': ch.get('title'), 'type': 'supergroup', 'status': None})
    # confirm current membership for each candidate
    for cid, g in groups.items():
        r = httpx.get(f'{base}/getChatMember', params={'chat_id': cid, 'user_id': me['result']['id']}, timeout=15).json()
        g['status'] = (r.get('result') or {}).get('status', g['status'] or 'unknown')
    live = {cid: g for cid, g in groups.items() if g['status'] in ('member', 'administrator', 'creator')}
    print(f"@{me['result']['username']} can see {len(live)} group(s):")
    for cid, g in live.items():
        print(f"  {cid}  {g['title']!r}  ({g['type']}, bot is {g['status']})")
    if not live:
        print('None yet. In Telegram: open the group -> Add members -> @' + me['result']['username'] +
              ', then send /start@' + me['result']['username'] + ' in the group, and run this again.')
        return 1
    if a.write:
        if len(live) != 1:
            print('More than one group; edit GROUP_CHAT_ID in .env by hand with the ID you want.')
            return 1
        cid = next(iter(live))
        p = ROOT / '.env'
        text = p.read_text()
        line = f'GROUP_CHAT_ID={cid}'
        text = re.sub(r'(?m)^GROUP_CHAT_ID=.*$', line, text) if re.search(r'(?m)^GROUP_CHAT_ID=', text) \
            else text.rstrip('\n') + '\n' + line + '\n'
        p.write_text(text)
        print(f'Saved GROUP_CHAT_ID for {live[cid]["title"]!r}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
