from audiobiblio.core.db.models import ApprovalMode, CrawlTarget, CrawlTargetKind


def test_default_is_review(db_session):
    t = CrawlTarget(url="https://mujrozhlas.cz/ctenarsky-denik",
                    kind=CrawlTargetKind.PROGRAM)
    db_session.add(t)
    db_session.flush()
    assert t.approval_mode == ApprovalMode.REVIEW


def test_auto_roundtrip(db_session):
    t = CrawlTarget(url="https://mujrozhlas.cz/hra-na-nedeli",
                    kind=CrawlTargetKind.PROGRAM, approval_mode=ApprovalMode.AUTO)
    db_session.add(t)
    db_session.flush()
    db_session.expire(t)
    assert t.approval_mode == ApprovalMode.AUTO
