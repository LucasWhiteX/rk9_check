"""Spoilers de cartas Pokémon TCG (JP + EN) -> Discord (webhook).

Fonte: feed RSS do PokeBeach, filtrado para notícias da página principal
sobre cartas reveladas. Guarda os itens já enviados em spoilers_state.json.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

FEED_URL = "https://www.pokebeach.com/forums/forum/-/index.rss"
STATE_FILE = Path(__file__).with_name("spoilers_state.json")
HEADERS = {"User-Agent": "tcg-spoiler-notifier/1.0 (personal use)"}
MAX_SEEN = 500

INCLUDE = re.compile(
    r"\breveal|\bcards?\b|set list|secret rare|illustration rare|full art|promo",
    re.I,
)
EXCLUDE = re.compile(r"preorder|accessor|binder|sleeve|McDonald", re.I)
INCLUDE_POCKET = os.environ.get("INCLUDE_POCKET") == "1"


def fetch_items() -> list[dict]:
    r = requests.get(FEED_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        cats = [c.text or "" for c in it.findall("category")]
        items.append({
            "guid": (it.findtext("guid") or it.findtext("link") or "").strip(),
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "categories": cats,
        })
    return items


def is_spoiler(item: dict) -> bool:
    if not any("front page news" in c.lower() for c in item["categories"]):
        return False
    t = item["title"]
    if not INCLUDE_POCKET and re.search(r"\bpocket\b", t, re.I):
        return False
    return bool(INCLUDE.search(t)) and not EXCLUDE.search(t)


def flag(title: str) -> str:
    if re.search(r"japan|japanese", title, re.I):
        return "🇯🇵"
    if re.search(r"english", title, re.I):
        return "🇬🇧"
    return "🃏"


def send(webhook: str, content: str, dry_run: bool) -> None:
    if dry_run:
        print("--- DISCORD ---\n" + content + "\n")
        return
    # Sem <> à volta do link, para o Discord mostrar a pré-visualização com imagem.
    r = requests.post(webhook, json={"content": content}, timeout=30)
    r.raise_for_status()


def main() -> int:
    webhook = os.environ.get("DISCORD_SPOILERS_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL", "")
    dry_run = not webhook or os.environ.get("DRY_RUN") == "1"

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"seen": []}
    first_run = not state["seen"]
    seen = set(state["seen"])

    items = fetch_items()
    new = [i for i in items if i["guid"] and i["guid"] not in seen]

    if first_run:
        send(webhook, "🤖 Monitor de spoilers ligado. Vou avisar aqui quando forem reveladas novas cartas (JP e EN).", dry_run)
    else:
        # O feed vem do mais recente para o mais antigo; envia por ordem cronológica.
        for item in reversed(new):
            if is_spoiler(item):
                send(webhook, f"{flag(item['title'])} **{item['title']}**\n{item['link']}", dry_run)

    state["seen"] = (state["seen"] + [i["guid"] for i in reversed(new)])[-MAX_SEEN:]
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    print(f"OK: {len(new)} itens novos no feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
