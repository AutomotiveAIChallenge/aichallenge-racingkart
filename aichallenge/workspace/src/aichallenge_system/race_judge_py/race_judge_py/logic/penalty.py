from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PenaltyKind(str, Enum):
    CRASH = "crash"
    WALL = "wall"
    OVER = "over"


@dataclass
class PenaltyEvent:
    kind: PenaltyKind
    lap: int
    race_time: float   # レース開始からの秒、ペナルティ開始時刻
    duration: float

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "lap": self.lap,
            "race_time": self.race_time,
            "duration": self.duration,
        }


class PenaltyTracker:
    """AWSIM互換のペナルティ簿記。

    trigger() は kind 毎に cooldown_sec のペナルティ窓を開く(または延長する)。
    窓内の再トリガーは新イベントを数えず終端を延長する(OnTriggerStay 相当)。
    update() は期限切れの窓を PenaltyEvent として確定する。
    union_total_seconds() は kind 重複期間を一度だけ数える(PenaltyUnionTimer 相当)。"""

    def __init__(self, cooldown_sec: float = 2.0):
        self.cooldown_sec = cooldown_sec
        self._open: dict = {}          # kind -> [start, end, lap]
        self._events: list[PenaltyEvent] = []

    def trigger(self, kind: PenaltyKind, lap: int, race_time: float) -> None:
        cur = self._open.get(kind)
        if cur is not None and race_time <= cur[1]:
            cur[1] = max(cur[1], race_time + self.cooldown_sec)
        else:
            if cur is not None:
                self._finalize(kind)
            self._open[kind] = [race_time, race_time + self.cooldown_sec, lap]

    def _finalize(self, kind: PenaltyKind) -> None:
        start, end, lap = self._open.pop(kind)
        self._events.append(PenaltyEvent(kind, lap, start, end - start))

    def update(self, race_time: float) -> list[PenaltyEvent]:
        before = len(self._events)
        for kind in [k for k, v in self._open.items() if v[1] <= race_time]:
            self._finalize(kind)
        return self._events[before:]

    def finalize_all(self) -> None:
        for kind in list(self._open):
            self._finalize(kind)

    def is_active(self, race_time: float) -> bool:
        return any(start <= race_time < end for start, end, _ in self._open.values())

    @property
    def events(self) -> list:
        return list(self._events)

    def by_kind(self) -> dict:
        out = {k.value: {"count": 0, "total_seconds": 0.0} for k in PenaltyKind}
        for e in self._events:
            out[e.kind.value]["count"] += 1
            out[e.kind.value]["total_seconds"] += e.duration
        return out

    def union_total_seconds(self) -> float:
        intervals = sorted(
            [(e.race_time, e.race_time + e.duration) for e in self._events]
            + [(s, en) for s, en, _ in self._open.values()]
        )
        total, cur_s, cur_e = 0.0, None, None
        for s, e in intervals:
            if cur_e is None:
                cur_s, cur_e = s, e
            elif s <= cur_e:
                cur_e = max(cur_e, e)
            else:
                total += cur_e - cur_s
                cur_s, cur_e = s, e
        if cur_e is not None:
            total += cur_e - cur_s
        return total
