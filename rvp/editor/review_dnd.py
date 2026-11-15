"""アイテムレビュー・スクリプト編集画面の D&D と単独起動(=380)。

ItemReviewDialog の mixin(状態は持たない=作法 §3.6。状態は本体の __init__)。

- **単独起動(A)**: メイン画面ヘッダーの「スクリプト編集」から、シナリオにも
  アイテムにも紐づかない編集画面として開く(`standalone=True`)。レビューを経ず
  最初から編集モードの新規作成。「レビューに戻る」は出さない。新規保存は
  独立した funscript/csv(アイテムへの紐づけなし)。
- **D&D**: 編集モードで funscript/csv を落とすと、ファイル名のタグと中身で
  種別を決めて**先に検証**し、「対象」の一覧へ加えて開く(未保存なら既存の
  「保存する/破棄する/やめる」)。単独起動では音声(wav/mp3)・動画も受け、
  **参考メディア**として再生・波形に使う(1つだけ・入れ替え・確認なし)。
  スクリプトを落としたとき同じフォルダに対応する音声・動画があれば自動で
  読み込む(動画優先・mpv が無ければ音声)。
  シナリオのアイテム(B)では音声・動画は差し替えない(案内だけ)。
"""
from __future__ import annotations

import os

from ..i18n import load_config, save_config, tr
from ._hooks import _pkg, DND_FILES
from .common import MSG_INFO, MSG_WARN, VIDEO_EXTS
from .review_support import (AUDIO_DROP_EXTS, SCRIPT_DROP_EXTS,
                             SCRIPT_EDIT_NEW_KINDS, _is_csv,
                             find_media_for_script, script_type_from_name)


