"""The description keeps every paragraph of the article body.

Live case 2026-11-08 (Fredric Brown: Hvězdná myš, Vltava): the body had
three paragraphs about the author; only the first made it into the comment.
The owner added the other two by hand.
"""
from __future__ import annotations

from audiobiblio.library.pipelines.html_scraper import scrape_episode_html

PAGE = """<html><body>
<div class="field field-perex"><p>Výstřední profesor Oberburger vyšle do vesmíru myšáka, se kterým sdílí svou domácnost.</p></div>
<div class="field body">
  <p>Dříve než se začal živit žurnalistikou a posléze i literaturou, prošel Fredric Brown různými zaměstnáními.</p>
  <p>Ve vědecko-fantastickém žánru se proslavil zejména jako autor krátkých a většinou humorně laděných povídek.</p>
  <p>Za svého života se neřadil mezi nejpopulárnější autory, nicméně s odstupem času získal kultovní základnu.</p>
</div>
<div class="b-041n__block"><p>Románová kronika ztraceného města - léta 1945–1961, úplně jiný pořad na stejné stránce.</p></div>
</body></html>"""


def test_all_body_paragraphs_kept_and_related_teasers_skipped(tmp_path):
    f = tmp_path / "page.html"
    f.write_text(PAGE, encoding="utf-8")
    meta = scrape_episode_html(f)
    paras = meta.description.split("\n\n")
    assert len(paras) == 3
    assert paras[0].startswith("Dříve než")
    assert paras[2].startswith("Za svého života")
    assert "Karviné" not in meta.description and "Románová" not in meta.description


def test_page_without_body_container_falls_back_to_first_paragraph(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<html><body><p class='perex'>Krátký perex, který je dost dlouhý na to, aby prošel.</p>"
                 "<p>První odstavec popisu, který je také dost dlouhý, aby prošel filtrem délky.</p>"
                 "<p>Druhý odstavec o úplně jiném pořadu na stejné stránce, ten se brát nemá.</p>"
                 "</body></html>", encoding="utf-8")
    meta = scrape_episode_html(f)
    assert meta.description.startswith("První odstavec")
    assert "Druhý" not in meta.description
