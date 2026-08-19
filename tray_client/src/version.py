"""현장 도우미 버전 단일 소스.

트레이 아이콘 title·설정 창 표기·설치 파일(installer.iss MyAppVersion)이 모두
이 값을 기준으로 한다. installer.iss 와의 동기화는 테스트
(tests/test_tray_installer_script.py)가 강제한다 — 한쪽만 올리면 실패한다.
"""

from __future__ import annotations

__version__ = "3.2.2"
