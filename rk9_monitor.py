"""Monitor de inscrições TCG no RK9 -> Discord (webhook).

Corre a cada execução do GitHub Actions. Guarda o estado em state.json
para não repetir avisos.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://rk9.gg"
EVENTS_URL = f"{BASE}/events/pokemon"
STATE_FILE = Path(__file__).with_name("state.json")
LISBON = ZoneInfo("Europe/Lisbon")
REMINDER_MINUTES = 15
HEADERS = {"User-Agent": "rk9-tcg-notifier/1.0 (personal use)"}

EUROPE = {
    "PT", "ES", "FR", "DE", "IT", "NL", "BE", "LU", "UK", "GB", "IE", "PL", "CZ",
    "SK", "AT", "CH", "DK", "SE", "NO", "FI", "IS", "HU", "RO", "BG", "GR", "HR",
    "SI", "RS", "EE", "LV", "LT", "MT", "CY",
}

TZ_MAP = {
    "CEST": "Europe/Paris", "CET": "Europe/Paris",
    "BST": "Europe/London", "GMT": "Europe/London",
    "WEST": "Europe/Lisbon", "WET": "Europe/Lisbon",
    "EDT": "America/New_York", "EST": "America/New_York",
    "CDT": "America/Chicago", "CST": "America/Chicago",
    "PDT": "America/Los_Angeles", "PST": "America/Los_Angeles",
    "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney",
    "BRT": "America/Sao_Paulo", "UTC": "UTC",
}

OPENS_RE = re.compile(
    r"Registration opens\s+([A-Za-z]+\s+\d{1,2})(?:,?\s*(\d{4}))?\s+at\s+(\d{1,2}:\d{2})\s*([A-Z]{2,5})?",
    re.I,
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def in_scope(name: str, location: str) -> bool:
    n = name.lower()
    if "professor" in n:
        return False
    if "world championships" in n:
        return True
    if "international championships" in n:
        return "europe" in n
    if "regional championships" in n or "special championships" in n:
        code = location.strip().split(",")[-1].strip().upper()
        return code in EUROPE
    return False


def parse_opens(text: str, ref: datetime):
    """Converte 'Registration opens September 30 at 19:00 CEST' em datetime UTC."""
    m = OPENS_RE.search(text)
    if not m:
        return None
    day, year, hhmm, tz = m.groups()
    tzinfo = ZoneInfo(TZ_MAP.get((tz or "CEST").upper(), "Europe/Paris"))
    yr = int(year) if year else ref.year
    try:
        dt = datetime.strptime(f"{day} {yr} {hhmm}", "%B %d %Y %H:%M")
    except ValueError:
        dt = datetime.strptime(f"{day} {yr} {hhmm}", "%b %d %Y %H:%M")
    dt = dt.replace(tzinfo=tzinfo).astimezone(timezone.utc)
    # Sem ano explícito: se ficou muito no passado, é do ano seguinte.
    if not year and dt < ref - timedelta(days=180):
        dt = dt.replace(year=dt.year + 1)
    return dt


def parse_events(html: str, ref: datetime) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(lambda t: t.name in ("h4", "h3", "h5") and "Upcoming" in t.get_text())
    table = heading.find_next("table") if heading else soup.find("table")
    if table is None:
        raise RuntimeError("Tabela de eventos não encontrada no RK9 (estrutura mudou?)")

    events = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        event_link = row.find("a", href=re.compile(r"/event/"))
        if not event_link:
            continue
        slug = event_link["href"].rstrip("/").split("/event/")[-1]
        name = event_link.get_text(" ", strip=True)
        dates = cells[0].get_text(" ", strip=True)
        location = cells[-2].get_text(" ", strip=True)
        event_cell_text = event_link.find_parent("td").get_text(" ", strip=True)

        tcg_url = None
        for a in cells[-1].find_all("a", href=re.compile(r"/tournament/")):
            if "TCG" in a.get_text(" ", strip=True):
                tcg_url = requests.compat.urljoin(BASE, a["href"])

        events.append({
            "slug": slug,
            "name": name,
            "dates": dates,
            "location": location,
            "event_url": f"{BASE}/event/{slug}",
            "tcg_url": tcg_url,
            "opens_at": parse_opens(event_cell_text, ref),
        })
    return events


def registration_open(tournament_html: str) -> bool:
    """Heurística: a página do torneio mostra 'Go to registration' quando está aberto."""
    text = BeautifulSoup(tournament_html, "html.parser").get_text(" ", strip=True).lower()
    if "registration is closed" in text or "registration closed" in text:
        return False
    return "go to registration" in text


def lisbon(dt: datetime) -> str:
    return dt.astimezone(LISBON).strftime("%d/%m às %H:%M (Lisboa)")


def send(webhook: str, content: str, dry_run: bool) -> None:
    mention = os.environ.get("DISCORD_MENTION", "").strip()
    if mention:
        content = f"{mention} {content}"
    if dry_run:
        print("--- DISCORD ---\n" + content + "\n")
        return
    r = requests.post(webhook, json={"content": content}, timeout=30)
    r.raise_for_status()


def links(ev: dict) -> str:
    out = f"Evento: <{ev['event_url']}>"
    if ev["tcg_url"]:
        tid = ev["tcg_url"].rstrip("/").split("/tournament/")[-1]
        out += f"\nTCG: <{ev['tcg_url']}>"
        out += f"\n👉 Inscrição: <{BASE}/register/{tid}>"
    return out


def main() -> int:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    dry_run = not webhook or os.environ.get("DRY_RUN") == "1"
    ref = now_utc()

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    first_run = not state

    events = [e for e in parse_events(fetch(EVENTS_URL), ref) if in_scope(e["name"], e["location"])]

    for ev in events:
        s = state.setdefault(ev["slug"], {})
        head = f"**{ev['name']}** · {ev['dates']} · {ev['location']}"

        # 1) Evento novo
        if not s.get("announced"):
            s["announced"] = True
            if not first_run:
                send(webhook, f"🆕 Novo evento no RK9\n{head}\n{links(ev)}", dry_run)

        # 2) Hora de abertura anunciada (ou alterada)
        if ev["opens_at"]:
            iso = ev["opens_at"].isoformat()
            if s.get("opens_at") != iso:
                s["opens_at"] = iso
                s.pop("reminder_sent", None)
                if ev["opens_at"] > ref and not first_run:
                    send(webhook, f"📅 Inscrições TCG abrem {lisbon(ev['opens_at'])}\n{head}\n{links(ev)}", dry_run)

            # 3) Lembrete 15 min antes
            start = ev["opens_at"] - timedelta(minutes=REMINDER_MINUTES)
            if start <= ref < ev["opens_at"] and not s.get("reminder_sent"):
                s["reminder_sent"] = True
                send(webhook, f"⏰ Inscrições TCG abrem daqui a {REMINDER_MINUTES} min: {lisbon(ev['opens_at'])}\n{head}\n{links(ev)}\nConfirma que já aceitaste o Competitor Agreement.", dry_run)

        # 4) Inscrições abertas
        if ev["tcg_url"] and not s.get("open_sent"):
            if ev["opens_at"] and ev["opens_at"] > ref:
                continue  # ainda não chegou a hora anunciada
            try:
                is_open = registration_open(fetch(ev["tcg_url"]))
            except requests.RequestException as exc:
                print(f"Aviso: falhou {ev['tcg_url']}: {exc}", file=sys.stderr)
                continue
            if is_open:
                s["open_sent"] = True
                if not first_run:
                    send(webhook, f"✅ Inscrições TCG ABERTAS\n{head}\n{links(ev)}\nConfirma que já aceitaste o Competitor Agreement.", dry_run)

    if first_run:
        lines = []
        for ev in events:
            s = state[ev["slug"]]
            status = "aberto" if s.get("open_sent") else (
                f"abre {lisbon(ev['opens_at'])}" if ev["opens_at"] else "sem data")
            lines.append(f"• {ev['name']} ({ev['dates']}): {status}")
        send(webhook, "🤖 Monitor RK9 ligado. Estado atual (TCG):\n" + ("\n".join(lines) or "Nenhum evento no âmbito."), dry_run)

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"OK: {len(events)} eventos no âmbito.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
