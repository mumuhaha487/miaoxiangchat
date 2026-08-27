<p align="center">
  <img src="frontend/public/assets/site-logo.jpg" alt="妙想之地のロゴ" width="144">
</p>

<h1 align="center">妙想之地 AI ワークスペース</h1>

<p align="center">Chat、Hermes Agent、実ブラウザー調査、定期タスク、マルチクライアント連携を統合したセルフホスト環境</p>

<p align="center">
  <a href="README.md">中文</a> ·
  <a href="README.en.md">English</a> ·
  <a href="README.ja.md">日本語</a>
</p>

## デモ利用の申請

- テスト Demo を利用する場合は、[vrhjio4405@163.com](mailto:vrhjio4405@163.com) へメールしてください。申請確認後、メンテナーが Demo のアドレスとアクティベーションコードをメールで案内します。
- Agent モードは CPU、メモリー、ブラウザー資源を多く使用するため、継続利用にはセルフホストを推奨します。申請メールには、**共有テスト環境の容量・安定性・資源制限を了承する**旨を明記してください。

> デモは評価専用です。パスワード、秘密鍵、機密文書などの重要情報をアップロードしないでください。

## 画面プレビュー

### デスクトップ Agent ワークスペース

![デスクトップ Agent ワークスペース](docs/images/workspace-desktop.png)

### モバイル画面と Top Dock 設定

<table>
  <tr>
    <td width="50%"><img src="docs/images/workspace-mobile.png" alt="モバイル Agent ワークスペース"></td>
    <td width="50%"><img src="docs/images/settings-dock.png" alt="Top Dock 表示設定"></td>
  </tr>
</table>

### 管理画面のモデル割り当て

![管理画面のモデル割り当て](docs/images/admin-model-routing.png)

スクリーンショットにはデモ用アカウントとタスクを使用しており、実際の会話、API Key、本番データは含まれていません。

## 概要

妙想之地は、単なるチャット UI や検索 API のラッパーではありません。通常の Chat、Hermes Agent、実ブラウザー、統括モデル、実行モデル、成果物検査、定期タスク、複数クライアントを一つのアカウント・権限・会話基盤にまとめます。継続的な調査、文書作成、開発支援、Web 操作、制御された PC 操作に適したセルフホスト型ワークスペースです。

## 主な特長

- **実ブラウザーによる調査**：隔離 Chromium、CDP、スクリーンショット、ページ操作を利用し、モデルの記憶や検索要約だけに依存しません。
- **Hermes の長時間タスク**：ユーザーごとのファイル、端末、スキル、永続ジョブ、Cron タスクを扱えます。
- **モデルの役割分離**：Chat、統括、実行モデルを個別に設定でき、計画・検査とツール実行で別のモデルを使えます。
- **成果物の品質ゲート**：PPTX、DOCX、動画などに明確なファイル契約を設け、不足や形式不一致の場合は修正工程へ戻せます。
- **根拠優先ワークフロー**：レポートや文書を作成する前に、ブラウザーでの確認と情報源の照合を要求できます。
- **選択的メモリー**：新規会話は既定でクリーンに保ち、必要な履歴だけを取得します。
- **マルチクライアント共有**：Web、Android、Windows、WeChat でアカウント、会話、権限を共有します。
- **制御された PC 操作**：Windows Agent は UI Automation、OCR、画面認識、任意の ADB を組み合わせ、承認と緊急停止を利用できます。
- **Basic/VIP のサーバー側分離**：Basic ユーザーは管理者が許可した Chat のみ使用でき、Agent ツールは認証済み VIP に限定されます。
- **導入単位の暗号分離**：導入ごとにアプリ、内部サービス、アクティベーション用の秘密情報を独立生成します。

## アーキテクチャ

```text
Web / Android / Windows / WeChat
                 |
              FastAPI
       +---------+----------+
       |         |          |
     Chat    Coordinator  Task Queue
                            |
                    Isolated Hermes Worker
                       |             |
                 Browser Runtime   Workspace
```

| ディレクトリ | 役割 |
| --- | --- |
| `backend/` | FastAPI、SQLite、認証、権限、モデル経路、スケジュール、成果物検査 |
| `frontend/` | React/Vite のレスポンシブ画面と独立管理コンソール |
| `hermes-worker/` | 隔離 Hermes Worker、組み込みスキル、ブラウザーツール |
| `browser-runtime/` | Chromium、CDP、VNC/noVNC 実行環境 |
| `android-app/` | Android ネイティブコンテナー、セッション、通知、ファイル、更新 |
| `windows-client/` | Windows ワークスペースと UIA/OCR/ADB Computer Agent |
| `wechat-mini-program/` | WeChat ログインゲートとワークスペース |
| `activation-manager/` | 管理 API を使う VIP コード管理ツール |
| `proxy-bridge/` | 任意のホストプロキシーブリッジ |

