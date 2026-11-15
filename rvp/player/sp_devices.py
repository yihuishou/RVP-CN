"""再生エンジン: デバイス出力(ソフトスタート・速度制限・linear/twist 送出・funscript/rotate/vibration ランナー・オフセット)(ScenarioPlayer の mixin)。"""
from __future__ import annotations

import asyncio
import bisect
import time
from ..funscript import Funscript
from ..rotate_source import RotateTimeline, load_rotate_source
from ..scenario import (TRACK_LINEAR, TRACK_ROTATE, TRACK_ROTATE_A10,
    TRACK_TWIST, TRACK_VIBRATION)
from ..i18n import tr

from .clock import PlaybackClock
from .common import logger


# =378: トラック種別 → 停止の猶予で使うレーン名。猶予付きで止めるのは
# ufo / a10 / vib の3つ。linear / twist は「使っている」印(claim)だけに使う
# (イベント終了の全停止 stop_all を、次のノードが linear を動かすなら送らない)。
_DS_LANE = {TRACK_ROTATE: "ufo", TRACK_ROTATE_A10: "a10",
            TRACK_VIBRATION: "vib", TRACK_LINEAR: "linear",
            TRACK_TWIST: "twist"}
_DS_STOP_LANES = ("ufo", "a10", "vib")


def ds_lanes(types) -> set:
    """トラック種別の集まり → レーン名の集合(=378)。"""
    return {_DS_LANE[t] for t in types if t in _DS_LANE}


