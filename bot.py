import json
import os
import re
import urllib.parse
from pathlib import Path

import feedparser
import requests

STATE_FILE = Path('seen.json')
BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

QUERIES = [
    '"WoW Forever" "Horde" "unlocked"',
    '"WoW Forever" "Horde" "character creation"',
    '"WoW Forever" "Horde" "open"',
]

FEEDS = [
    'https://www.reddit.com/search.rss?' + urllib.parse.urlencode({'q': '"WoW Forever" "Horde"', 'sort': 'new', 't': 'week'}),
    *[
        'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': q, 'hl': 'en', 'gl': 'US', 'ceid': 'US:en'})
        for q in QUERIES
    ],
]


POSITIVE = re.compile(
    r'\b(unlocked|unlocking|unlocked again|open again|reopened|re-opened|available again|creation enabled|'
    r'freigeschaltet|freischaltung|wieder offen|wieder geöffnet|wieder verfügbar|entsperrt|'
    r'charaktererstellung möglich|charaktererstellung erlaubt|sperre aufgehoben|'
    r'fraktionssperre aufgehoben|horde wieder offen)\b',
    re.I
)

HORDE = re.compile(
    r'\b(horde|hordenseite|horde-fraktion)\b',
    re.I
)

CONTEXT = re.compile(
    r'\b(forever|eu|pvp|pvp-server|character creation|char creation|'
    r'charaktererstellung|charakter erstellen|fraktionssperre|fraktionsbeschränkung|'
    r'faction lock|faction restriction)\b',
    re.I
)

NEGATIVE = re.compile(
    r'\b(not unlocked|still locked|is locked|remains locked|nicht freigeschaltet|'
    r'weiterhin gesperrt|noch gesperrt|bleibt gesperrt|nicht möglich|'
    r'freischaltung noch nicht|sperre bleibt bestehen)\b',
    re.I
)

def check_blizzard_latest(previous, current, matches):
    url = 'https://eu.forums.blizzard.com/en/wow/latest.json'

    response = requests.get(
        url,
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()
    topics = data.get('topic_list', {}).get('topics', [])

    for topic in topics:
        topic_id = f"blizzard-topic:{topic['id']}"
        if topic_id in current:
            continue

        title = topic.get('title', '')
        if HORDE.search(title) and CONTEXT.search(title) and POSITIVE.search(title) and not NEGATIVE.search(title):
            link = f"https://eu.forums.blizzard.com/en/wow/t/{topic['id']}"
            
            if topic_id not in previous:
                try:
                    detail = requests.get(
                        f"https://eu.forums.blizzard.com/en/wow/t/{topic['id']}.json",
                        headers={'User-Agent': 'Mozilla/5.0'},
                        timeout=20,
                    )
                    detail.raise_for_status()
                    posts = detail.json().get('post_stream', {}).get('posts', [])
                    content = re.sub('<[^>]*>', ' ', posts[0].get('cooked', '')) if posts else title
                except Exception as exc:
                    print(f'Beitragstext konnte nicht geladen werden: {exc}')
                    content = title

                matches.append((f'Blizzard-Forum:\n\n{content[:3000]}', link))


        current.add(topic_id)

    print(f'Blizzard neue Themen geprüft: {len(topics)}')

def check_blizzard_forum(previous, current, matches):
    url = 'https://eu.forums.blizzard.com/en/wow/t/639733.json'

    response = requests.get(
        url,
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()
    posts = data.get('post_stream', {}).get('posts', [])

    for post in posts:
        post_id = f"blizzard:{post['id']}"
        if post_id in current:
            continue

        content = re.sub('<[^>]*>', ' ', post.get('cooked', ''))
        title = data.get('title', 'WoW Forever Horde')
        text = f'{title} {content}'

        if (
            HORDE.search(text)
            and POSITIVE.search(text)
            and not NEGATIVE.search(text)
        ):
            link = (
                'https://eu.forums.blizzard.com/en/wow/t/639733/'
                f"{post['post_number']}"
            )
            if post_id not in previous:
                matches.append((f'Blizzard-Forum:\n\n{content[:3000]}', link))

        current.add(post_id)


def send(message):
    response = requests.post(
        f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
        json={'chat_id': CHAT_ID, 'text': message, 'disable_web_page_preview': True},
        timeout=20,
    )
    response.raise_for_status()


def main():
    previous = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    first_run = not STATE_FILE.exists()
    current = set(previous)
    matches = []
    successes = 0

    for url in FEEDS:
        try:
            response = requests.get(url, headers={'User-Agent': 'WoWHordeAlert/1.0 (personal RSS monitor)'}, timeout=20)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed.bozo and not feed.entries:
                raise ValueError('Invalid RSS response')
            successes += 1
            for entry in feed.entries[:30]:
                link = entry.get('link', '')
                if not link:
                    continue
                title = entry.get('title', '')
                summary = re.sub('<[^>]*>', ' ', entry.get('summary', ''))
                text = f'{title} {summary}'
                if link not in current and HORDE.search(text) and CONTEXT.search(text) and POSITIVE.search(text) and not NEGATIVE.search(text):
                    matches.append((title, link))
                current.add(link)
        except Exception as exc:
            print(f'Feed failed: {url}: {exc}')
    
    try:
        check_blizzard_forum(previous, current, matches)
        successes += 1
        print('Blizzard-Forum erfolgreich geprüft.')
    except Exception as exc:
        print(f'Blizzard-Forum fehlgeschlagen: {exc}')

    
    try:
        check_blizzard_latest(previous, current, matches)
        successes += 1
        print('Blizzard-Forum: Neue Themen erfolgreich geprüft.')
    except Exception as exc:
        print(f'Blizzard-Forum: Neue Themen fehlgeschlagen: {exc}')

    
    try:
        check_blizzard_us(previous, current, matches)
        successes += 1
        print('US-Blizzard-Thread erfolgreich geprüft.')
    except Exception as exc:
        print(f'US-Blizzard-Thread fehlgeschlagen: {exc}')


    if successes == 0:
        raise RuntimeError('All feeds failed; keeping previous state')

    if first_run:
        print('First run: baseline saved, no old alerts sent')
    else:
        for title, link in matches[:10]:
            if title.startswith('🔵 OFFIZIELLES BLIZZARD-UPDATE'):
                send(
                    f'{title}\n\n{link}\n\n'
                    'Offizielle Mitteilung – keine bestätigte Horde-Freischaltung.'
                )
            else:
                send(
                    f'🚨 MÖGLICHER HORDE-UNLOCK!\n\n'
                    f'{title}\n{link}\n\n'
                    'Bitte direkt im Spiel prüfen – nicht bestätigt.'
                )

    # Limit state growth; old links may eventually be re-alerted if they reappear.
    STATE_FILE.write_text(json.dumps(sorted(current)[-3000:], indent=2) + '\n')
    print(f'Feeds OK: {successes}/{len(FEEDS)}, new matches: {len(matches)}, first run: {first_run}')


if __name__ == '__main__':
    if os.environ.get('HORDE_TEST') == '1':
        test_text = 'WoW Forever EU PvP Horde character creation unlocked'

        if (
            HORDE.search(test_text)
            and CONTEXT.search(test_text)
            and POSITIVE.search(test_text)
            and not NEGATIVE.search(test_text)
        ):
            send('🧪 TESTALARM: Horde-Freischaltung erkannt!\n\nDies ist nur ein Test. Keine echte Freischaltung.')
            print('Horde-Erkennungstest erfolgreich.')
        else:
            raise RuntimeError('Horde-Erkennungstest fehlgeschlagen.')
    else:
            main()