## クイックスタート

必要環境：Linux、Bash、Docker Engine、Docker Compose v2。依存ディレクトリ、実行データ、ビルド済みクライアントはソース ZIP に含まれません。

```bash
unzip miaoxiang.zip
cd miaoxiang
bash scripts/start.sh
```

`.env` がない場合、`start.sh` は対話式初期化を実行します。初期化済みの場合はそのまま起動します。入力項目は次のとおりです。

1. アプリ名、配置先、Web ポート、公開 HTTPS URL
2. 管理者ユーザー名とパスワード
3. 実行モデルの API URL、モデル名、API Key
4. Chat 専用モデルを使用するか、および接続情報
5. 統括モデルが必要か、有効にするか、および接続情報
6. 任意の SMTP 設定
7. 任意の WeChat AppID、AppSecret、クラウドブリッジ設定

`https://api.example.com/v1`、`your-model-name`、`sk-your-key` は形式を示す例であり、実在の接続情報ではありません。

```bash
bash scripts/start.sh   # 必要なら初期化して起動
bash scripts/status.sh  # この導入の状態を表示
bash scripts/stop.sh    # 永続データを保持して停止
```

## モデルと権限

モデル接続は OpenAI Chat Completions 互換 API を使用します。

| 役割 | 用途 | 初期設定 |
| --- | --- | --- |
| Chat | ゲスト、Basic、通常会話 | 専用接続または実行モデルを継承 |
| Coordinator | 計画、分担、最終検査、修正指示 | 無効化または専用接続 |
| Executor | Agent のツール呼び出しとタスク実行 | 主モデル |

Basic ユーザーは管理者が許可したモデルで Chat のみ利用できます。Agent、ブラウザー、端末、ファイル、定期タスク、PC 操作にはログインと有効な VIP 権限が必要です。管理者はユーザー単位の権限と三種類のモデル接続を個別に変更できます。

## 動的アクティベーション保護

- 初回初期化時に導入固有の `ACTIVATION_SECRET` を生成し、ソースコードには固定値を置きません。
- バックエンドを起動するたびに新しい暗号エポックを作成し、コード用ハッシュ方式と登録リンク用認証方式をランダムに選びます。どちらも直前と同じ方式にはなりません。
- HMAC-SHA-256、HMAC-SHA3-256、鍵付き BLAKE2 系列から、ハッシュ用と認証用の独立した集合で選択します。
- エポックごとに新しいランダム salt を生成し、導入ルート秘密から専用キーを導出します。データベースにコードの平文は保存しません。
- エポック状態は Git 対象外の実行データ領域に保存し、完全性を認証します。有効な既存コードを検証できるよう過去のエポックも保持します。
- マシンごとにルート秘密とエポック状態が異なるため、同じソースから導入してもコードを別環境へ移用できません。

## セキュリティとプライバシー

- `.env`、DB、会話、ログ、ユーザーワークスペース、モデル URL、API Key、署名ファイル、ビルド成果物をコミットしないでください。
- 初期化はアプリ、アクティベーション、ブラウザー、Hermes、任意の WeChat ブリッジ秘密を生成し、`.env` を `0600` で保存します。
- モデル API Key は暗号化して保存し、管理 API は設定済みかどうかだけを返します。
- Backend、CDP、VNC、Worker のポートはコンテナーネットワーク内に置き、外部には Web 入口だけを公開してください。
- Hermes 状態、ブラウザープロファイル、添付ファイル、ワークスペースはユーザーごとに分離します。
- 公開前に GitHub Secret Scanning を有効化し、Git 履歴も確認してください。現在のファイル削除だけでは過去コミットの秘密は消えません。

## クライアント設定

- Web：ビルド時に `VITE_PUBLIC_APP_ORIGIN=https://your-domain.example` を設定します。
- Android：`AICHAT_PUBLIC_ORIGIN=https://your-domain.example` を設定し、署名情報は環境変数だけで渡します。
- Windows：初回起動前に `MIAOXIANG_SERVER_URL=https://your-domain.example` を設定するか、`--server` で上書きします。
- WeChat Mini Program：公開 URL とクラウド環境を設定し、秘密値はプラットフォーム環境変数で注入します。

## Upstream

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [noVNC](https://github.com/novnc/noVNC)
- [Playwright](https://playwright.dev/)

## ライセンスと責任

リポジトリに含まれるライセンスに従って利用してください。モデル認証情報、ユーザーデータ、公開入口の保護、および接続先サービスの利用規約遵守は導入者の責任です。