class _ScenarioPlayerDevicesMixin:
    """ScenarioPlayer の mixin(=301 分割)。デバイス出力(ソフトスタート・速度制限・linear/twist 送出・funscript/rotate/vibration ランナー・オフセット)"""

    def start_soft_start(self) -> None:
        """スマートスタートを開始する(2秒かけて速度上限を通常へ戻す)。

        再生開始/再開/シーク/イベントジャンプなど、再生位置が不連続に
        飛ぶ操作の直後に呼ぶ。速度制限「なし」設定でも期間中は機能する。
        """
        self._soft_until = time.monotonic() + self.SOFT_START_SEC

    def _current_speed_limit_ms(self) -> float:
        """現在有効なフルストローク最短時間(ms)を返す。0以下=制限なし。

        スマートスタート期間中は上限速度をSOFT_MIN_FRAC倍から線形に
        通常値へ戻す(最短時間としては 基準/割合 で長くなる)。
        """
        limit = self.linear_speed_limit_ms
        remain = self._soft_until - time.monotonic()
        if remain > 0:
            base = limit if limit > 0 else self.SOFT_BASE_MS
            frac = max(self.SOFT_MIN_FRAC, 1.0 - remain / self.SOFT_START_SEC)
            return base / frac
        return limit

    def _estimate_device_pos(self) -> float | None:
        """直近の送信履歴からデバイスの現在位置(実座標0-100)を推定する。

        進行中の移動は線形補間、完了後は到達位置。不明ならNone。
        """
        return self._estimate_axis(self._dev_move, self._dev_known_pos)

    @staticmethod
    def _estimate_axis(mv: dict | None, known: float | None) -> float | None:
        """軸の送信履歴(move/known)から現在位置を推定する(=79で汎用化)。"""
        if mv:
            frac = (time.monotonic() - mv["t0"]) / max(mv["dur"], 0.001)
            frac = max(0.0, min(1.0, frac))
            return mv["from"] + (mv["to"] - mv["from"]) * frac
        return known

    def _limit_linear_duration(self, duration_ms: int,
                               device_target: float) -> int:
        """速度制限を適用した所要時間(ms)を返す。

        移動距離(実座標)に比例した最短時間を下回る指示は引き伸ばす
        (移動は行うが最高速度以下になる)。現在位置が不明な場合は
        フルストローク相当の距離とみなして保守的に制限する。
        """
        return self._limit_axis_duration(duration_ms, device_target,
                                         self._estimate_device_pos())

    def _limit_axis_duration(self, duration_ms: int, device_target: float,
                             est: float | None) -> int:
        """軸の推定現在位置から速度制限後の所要時間を返す(=79で汎用化)。

        急動作防止の設定(linear_speed_limit_ms+スマートスタート)は
        linear/twist共通(ユーザー決定=79)。
        """
        limit = self._current_speed_limit_ms()
        if limit <= 0:
            return duration_ms
        dist = 100.0 if est is None else abs(device_target - est)
        return max(duration_ms, int(dist / 100.0 * limit))

    def _tcode_active(self) -> bool:
        """TCode直接出力(=80)が接続中か。接続中は linear/twist をそちらへ送る。"""
        return self.tcode is not None and getattr(self.tcode, "connected", False)

    def _device_link(self) -> bool:
        """linear/twist の送信先が1つでも生きているか(Intiface or TCode)。"""
        return self.intiface.connected or self._tcode_active()

    async def _send_linear_pos(self, raw_pos: int, duration_ms: int) -> None:
        """funscript座標の pos を駆動区間へ写して送り、表示用の状態も更新する。

        =71: funscript実行とレンジ調整時の追従で共用する。安全機構(速度制限+
        スマートスタート)はデバイス実座標(invert適用後)の移動距離で測る。
        =80: TCode接続中は Intiface の代わりに L0 軸へ直接送る(補正・
        安全機構・表示は完全に共通=最終段の送信だけが替わる)。
        """
        mapped_to = self.intiface.map_pos(raw_pos)
        invert = bool(getattr(self.intiface, "invert", False))
        # =86: 反転は区間内での折り返し(min+max - x)。自己逆写像なので、
        # デバイス座標→funscript座標の逆変換(ui_from)にも同じ式を使える。
        rlo = getattr(self.intiface, "range_min", 0)
        rhi = getattr(self.intiface, "range_max", 100)
        device_to = (rlo + rhi - mapped_to) if invert else mapped_to
        dev_from = self._estimate_device_pos()
        duration = self._limit_linear_duration(max(int(duration_ms), 20),
                                               device_to)
        if self._tcode_active():
            await self.tcode.send_axis("L0", device_to, duration)
        else:
            await self.intiface.send_linear(duration, raw_pos)
        now = time.monotonic()
        self._dev_move = {
            "from": dev_from if dev_from is not None else device_to,
            "to": device_to, "t0": now, "dur": duration / 1000.0,
        }
        self._dev_known_pos = device_to
        # UI表示はfunscript座標系(現在のinvert設定で戻す)
        ui_from = mapped_to if dev_from is None else \
            ((rlo + rhi - dev_from) if invert else dev_from)
        self.state["linear_move"] = {
            "from": ui_from, "to": mapped_to, "t0": now,
            "dur": duration / 1000.0,
        }
        self.state["linear_pos"] = mapped_to
        # レンジ調整の追従(=71)で使うため、写す前の生の pos を覚えておく
        self._last_linear_raw = raw_pos

    async def retarget_linear(self) -> bool:
        """駆動区間・反転の変更にあわせて、停止中のデバイスを追従させる(=71)。

        一度も指示を送っていない間はデバイスの実位置が分からない
        (buttplugは位置を読み出せない)ので**何もしない**。所要時間は
        急動作防止に任せる(大きく動くときほどゆっくり動く)。
        """
        raw = self._last_linear_raw
        if raw is None or not self._device_link():
            return False
        try:
            await self._send_linear_pos(raw, 20)
        except Exception:
            logger.warning(tr("linear位置の追従に失敗しました"))
            return False
        return True

    async def _send_twist_pos(self, raw_pos: int, duration_ms: int) -> None:
        """TWIST軸へfunscript座標の pos を送り、表示用の状態も更新する(=79)。

        _send_linear_pos のtwist版。急動作防止(速度制限+スマートスタート)は
        linearと共通設定を、twist軸自身の移動距離で適用する。
        """
        iface = self.intiface
        mapped_to = iface.map_twist_pos(raw_pos)
        invert = bool(getattr(iface, "twist_invert", False))
        # =86: 反転は区間内での折り返し(_send_linear_pos と同じ)
        tlo = getattr(iface, "twist_min", 0)
        thi = getattr(iface, "twist_max", 100)
        device_to = (tlo + thi - mapped_to) if invert else mapped_to
        dev_from = self._estimate_axis(self._twist_move, self._twist_known_pos)
        duration = self._limit_axis_duration(max(int(duration_ms), 20),
                                             device_to, dev_from)
        if self._tcode_active():
            await self.tcode.send_axis("R0", device_to, duration)
        else:
            await iface.send_twist(duration, raw_pos)
        now = time.monotonic()
        self._twist_move = {
            "from": dev_from if dev_from is not None else device_to,
            "to": device_to, "t0": now, "dur": duration / 1000.0,
        }
        self._twist_known_pos = device_to
        ui_from = mapped_to if dev_from is None else \
            ((tlo + thi - dev_from) if invert else dev_from)
        self.state["twist_move"] = {
            "from": ui_from, "to": mapped_to, "t0": now,
            "dur": duration / 1000.0,
        }
        self.state["twist_pos"] = mapped_to
        self._last_twist_raw = raw_pos

    async def retarget_twist(self) -> bool:
        """TWISTの駆動区間・反転の変更にあわせた停止中の追従(=79。=71と同型)。"""
        raw = self._last_twist_raw
        if raw is None or not self._device_link():
            return False
        try:
            await self._send_twist_pos(raw, 20)
        except Exception:
            logger.warning(tr("twist位置の追従に失敗しました"))
            return False
        return True

    def _device_output_on(self) -> bool:
        """デバイス駆動が有効か(=252 デバイス連動フラグ)。

        シナリオの device_enabled=False のときはトラックのランナーを一切
        起動しない(=デバイスへ送信しない・グラフ断片も作らない)。
        スクリプト専用アイテムの**再生時間はそのまま**(長さ計算はランナーと
        独立)なので、フラグを切り替えてもシナリオの進行タイミングは
        変わらない。シナリオ未読込は True(従来どおり)。
        属性ごと無い場合(テストが __new__ で部分構築するケース)も True。
        """
        return bool(getattr(getattr(self, "scenario", None),
                            "device_enabled", True))

    def set_offset(self, device_type: str, seconds: float) -> None:
        """デバイス動作タイミングのオフセットを設定する(UIスレッドから呼び出し可)。

        正の値=遅らせる / 負の値=早める。-2.0〜+2.0秒にクランプ。
        """
        seconds = max(-2.0, min(2.0, seconds))
        self.offsets_ms[device_type] = int(round(seconds * 1000))
        # 送信済みのlinearコマンドは旧基準のままなので再送させる
        self._fs_resync = True

    async def _run_funscript(self, funscript: Funscript, clock: PlaybackClock,
                             twist: bool = False) -> None:
        """クロックの現在時刻から実行区間を逆算してlinear/twistコマンドを送る。

        =79: twist=True で送信先が TWIST軸(2軸目)・オフセットが "twist" レーン
        になる。ループ構造・シーク/一時停止の再同期(_fs_resync)は完全に共通。
        """
        actions = funscript.actions
        ats = [a.at for a in actions]
        n = len(actions)
        last_sent = None
        was_paused = False
        offset_key = "twist" if twist else "linear"
        send = self._send_twist_pos if twist else self._send_linear_pos

        while True:
            if clock.paused:
                was_paused = True
                await asyncio.sleep(0.05)
                continue
            if was_paused or self._fs_resync:
                was_paused = False
                self._fs_resync = False
                last_sent = None

            now = clock.now_ms() - self.offsets_ms[offset_key]
            idx = bisect.bisect_right(ats, now) - 1
            if idx >= n - 1:
                break

            if last_sent != idx:
                target = actions[idx + 1]
                try:
                    await send(target.pos, max(int(target.at - now), 20))
                except Exception:
                    logger.warning(tr("接続ロストのためfunscript実行を中断します"))
                    return
                last_sent = idx

            await asyncio.sleep(0.01)

    def _load_rotate_safe(self, path: str):
        """rotate動作元(funscript/CSV)を安全に読み込む。

        壊れたCSV/funscriptでも再生全体を止めず、そのトラックだけスキップする
        (警告ログのみ)。読み込めた場合は RotateTimeline を返す。
        """
        try:
            return load_rotate_source(path)
        except Exception as e:
            logger.warning(tr("rotate動作元の読み込みに失敗しました: %s"), e)
            return None

    async def _run_rotate(self, funscript: Funscript, clock: PlaybackClock,
                          a10: bool = False) -> None:
        """後方互換: 既読funscriptを RotateTimeline 化して実行する。

        (旧API。直接呼ぶテスト用。通常の再生経路は _run_rotate_timeline。)
        """
        tl = RotateTimeline.from_funscript(funscript)
        await self._run_rotate_timeline(tl, clock, "a10" if a10 else "ufo")

    @staticmethod
    def _display_pos(vals) -> int:
        """表示用の代表pos(0-100)。2chは速度率の大きいチャンネル(=OR)を使う。"""
        if not vals:
            return 50
        cw, frac = max(vals, key=lambda v: v[1])   # 同率はch0(左)優先
        off = int(round(max(0.0, min(1.0, frac)) * 50))
        return 50 + off if cw else 50 - off

    async def _run_rotate_timeline(self, timeline: RotateTimeline,
                                   clock: PlaybackClock, lane: str,
                                   hold_tail: float | None = None) -> None:
        """RotateTimeline(funscript/CSV)を実行する。

        lane: "ufo" / "a10"。funscript由来(1ch)は従来の pos 送信API
        (send_rotate/send_rotate_a10)を使い、モック互換と整数精度を保つ。
        CSV由来は send_rotate_lane でロータ個別・float精度で送出する
        (ufotwの2ロータ独立=タイプB対応)。

        hold_tail(=378・スクリプトのみアイテムの長さ ms): アイテムの終わり
        ちょうど(以降)にある最後の行(多くは停止の行)は送らない。止めるかどうかはアイテムの
        後始末(停止の猶予)が決める=次のスクリプトが続けば止めない。
        """
        lane_ufo = (lane != "a10")
        offset_key = "rotate" if lane_ufo else TRACK_ROTATE_A10
        state_key = "rotate_pos" if lane_ufo else "rotate_a10_pos"
        ch_key = "rotate_ufo_ch" if lane_ufo else "rotate_a10_ch"
        use_pos_api = (timeline.source_kind == "funscript")
        steps = timeline.steps
        ats = timeline.ats
        n = len(steps)
        last_sent = None
        was_paused = False
        last_swap = bool(self.rotate_swap.get(lane, False))

        async def send_vals(vals):
            if use_pos_api:
                pos = self._display_pos(vals)
                if lane_ufo:
                    await self.intiface.send_rotate(pos)
                else:
                    await self.intiface.send_rotate_a10(pos)
            else:
                # =71: 左右反転は**送信のたびに読み直す**。トラック開始時に
                # 固定すると、再生中にチェックを操作しても次のアイテムまで
                # 反映されない(レンジ/反転/オフセットは元から都度読み直し)。
                swap = bool(self.rotate_swap.get(lane, False))
                await self.intiface.send_rotate_lane(lane, list(vals), swap=swap)

        stop_vals = tuple((True, 0.0) for _ in range(timeline.channels))
        if n == 0:
            return
        tail_idx = (n - 1 if hold_tail is not None and n > 1
                    and steps[-1].at >= hold_tail else None)

        while True:
            if clock.paused:
                if not was_paused:
                    try:
                        await send_vals(stop_vals)
                    except Exception:
                        return
                    self.state[state_key] = 50
                    self.state[ch_key] = list(stop_vals)
                was_paused = True
                await asyncio.sleep(0.05)
                continue
            if was_paused or self._fs_resync:
                was_paused = False
                last_sent = None
            # =71: 左右反転が切り替わったら同じ行でも送り直す。CSVは行の間隔が
            # 長いことがあり、次の行を待つと反映が数十秒遅れてしまう。
            cur_swap = bool(self.rotate_swap.get(lane, False))
            if cur_swap != last_swap:
                last_swap = cur_swap
                last_sent = None

            now = clock.now_ms() - self.offsets_ms[offset_key]
            idx = bisect.bisect_right(ats, now) - 1
            if idx < 0:
                # 再生位置が最初の行/アクションの時刻に達する前は**停止**。
                # CSV: 従来どおり(例: 1行目 300,1,20 → 0〜30秒はUFO停止)。
                # funscript: =271 でCSVと同じ停止へ変更。従来は先頭
                # アクションのposを先取り保持していた(1行目 at=5000,pos=80
                # だと最初の5秒間も80で回ってしまう)。at=0 の指示が無い
                # ときは既定 pos,at=50,0(停止)として扱う(ユーザー依頼)。
                if last_sent != "pre":
                    try:
                        await send_vals(stop_vals)
                    except Exception:
                        logger.warning(tr("接続ロストのためrotate実行を中断します"))
                        return
                    self.state[state_key] = 50
                    self.state[ch_key] = list(stop_vals)
                    last_sent = "pre"
                await asyncio.sleep(0.01)
                continue
            if idx >= n:
                break

            if last_sent != idx and idx == tail_idx:
                last_sent = idx          # =378: 終わりちょうどの行は送らない
            if last_sent != idx:
                vals = steps[idx].vals
                try:
                    await send_vals(vals)
                    self.state[state_key] = self._display_pos(vals)
                    self.state[ch_key] = list(vals)
                except Exception:
                    logger.warning(tr("接続ロストのためrotate実行を中断します"))
                    return
                last_sent = idx

            await asyncio.sleep(0.01)

    async def _run_vibration(self, funscript: Funscript, clock: PlaybackClock,
                             hold_tail: float | None = None) -> None:
        """vibration用funscript実行。pos=0停止/100最大振動。

        rotateと同じステップ方式: 各区間の開始アクションのposを
        その区間中ずっと維持する。hold_tail は _run_rotate_timeline と同じ(=378)。
        """
        actions = funscript.actions
        ats = [a.at for a in actions]
        n = len(actions)
        tail_idx = (n - 1 if hold_tail is not None and n > 1
                    and actions[-1].at >= hold_tail else None)
        last_sent = None
        was_paused = False

        while True:
            if clock.paused:
                if not was_paused:
                    try:
                        await self.intiface.send_vibration(0)
                    except Exception:
                        return
                    self.state["vibration_pos"] = 0
                was_paused = True
                await asyncio.sleep(0.05)
                continue
            if was_paused or self._fs_resync:
                was_paused = False
                last_sent = None

            now = clock.now_ms() - self.offsets_ms["vibration"]
            idx = bisect.bisect_right(ats, now) - 1
            if idx < 0:
                # =271: 最初のアクションの時刻に達する前は**停止**(pos=0)。
                # at=0 の指示が無いときは既定 pos,at=0,0(停止)として扱う
                # (従来は先頭アクションのposを先取り保持していた=rotateと
                # 同じ問題。ユーザー依頼で変更)。
                if last_sent != "pre":
                    try:
                        await self.intiface.send_vibration(0)
                    except Exception:
                        logger.warning(tr("接続ロストのためvibration実行を中断します"))
                        return
                    self.state["vibration_pos"] = 0
                    last_sent = "pre"
                await asyncio.sleep(0.01)
                continue
            if idx >= n:
                break

            if last_sent != idx and idx == tail_idx:
                last_sent = idx          # =378: 終わりちょうどの点は送らない
            if last_sent != idx:
                pos = actions[idx].pos
                try:
                    await self.intiface.send_vibration(pos)
                    self.state["vibration_pos"] = pos
                except Exception:
                    logger.warning(tr("接続ロストのためvibration実行を中断します"))
                    return
                last_sent = idx

            await asyncio.sleep(0.01)

    # ------------------------------------------------------------------
    # =378 停止の猶予(スクリプトのみアイテムの切り替わりで止めない)
    #
    # 従来はスクリプトのみアイテムが終わるたび(=ランダムで次の1本へ移る
    # たび・ステートの切り替わり)に rotate/vibration へ停止を送り、イベント
    # の終わりには stop_all を送っていた。短い CSV を続けて流すと、その
    # たびに UFO が一瞬止まる(ユーザー報告・漫画連動)。
    # スクリプトのみアイテムの終わりでは停止を STOP_GRACE_MS だけ保留し、
    # その間に次のスクリプトのみアイテムが同じレーンを使い始めたら取り消す。
    # 音声・動画のアイテムは従来どおり(終わりで即停止。始まるときは保留中の
    # 停止を先に送る=音声の切り替わりでは止まる)。
    # 状態(_ds_*)は ScenarioPlayer.__init__ が持つ(mixin は状態を持たない)。
    # ------------------------------------------------------------------
    def _ds_now(self) -> float:
        return time.monotonic()

    def _ds_reset(self) -> None:
        """保留をすべて捨てる(停止・一時停止・ジャンプ・再生開始)。"""
        self._ds_pending = {}
        self._ds_all = None
        self._ds_claimed = set()
        task, self._ds_task = self._ds_task, None
        if task is not None and not task.done() \
                and task is not _current_task():
            task.cancel()

    def _ds_defer(self, lanes) -> None:
        """スクリプトのみアイテムの終わり: lanes の停止を猶予付きで保留する。"""
        if self._stop_requested:
            return
        due = self._ds_now() + self.STOP_GRACE_MS / 1000.0
        for ln in lanes:
            if ln in _DS_STOP_LANES:
                self._ds_pending[ln] = due
                self._ds_claimed.discard(ln)
        self._ds_kick()

    def _ds_defer_all(self) -> None:
        """イベントの終わり: 全停止(stop_all)を猶予付きで保留する。

        猶予の間に次のノードが使い始めたレーン(claim)は止めない。誰も
        使い始めなければ従来どおり stop_all。
        """
        if self._stop_requested:
            return
        self._ds_all = self._ds_now() + self.STOP_GRACE_MS / 1000.0
        self._ds_claimed = set()
        self._ds_kick()

    def _ds_claim(self, lanes) -> None:
        """スクリプトのみアイテムの始まり: 保留中の停止を取り消す(送らない)。"""
        for ln in lanes:
            self._ds_pending.pop(ln, None)
            self._ds_claimed.add(ln)

    async def _ds_flush(self, lanes) -> None:
        """音声・動画アイテムの始まり: そのアイテムが動かすレーンの保留中の
        停止を**いま**送る(音声の切り替わりでは止める=従来の動き)。"""
        for ln in lanes:
            send = self._ds_pending.pop(ln, None) is not None
            if (self._ds_all is not None and ln in _DS_STOP_LANES
                    and ln not in self._ds_claimed):
                send = True
            if ln in _DS_STOP_LANES and send:
                await self._ds_send_stop(ln)
            self._ds_claimed.add(ln)

    def _ds_drop(self, lanes) -> None:
        """いま停止を送った(インターバル)レーンの保留を外す。"""
        for ln in lanes:
            self._ds_pending.pop(ln, None)

    def _ds_kick(self) -> None:
        if self._ds_task is None or self._ds_task.done():
            try:
                self._ds_task = asyncio.ensure_future(self._ds_loop())
            except RuntimeError:
                # イベントループの外(テストの同期呼び出し等)。保留は残る
                self._ds_task = None

    async def _ds_loop(self) -> None:
        try:
            while self._ds_pending or self._ds_all is not None:
                due = list(self._ds_pending.values())
                if self._ds_all is not None:
                    due.append(self._ds_all)
                wait = min(due) - self._ds_now()
                if wait > 0:
                    await asyncio.sleep(min(wait, 0.05))
                    continue
                await self._ds_fire_due()
        finally:
            if self._ds_task is _current_task():
                self._ds_task = None

    async def _ds_fire_due(self) -> None:
        now = self._ds_now()
        if self._stop_requested or self._paused:
            # 停止・一時停止は stop_all を自分で送っている
            self._ds_pending = {}
            self._ds_all = None
            self._ds_claimed = set()
            return
        for ln, t in list(self._ds_pending.items()):
            if t <= now:
                del self._ds_pending[ln]
                await self._ds_send_stop(ln)
        if self._ds_all is not None and self._ds_all <= now:
            self._ds_all = None
            claimed, self._ds_claimed = self._ds_claimed, set()
            if not claimed:
                try:
                    await self.intiface.stop_all()
                except Exception:
                    pass
                self._ds_pending = {}
                self.state["rotate_pos"] = 50
                self.state["rotate_a10_pos"] = 50
                self.state["vibration_pos"] = 0
            else:
                for ln in _DS_STOP_LANES:
                    if ln not in claimed:
                        self._ds_pending.pop(ln, None)
                        await self._ds_send_stop(ln)

    async def _ds_send_stop(self, lane: str) -> None:
        try:
            if lane == "ufo":
                await self.intiface.send_rotate(50)
                self.state["rotate_pos"] = 50
            elif lane == "a10":
                await self.intiface.send_rotate_a10(50)
                self.state["rotate_a10_pos"] = 50
            elif lane == "vib":
                await self.intiface.send_vibration(0)
                self.state["vibration_pos"] = 0
        except Exception:
            pass


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None
