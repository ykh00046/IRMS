"""템플릿이 참조하는 정적 파일이 실제로 있는지.

두 겹으로 지킨다.

  ① 저장소 기준 — 이 저장소 상태에서 참조가 전부 해소되는지. 템플릿에 `?v=` 를 올리면서
     파일명을 잘못 적거나, 새 JS/CSS 를 커밋에서 빠뜨리면 여기서 걸린다.
  ② 배포 기준 — 서버 기동 시 같은 검사를 돌려 로그로 경고한다(src.main).

②가 필요한 이유: 2026-08-12 에 운영 PC 의 git pull 이 static/js 세 파일을 디스크에
쓰지 못했는데 HEAD 는 최신으로 올라갔다. 저장소는 멀쩡했으므로 ①로는 절대 잡히지
않고, 자재 관리 화면을 열어 본 사람이 "메뉴가 먹통" 이라고 말할 때까지 아무도 몰랐다.
"""

from __future__ import annotations

from pathlib import Path

from src.main import missing_static_assets

BASE_DIR = Path(__file__).resolve().parent.parent


# ── ① 저장소에 참조가 전부 있는가 ────────────────────────────────────────
def test_no_template_references_a_missing_static_file():
    missing = missing_static_assets(BASE_DIR)
    assert not missing, (
        "템플릿이 가리키는 정적 파일이 없습니다(커밋 누락이거나 경로 오타):\n  "
        + "\n  ".join(missing)
    )


def test_the_check_actually_reads_something():
    """검사가 빈손으로 통과하고 있지 않은지 — 참조를 실제로 걷어내는지 확인."""
    import re

    from src.main import _STATIC_REF_RE

    html = (BASE_DIR / "templates" / "materials.html").read_text(encoding="utf-8")
    refs = _STATIC_REF_RE.findall(html)
    assert "js/materials.js" in refs, "자재 관리 화면의 JS 참조를 못 읽는다"
    assert any(r.startswith("css/") for r in refs), "CSS 참조를 못 읽는다"
    # ?v= 캐시버스팅이 붙어 있어도 파일명만 떼어내야 한다.
    assert not any("?" in r for r in refs), "쿼리스트링이 파일명에 섞였다"
    assert re.search(r'src="/static/js/materials\.js\?v=', html), "테스트 전제가 바뀌었다"


# ── ② 배포 기준 검사가 없는 파일을 잡아내는가 ────────────────────────────
def test_check_reports_a_file_that_is_not_on_disk(tmp_path):
    """디스크에만 없는 경우를 재현 — 운영에서 실제로 일어난 형태."""
    (tmp_path / "templates").mkdir()
    (tmp_path / "static" / "js").mkdir(parents=True)
    (tmp_path / "templates" / "page.html").write_text(
        '<script src="/static/js/present.js?v=1"></script>\n'
        '<script src="/static/js/gone.js?v=1"></script>\n'
        '<link rel="stylesheet" href="/static/css/gone.css" />\n',
        encoding="utf-8",
    )
    (tmp_path / "static" / "js" / "present.js").write_text("//", encoding="utf-8")

    assert missing_static_assets(tmp_path) == ["css/gone.css", "js/gone.js"]


def test_check_is_quiet_when_everything_is_there(tmp_path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "static" / "js").mkdir(parents=True)
    (tmp_path / "templates" / "page.html").write_text(
        '<script src="/static/js/app.js?v=20260812a"></script>', encoding="utf-8"
    )
    (tmp_path / "static" / "js" / "app.js").write_text("//", encoding="utf-8")

    assert missing_static_assets(tmp_path) == []


def test_check_survives_a_project_without_templates(tmp_path):
    """템플릿 폴더가 없어도 기동을 막지 않는다(검사는 조용히 빈 목록)."""
    assert missing_static_assets(tmp_path) == []


def test_startup_logs_an_error_when_a_file_is_missing(tmp_path, caplog):
    """기동 검사는 로그만 남기고 예외를 던지지 않는다 — 파일 하나로 서버 전체가
    안 뜨는 쪽이 더 나쁘다."""
    from src.main import _warn_missing_static_assets

    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    (tmp_path / "templates" / "page.html").write_text(
        '<script src="/static/js/gone.js"></script>', encoding="utf-8"
    )

    with caplog.at_level("ERROR", logger="irms.startup"):
        _warn_missing_static_assets(tmp_path)   # 예외 없이 반환해야 한다

    assert any("js/gone.js" in r.getMessage() for r in caplog.records)
