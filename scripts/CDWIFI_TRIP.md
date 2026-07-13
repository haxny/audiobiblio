# ČD WiFi — trip sheet (vícezařízeňový režim)

Všechno spouštěj z **běžného Terminálu** (ne z Claude session — TCC sandbox
blokuje přepis souborů z minulých sessions).

## PŘED odjezdem (doma, teď)

```bash
# 1) Smazat zamčený partial (uvolní track 5 Mlhy Olandu k novému stažení):
rm "/Users/jirislovacek/Downloads/audiobiblio/cd.cz/audiobooks/Johan Theorin - Mlhy Olandu/05 - 05 Kapitola 3.mp3"

# 2) Poslat Termux runner do telefonu (AirDrop / kabel):
#    soubor: ~/projects/audiobiblio/scripts/cdwifi_termux_dl.py  →  Termux ~
#    v Termuxu jednorázově: pkg install python curl
```

## VE VLAKU

```bash
cd ~/projects/audiobiblio && source .venv/bin/activate
OUT=/Users/jirislovacek/Downloads/audiobiblio/cd.cz

# 0) Portál? (DNS často nefunguje — vždy IP)
curl -sk --max-time 5 -o /dev/null -w 'HTTP %{http_code}\n' https://10.0.0.60/portal/api/audiobook
# 404/nic = vlak portál nemá; nediagnostikovat, počkat na jiný vlak.

# 1) MANIFEST NEJDŘÍV (HEAD-proby se nesmí prát s běžícím stahováním!)
python3 scripts/cdwifi_backup.py --base-url https://10.0.0.60 \
    --audiobooks --scan-audiobooks 130:200 \
    --manifest-out "$OUT/manifest_today.json" --output-dir "$OUT"

# 2) Manifest do telefonu (AirDrop manifest_today.json)

# 3) Mac = lichý shard (běží na pozadí, log v cd.cz/logs):
nohup python3 -u scripts/cdwifi_backup.py --base-url https://10.0.0.60 \
    --manifest-in "$OUT/manifest_today.json" --shard 1/2 --order at-risk \
    --output-dir "$OUT" > "$OUT/logs/mac_$(date +%H%M).log" 2>&1 &

# 4) Telefon (Termux) = sudý shard:
#    python3 cdwifi_termux_dl.py --manifest manifest_today.json \
#        --output ~/storage/downloads/cdwifi --shard 2/2

# Monitor (Mac):
/usr/bin/grep -cE '^    Done \(' "$OUT"/logs/mac_*.log
```

Pravidla z minulých jízd: jeden stream na zařízení je nejrychlejší;
partials se dokončují automaticky (`curl -C -`); katalog se mezi vlaky
točí — manifest řadí „at-risk" tituly první.

**Priority (stav po 2026-06-29):** Smrt v temnotách (157, zbývá 6 stop) ·
Mlhy Olandu (161, track 5) · Pán hor I (165) · Čarodějky malostranské (164) ·
Píseň oceli (160) · Rytíři z Vřesova (163) — pak hudba, pak video.

## PO jízdě (doma)

```bash
# Stáhnout výsledky z telefonu (adb / rsync / AirDrop) do $OUT/audiobooks/…
# a zapsat je do DB:
python3 scripts/cdwifi_backup.py --reconcile-on-disk "$OUT"
```
