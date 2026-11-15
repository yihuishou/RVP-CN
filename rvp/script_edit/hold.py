"""=379: Fキー「押している間だけ」の点の供給(Tk 非依存の純ロジック)。

押した瞬間の素材時刻 at0 から、パターンの相対点列(pattern_plan を at0=0 で
求めたもの)を素材時刻へ写して順に返す。押しっぱなしでパターンの終わりに
届いたら、次のパターンを **0秒から** 続ける(ユーザー決定 Q12)。途中で
同じ側の別の Fキーへ持ち替えたら、次のパターンからその形にする(=222 と同じ)。

開始位置(ユーザー決定 Q9〜Q11):
  - 先頭から: off=0
  - ランダム: off は 0.0 秒〜(長さ−0.1 秒)の 0.1 秒刻み(random_offset)。
    最初のパターンだけ off から引用し、押した位置には **off の時点で効いて
    いる値** を打つ(点と点の間でも)。
"""
from __future__ import annotations

import random as _random

HOLD_OFFSET_STEP_MS = 100       # ランダム開始位置の刻み(0.1 秒)


def rel_len(rel) -> int:
    """相対点列の長さ(最後の点の時刻)。"""
    return int(rel[-1][0]) if rel else 0


def value_at(rel, t: float):
    """相対点列で時刻 t に効いている値(階段=次の点まで同じ値)。"""
    if not rel:
        return None
    v = rel[0][1]
    for r, p in rel:
        if r <= t + 1e-9:
            v = p
        else:
            break
    return v


def random_offset(length_ms: int, rng=None,
                  step: int = HOLD_OFFSET_STEP_MS) -> int:
    """0〜(length−step) の step 刻みから1つ選ぶ(短すぎる形は 0)。"""
    length_ms = int(length_ms)
    if length_ms <= step:
        return 0
    n = (length_ms - step) // step + 1
    r = rng if rng is not None else _random
    return int(r.randrange(n)) * step


class HoldSession:
    """1回の押しっぱなし(Fキー1つ)。

    entries … 打点先(左/右)ごとの dict:
      idx(モデル番号)・model・feed(HoldFeed)・at0(押した位置)・
      written(ここまで明け渡し済みの素材時刻)・token(UNDO の印)
    stop … 離したときに打つ停止値(ROTATE=中心 / VIBRATION=0)"""
    __slots__ = ("fk", "entries", "stop")

    def __init__(self, fk, entries, stop):
        self.fk = fk
        self.entries = list(entries)
        self.stop = int(stop)


class HoldFeed:
    """1つのモデル(左/右)ぶんの点の供給。

    rel  … pattern_plan(at0=0) の点列 [(相対ms, pos), …](0 始まり・昇順)
    at0  … 押した位置(素材時刻・グリッド吸着済み)
    off  … 最初のパターンの引用開始(相対ms)
    """

    def __init__(self, rel, at0: int, off: int = 0):
        self.rel = [(int(round(r)), int(p)) for r, p in rel]
        self.at0 = int(at0)
        self.off = int(off)
        self.next_rel = None        # 持ち替え後の形(次のパターンから)
        self._seg_at = self.at0     # 今のパターンの「off に当たる」素材時刻
        self._seg_off = self.off
        self._seg_rel = self.rel
        self._queue = []            # まだ返していない今のパターンの点
        self._load_segment(first=True)

    def set_next(self, rel) -> None:
        """同じ側の別の Fキーへ持ち替えた: 次のパターンからこの形。"""
        if rel:
            self.next_rel = [(int(round(r)), int(p)) for r, p in rel]

    def _load_segment(self, first: bool = False) -> None:
        rel, off, at = self._seg_rel, self._seg_off, self._seg_at
        q = [(at, value_at(rel, off))]
        for r, p in rel:
            if r > off:
                q.append((at + r - off, p))
        self._queue = q

    def _advance_segment(self) -> bool:
        """今のパターンを出し切った: 次のパターン(0秒から)へ。"""
        length = rel_len(self._seg_rel) - self._seg_off
        if length <= 0:
            return False                # 進まない形=無限ループ防止
        self._seg_at = self._seg_at + length
        if self.next_rel is not None:
            self._seg_rel = self.next_rel
            self.next_rel = None
        self._seg_off = 0
        if rel_len(self._seg_rel) <= 0:
            return False
        self._load_segment()
        return True

    def take(self, upto: float, before: bool = False):
        """素材時刻 upto まで(before=True なら upto 未満)の点を順に返す。"""
        out = []
        while True:
            if not self._queue:
                if not self._advance_segment():
                    break
            at, p = self._queue[0]
            if at > upto or (before and at >= upto):
                break
            out.append(self._queue.pop(0))
        return out
