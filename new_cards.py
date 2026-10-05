"""Cartas novas (JP + EN) com imagem e texto, via API TCGdex -> Discord.

As cartas aparecem no TCGdex por volta do lançamento do set (não na revelação).
Guarda o estado em new_cards_state.json.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "https://api.tcgdex.net/v2"
LANGS = {"ja": ("🇯🇵", 0xE60012), "en": ("🇬🇧", 0x1F5FBF)}
STATE_FILE = Path(__file__).with_name("new_cards_state.json")
HEADERS = {"User-Agent": "tcg-new-cards-notifier/1.0 (personal use)"}
MAX_CARDS_PER_RUN = int(os.environ.get("MAX_CARDS_PER_RUN", "40"))
BASELINE_RECENT_SETS = 3        # na 1.ª execução, guarda as cartas dos 3 sets mais recentes
EMBEDS_PER_MESSAGE = 10         # limite do Discord
SKIP_SERIES = {"tcgp"}          # TCG Pocket

ENERGY = {
    "Grass": "🌿", "Fire": "🔥", "Water": "💧", "Lightning": "⚡", "Psychic": "🔮",
    "Fighting": "👊", "Darkness": "🌑", "Metal": "⚙️", "Fairy": "🧚", "Dragon": "🐉",
    "Colorless": "⚪",
}


def api(path: str):
    r = requests.get(f"{API}/{path}", headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def cost(types) -> str:
    return "".join(ENERGY.get(t, f"[{t}]") for t in (types or []))


def card_embed(card: dict, set_info: dict, lang: str) -> dict:
    flag, color = LANGS[lang]
    total = (set_info.get("cardCount") or {}).get("official") or "?"
    lines = []

    head = []
    if card.get("hp"):
        head.append(f"HP {card['hp']}")
    if card.get("types"):
        head.append(cost(card["types"]))
    if card.get("stage"):
        head.append(card["stage"])
    if card.get("evolveFrom"):
        head.append(f"evolui de {card['evolveFrom']}")
    if card.get("trainerType") or card.get("energyType"):
        head.append(card.get("trainerType") or card.get("energyType"))
    if head:
        lines.append(" · ".join(str(h) for h in head))

    for ab in card.get("abilities") or []:
        lines.append(f"\n**[{ab.get('type', 'Ability')}] {ab.get('name', '')}**\n{ab.get('effect', '')}")
    for at in card.get("attacks") or []:
        dmg = f" — **{at['damage']}**" if at.get("damage") not in (None, "") else ""
        lines.append(f"\n{cost(at.get('cost'))} **{at.get('name', '')}**{dmg}")
        if at.get("effect"):
            lines.append(at["effect"])
    if card.get("effect"):
        lines.append(f"\n{card['effect']}")

    tail = []
    if card.get("weaknesses"):
        tail.append("Fraqueza: " + ", ".join(f"{cost([w.get('type')])}{w.get('value', '')}" for w in card["weaknesses"]))
    if card.get("resistances"):
        tail.append("Resistência: " + ", ".join(f"{cost([w.get('type')])}{w.get('value', '')}" for w in card["resistances"]))
    if card.get("retreat") is not None:
        tail.append(f"Recuo: {card['retreat']}")
    if tail:
        lines.append("\n" + " · ".join(tail))

    footer = " · ".join(x for x in [
        card.get("rarity"),
        f"Ilustração: {card['illustrator']}" if card.get("illustrator") else None,
        f"Regulação {card['regulationMark']}" if card.get("regulationMark") else None,
    ] if x)

    embed = {
        "title": f"{flag} {card.get('name', '?')} · {set_info.get('name', '')} {card.get('localId', '')}/{total}",
        "description": "\n".join(lines)[:4000] or None,
        "color": color,
    }
    if card.get("image"):
        embed["image"] = {"url": f"{card['image']}/high.png"}
    if footer:
        embed["footer"] = {"text": footer}
    return {k: v for k, v in embed.items() if v is not None}


def post(webhook: str, payload: dict, dry_run: bool) -> None:
    if dry_run:
        print("--- DISCORD ---")
        print(payload.get("content", ""))
        for e in payload.get("embeds", []):
            print(f"[embed] {e['title']}\n{e.get('description', '')}\n(img {e.get('image', {}).get('url')})\n")
        return
    for _ in range(5):
        r = requests.post(webhook, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        time.sleep(1.5)
        return
    raise RuntimeError("Discord continua a limitar pedidos (429)")


def main() -> int:
    webhook = os.environ.get("DISCORD_SPOILERS_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL", "")
    dry_run = not webhook or os.environ.get("DRY_RUN") == "1"
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    budget = MAX_CARDS_PER_RUN

    for lang in LANGS:
        lstate = state.setdefault(lang, {"sets": {}})
        first_run = not lstate["sets"]
        sets = api(f"{lang}/sets") or []

        if first_run:
            for s in sets:
                lstate["sets"][s["id"]] = {"count": (s.get("cardCount") or {}).get("total", 0), "cards": None}
            for s in sets[-BASELINE_RECENT_SETS:]:
                detail = api(f"{lang}/sets/{s['id']}") or {}
                lstate["sets"][s["id"]]["cards"] = [c["id"] for c in detail.get("cards", [])]
            continue

        for s in sets:
            if budget <= 0:
                break
            count = (s.get("cardCount") or {}).get("total", 0)
            known = lstate["sets"].get(s["id"])
            if known and known.get("count") == count and known.get("cards") is not None and not known.get("pending"):
                continue

            detail = api(f"{lang}/sets/{s['id']}")
            if not detail:
                continue
            serie = (detail.get("serie") or {}).get("id", "")
            card_ids = [c["id"] for c in detail.get("cards", [])]

            if serie in SKIP_SERIES or (known and known.get("cards") is None):
                # TCG Pocket, ou set antigo sem lista guardada: regista em silêncio.
                lstate["sets"][s["id"]] = {"count": count, "cards": card_ids}
                continue

            seen = set(known["cards"]) if known else set()
            new_ids = [c for c in card_ids if c not in seen]
            if not known:
                flag = LANGS[lang][0]
                post(webhook, {"content": f"🆕 {flag} Set novo no TCGdex: **{detail.get('name', s['id'])}** ({len(card_ids)} cartas)"}, dry_run)

            sent = []
            batch = []
            for cid in new_ids:
                if budget <= 0:
                    break
                card = api(f"{lang}/cards/{cid}")
                if not card:
                    continue
                batch.append(card_embed(card, detail, lang))
                sent.append(cid)
                budget -= 1
                if len(batch) == EMBEDS_PER_MESSAGE:
                    post(webhook, {"embeds": batch}, dry_run)
                    batch = []
            if batch:
                post(webhook, {"embeds": batch}, dry_run)

            done = sorted(seen | set(sent))
            lstate["sets"][s["id"]] = {
                "count": count,
                "cards": done,
                "pending": len(sent) < len(new_ids),  # continua na próxima execução
            }

    if all(v.get("sets") for v in state.values()) and not STATE_FILE.exists():
        post(webhook, {"content": "🤖 Monitor de cartas novas (TCGdex) ligado. Vou mostrar aqui cada carta nova com imagem e texto, quando os sets forem adicionados (JP e EN)."}, dry_run)

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"OK: {MAX_CARDS_PER_RUN - budget} cartas enviadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
