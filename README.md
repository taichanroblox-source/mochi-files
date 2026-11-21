# mochi-files

ファイルを、もっと自由に。もち系デザインのファイルツール。

## 実装済み（初期版）
- PCサイドバー / スマホ・iPad下部タブ
- ブラウザ内保存（IndexedDB）、お気に入り、タグ、検索、並び替え、端末へのダウンロード
- 画像変換（PNG/JPG/WebP）
- ZIP作成・解凍（JSZip、対応ブラウザでフォルダ選択）
- 公開Scratch作品の情報表示・SB3取得・複数作品のZIP一括ダウンロード
- ファイル共有・動画音声変換は今後実装（現時点では機能しません）

## Render公開
- GitHubリポジトリをWeb Serviceとして接続
- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
- Health Check Path: `/health`

## ローカル起動
```sh
pip install -r requirements.txt
python server.py
```

ファイルはクラウドに保存しません。ブラウザのデータを削除すると消える可能性があります。Scratch APIの仕様や制限によって公開作品も取得できない場合があります。
