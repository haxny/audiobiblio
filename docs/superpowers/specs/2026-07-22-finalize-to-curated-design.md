# Finalize-to-curated — hotové knihy do stávajícího systému

Cíl (uživatel, 2026-07-22): kompletně stažené a otagované knihy/seriály se
přesunou PŘÍMO do kurátorské knihovny — správné adresáře v
`/eBOOKs.fiction` a `/eBOOKs.nonfiction`.

## Mapování destinací (per program)

Konfigurace `finalize_destinations` (config.yaml), klíč = normalizované
jméno programu (viz `_norm_program_name`):

| Program | Destinace | Layout |
|---|---|---|
| Četba na pokračování, Radiokniha, Četba s hvězdičkou, Hra na neděli | `/media/fiction` | `book` |
| Historie českého zločinu, Stopy fakta tajemství | `/media/nonfiction/history [audio]` | `collection` |
| (nenamapovaný program) | žádný auto-přesun — jen dosavadní finalize do library | — |

## Layouty a pojmenování (VŠE unidecoded — závazné pravidlo)

**book** (fiction): `{Autor} [audio]/{Autor} - ({rok}) {Titul} (cte {Interpret}, {kanal} {rok_nahravky})/NN - {titul dilu}.ext`
- Autor z work.author; rok = rok ORIGINÁLU (work.year); Interpret z provenance
  narrator; kanál ze station.code (CRo2…); rok_nahravky z published_at prvního dílu.
- Chybí-li autor/interpret → kniha se NEfinalizuje automaticky, jde do
  Inboxu jako „čeká na metadata" (žádné poloprázdné názvy).

**collection** (nonfiction pořady): `{Program} ({kanal})/{Titul dilu}.ext`
- plochá struktura po vzoru stávající SFT složky.

## Kdy je kniha „hotová" (auto-finalize job, denně)

1. každý indexovaný díl má COMPLETE audio, A
2. `expected_total` je splněn, NEBO poslední díl vyšel před ≥ 14 dny
   (kniha dovysílala), A
3. sync-tags na díly proběhl bez chyb (tagy fixnuté PŘED přesunem).

Splněno → finalize do namapované destinace. Nesplněno kvůli metadatům →
Inbox karta. Soubory se nikdy nemažou; kolize `-2` suffixem; DB cesty
aktualizovány; provenance nedotčena.

## Prerekvizity

- fiction + nonfiction mounty RW (dosud ro) — HOTOVO v compose.
- finalize_work: parametr layout ("legacy" | "book" | "collection") +
  destination_root.
- Odznak „finalizováno" na knize + v Library (žádané dříve).

## Bezpečnost

- Auto-přesun jde do kurátorské knihovny ⇒ každý běh zapisuje plný akční
  log do journalu; první týden navíc kopie akcí do Inboxu (informativní,
  bez čekání na schválení — režim automatiky).