class _ItemReviewDndMixin:
    """ItemReviewDialog の mixin(=380)。D&D・単独起動・参考メディア。"""

    # 単独起動で最後に選んだ新規作成の種別(表示名)を覚える config キー(Q4)
    CFG_STANDALONE_KIND = "script_edit_new_kind"

    # ---- D&D の登録 ----
    def _setup_dnd(self):
        """ダイアログ全体を1つのドロップ先にする(tkinterdnd2 が無ければ無効)。"""
        self._dnd_ok = False
        if _pkg().TkinterDnD is None:
            return
        try:
            _pkg().TkinterDnD._require(self)
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_review_drop)
            self._dnd_ok = True
        except Exception:
            self._dnd_ok = False

    def _dnd_hint_text(self) -> str:
        # =382: 文言はユーザー指定(A・B 共通。音声・動画の D&D は A の名前欄
        # 「音声・動画(D&D可)」で案内する)
        return tr("(funscript/csvをD&Dで読み込めます)")

    def _refresh_dnd_hint(self):
        """「＋新規」の右の案内(Q16)。D&D が使える環境でだけ出す。"""
        lbl = getattr(self, "edit_dnd_hint", None)
        if lbl is None:
            return
        lbl.configure(text=self._dnd_hint_text())
        if getattr(self, "_dnd_ok", False):
            if not lbl.winfo_manager():
                lbl.pack(side="left", padx=(12, 0))
        elif lbl.winfo_manager():
            lbl.pack_forget()

    # ---- 単独起動(A) ----
    def _start_standalone(self):
        """単独起動の初期化: 見出し・「レビューに戻る」を隠す・前回の種別で
        新規作成の編集モードへ。"""
        self.title(tr("スクリプト編集 - RVP"))
        if self.edit_btn.winfo_manager():
            self.edit_btn.pack_forget()
        try:
            label = str(load_config().get(self.CFG_STANDALONE_KIND, ""))
        except Exception:
            label = ""
        if not self._edit_built:
            self._build_edit_ui()
        if label in [n for n, _s in SCRIPT_EDIT_NEW_KINDS]:
            self.edit_type_var.set(label)
        self._enter_edit_mode()
        self._refresh_media_label()

    def _remember_standalone_kind(self):
        """新規作成の種別コンボを替えたら覚える(単独起動のときだけ=Q4)。"""
        if not getattr(self, "_standalone", False):
            return
        label = self.edit_type_var.get()
        try:
            cfg = load_config()
            if cfg.get(self.CFG_STANDALONE_KIND) != label:
                cfg[self.CFG_STANDALONE_KIND] = label
                save_config(cfg)
        except Exception:
            pass

    def _refresh_media_label(self):
        """単独起動の上部の名前欄: 参考メディアの名前/無いときは案内(Q16)。"""
        if not getattr(self, "_standalone", False):
            return
        media = self.spec.get("video") or self.spec.get("audio") or ""
        if media:
            text = os.path.basename(media)
        else:
            # =382: ユーザー指定の文言(「なし」は意図的に使わない)
            text = tr("音声・動画(D&D可)")
        self.name_label.configure(text=text)
        self.info_label.configure(text="")

    # ---- 落とされたファイルの振り分け ----
    def _on_review_drop(self, event):
        try:
            paths = list(self.tk.splitlist(event.data))
        except Exception:
            return
        self.handle_dropped_files(paths)

    def _edit_msg(self, text: str, warn: bool = True):
        if getattr(self, "edit_msg", None) is None:
            return
        self.edit_msg.configure(text=text,
                                text_color=MSG_WARN if warn else MSG_INFO)
        if warn:
            self._edit_paste_warned = True   # 次の編集の成功で下ろす(=171)

    def handle_dropped_files(self, paths) -> bool:
        """落とされたファイルを処理する(テストからも呼ぶ)。何か読んだら True。

        編集モードのときだけ受ける(B のレビュー表示中は何もしない)。"""
        if not self.edit_mode or not self._edit_built:
            return False
        scripts, media, other = [], [], []
        for p in paths:
            ext = os.path.splitext(str(p))[1].lower()
            if ext in SCRIPT_DROP_EXTS:
                scripts.append(p)
            elif ext in AUDIO_DROP_EXTS or ext in VIDEO_EXTS:
                media.append(p)
            else:
                other.append(p)
        msgs = []
        if other:
            msgs.append(tr("対応していないファイルです: {0}").format(
                ", ".join(os.path.basename(p) for p in other)))
        if media and not getattr(self, "_standalone", False):
            msgs.append(tr("シナリオのアイテムでは音声・動画は差し替えられません"
                           "（funscript/csv は読み込めます）"))
            media = []
        done = False
        if scripts:
            done, smsgs = self._drop_scripts(scripts, media[:1])
            msgs = smsgs + msgs
        elif media:
            ok, m = self._set_ref_media(media[0])
            done = ok
            if m:
                msgs.append(m)
        if msgs:
            self._edit_msg("\n".join(msgs), warn=True)
        return done

    def _probe_script(self, path: str):
        """=380 Q8: 読み込む前に検証する。戻り値 (候補の5つ組, None) /
        (None, エラー文言)。"""
        from .. import script_edit
        ttype = script_type_from_name(path)
        name = os.path.basename(path)
        if not os.path.isfile(path):
            return None, tr("スクリプトを読み込めません({0}): {1}").format(
                name, tr("ファイルが見つかりません"))
        try:
            if _is_csv(path):
                cols, _pts, _allp = script_edit.load_csv_points(path, 0)
                ch = (0, 1) if cols == 5 else 0
            else:
                script_edit.load_funscript_raw(path)
                ch = None
        except Exception as e:
            return None, tr("スクリプトを読み込めません({0}): {1}").format(
                name, e)
        return (ttype, path, 0.0, None, ch), None

    def _drop_scripts(self, paths, media):
        """スクリプトを検証して「対象」の一覧へ加え、先頭を開く。
        戻り値 (開いたか, メッセージの一覧)。"""
        entries, msgs = [], []
        for p in paths:
            e, err = self._probe_script(p)
            if err:
                msgs.append(err)
            else:
                entries.append(e)
        if not entries:
            return False, msgs
        # 未保存の確認(Q5 と同じ3択)。「やめる」なら何も変えない
        if not self._resolve_unsaved():
            return False, msgs
        # 参考メディア(単独起動のみ): 一緒に落とされたもの > 同じフォルダの対応品
        if getattr(self, "_standalone", False):
            ref = media[0] if media else find_media_for_script(
                entries[0][1],
                prefer_video=_pkg()._mpv_path_setting() is not None)
            cur = self.spec.get("video") or self.spec.get("audio") or ""
            same = bool(ref and cur and
                        os.path.normcase(os.path.abspath(ref)) ==
                        os.path.normcase(os.path.abspath(cur)))
            if ref and not same:
                _ok, m = self._set_ref_media(ref, reset_view=False)
                if m:
                    msgs.append(m)
        first = self._add_edit_tracks(entries)
        self._edit_load_track(first)
        return True, msgs

    def _add_edit_tracks(self, entries) -> int:
        """候補を「対象」の一覧へ加える(同じ種別・パスは差し替え)。先頭の番号。"""
        tracks = list(self._edit_tracks)
        first = None
        for e in entries:
            key = (e[0], os.path.normcase(os.path.abspath(e[1])))
            hit = None
            for i, t in enumerate(tracks):
                if (t[0], os.path.normcase(os.path.abspath(t[1]))) == key:
                    hit = i
                    break
            if hit is None:
                tracks.append(e)
                hit = len(tracks) - 1
            else:
                tracks[hit] = e
            if first is None:
                first = hit
        self._edit_tracks = tracks
        labels = [self._edit_track_label(i) for i in range(len(tracks))]
        self.edit_track_menu.configure(values=labels)
        self.edit_track_var.set(labels[first])
        self.edit_type_menu.pack_forget()
        self._edit_new = False
        return first

    # ---- 参考メディア(単独起動) ----
    def _set_ref_media(self, path: str, reset_view: bool = True):
        """参考の音声/動画を入れ替える(Q14: 1つだけ・確認なし・スクリプトは
        そのまま)。戻り値 (読み込めたか, メッセージ|"")。"""
        name = os.path.basename(path)
        if not os.path.isfile(path):
            return False, tr("音声ファイルが見つかりません: {0}").format(name)
        ext = os.path.splitext(path)[1].lower()
        self.pause()
        if ext in VIDEO_EXTS:
            if _pkg()._mpv_path_setting() is None:
                return False, tr("mpv が見つからないため、動画は再生できません"
                                 "（メイン画面の Settings でパスを指定してください）")
            self._edit_sound = None
            self._sound = None
            self._wave_src = None
            self._video = path
            self._video_lo = 0.0
            self._video_hi = None
            self._video_len = 0.0
            err = self._video_open(0.0)
            if err:
                self._video = ""
                return False, err
            self.spec["video"], self.spec["audio"] = path, ""
        else:
            snd, err = self._load_audio(path, 0.0, None)
            if err:
                return False, err
            self._close_ref_video()
            self._edit_sound = snd
            self.spec["audio"], self.spec["video"] = path, ""
        self._start_wave_env()           # =243: 波形(動画は無し=Q17)
        self._edit_refresh_duration()
        self._reset_clock()
        if reset_view:
            for g in self.edit_graphs:
                g.now_ms = 0.0
                g.initial_view(self._duration_ms)
        self._update_time(0.0, force=True)
        self._refresh_media_label()
        return True, ""

    def _close_ref_video(self):
        """動画から音声へ入れ替えるとき mpv の窓を閉じる(単独起動の mpv は
        この画面が持ち主=閉じれば quit される)。"""
        if not self._video:
            return
        self._video = ""
        self._video_len = 0.0
        m = getattr(self, "_mpv", None)
        self._mpv = None
        if m is not None and getattr(self, "_edit_mpv", None) is m:
            try:
                m.quit()
            except Exception:
                pass
            self._edit_mpv = None
        elif m is not None:
            try:
                m.set_pause(True)
            except Exception:
                pass
