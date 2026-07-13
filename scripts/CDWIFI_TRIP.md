# ČD WiFi — trip sheet (vícezařízeňový režim)

Všechno spouštěj z **běžného Terminálu** (ne z Claude session — TCC sandbox
blokuje přepis souborů z minulých sessions).

## PŘED odjezdem (doma, teď)

### Tailscale (~10 min) — odemkne příští krok: koordinace přes NAS

NAS i Mac mají Tailscale nainstalovaný, jen odhlášený. Po přihlášení všech
zařízení jedním účtem bude audiobiblio na NASu dosažitelné i z vlaku
(vlaková WiFi má internet) — a příští jízda pojede přes serverovou frontu
místo ručních shardů.

```bash
# Mac:
tailscale up          # otevře prohlížeč, přihlas se (Google/GitHub/e-mail)

# NAS (stejný účet!):
ssh -t 314.slovacek@nasx 'sudo /var/packages/Tailscale/target/bin/tailscale up'

# Telefon: nainstaluj aplikaci Tailscale, přihlas se stejným účtem.

# Ověření + POZNAMENEJ SI NAS IP (100.x.y.z):
tailscale status
```

### Zbytek přípravy

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

# 0b) DATOVÝ BOD PRO KOORDINÁTOR: je NAS dosažitelný z vlaku přes Tailscale?
curl -s --max-time 8 http://<NAS-100.x-IP>:8321/api/v1/health && echo " ← NAS OK z vlaku!"
# Výsledek (OK/NE) si poznamenej — rozhoduje o architektuře příští fáze.

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
