# 2026-11-08 Ranní kontrola nočního stahování

## Zjištění
- Noc 7.→8. 11. (od 19:00 CET): 3 868 SUCCESS, 168 ERROR, 371 SKIPPED.
- Pokles tempa po 01:00 (450–700/h → 35–100/h) nebyl zásek: fronta přešla
  z krátkých dílů (3–6 min) na ~50min díly; minuty audia za hodinu zhruba stejné.
  Od 05:00 platí denní limit (~60/h) — záměr.
- Live recorder: Wiesner I a II COMPLETE (coverage 0,99 a 1,0), repríza II
  správně SKIPPED ("recorded by #2").
- 4 joby visely v RUNNING ~24 h: start těsně před deployi 7. 11.; restart
  kontejneru zabil stahování a nic stav neuvolnilo.
- 86 chyb yt-dlp = pořad „Básnění“ (Vltava): stránka díly vypisuje, mp3 vrací 404.
  Chyba v DB byla jen "exit status 1" — stderr yt-dlp se zahazoval.
- 79 chyb "404 Client Error" na mujrozhlas — obsah stažen.

## Důsledky a opravy (commit fix(downloader) 2026-11-08)
- Chyba yt-dlp nově nese posledních 5 řádků stderr.
- 404/410 na médiu → WATCH s markerem `[media-gone]`; watch list takový job
  zkusí znovu až po 30 dnech. Důvod: stránka zůstává 200, takže sonda stránky
  by vytvořila smyčku stáhnout → 404 → WATCH → PENDING.
- Start aplikace vrací RUNNING → PENDING (`reset_orphaned_running`).
- Ručně: 4 zombie joby → PENDING; 97 explicitních 404 + 81 Básnění ERROR → WATCH.

## Poučení
- Pokles počtu stažených za hodinu posuzovat podle minut audia, ne počtu dílů.
- Každé nasazení uprostřed stahování dřív nechávalo zombie — teď řeší start.

## Doplněk: „To nejlepší k poslechu“ (Brno) mezi prioritní pořady
- Na pokyn vlastníka: target 445 https://brno.rozhlas.cz/nejlepsi-k-poslechu-9202195
  REVIEW/NEWS-TALK → AUTO, content_class BOOK, priority_base 80 (jako Toulky, Osudy).
- 248 epizod zvednuto na prioritu 80; 642 jobů APPROVAL → PENDING (214 audio + meta/web).
- Pořad je mix (rozhlasové hry, četba, vzpomínky, reportáže o hasičích); stahuje se celý.

## Doplněk 2: metadata „Hvězdná myš“ (výtky vlastníka 2026-11-09)
- ABS čte metadata.json: title = celý nadpis stránky, publishedYear = rok
  nahrávky 2026 (z publisheru), description jen s 1. odstavcem těla článku.
- Příčiny: html_scraper bral jen první odstavec; build_abs_metadata bral
  work.title bez ořezu „Autor: Titul. Podtitul“ (složka ho ořezávala).
- Opravy: scraper bere všechny odstavce div.field.body; metadata.json title
  ořez stejným pravidlem jako složka.
- Data: rok 1942 (první vydání The Star Mouse, od vlastníka) jako MANUAL,
  popis z vlastníkovy ruční úpravy souboru jako MANUAL; tag date 2026→1942;
  vlastníkův title/album v souboru zachovány (sync je hlásí jen jako FILE).
- Poučení: rok prvního vydání u četby stránka neuvádí — potřeba obohacení
  (databazeknih / wiki), jinak padá na rok nahrávky.
