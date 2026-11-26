# ADR 0002: 取得対象と取得範囲（v1）

- Status: Accepted
- Date: 2026-11-24

## Decision
### 対象リポジトリ
`repos.yml` で管理する。データ系を中心に幅広い有名 OSS 26 件。
超大規模リポジトリ（pytorch, rust, kubernetes, vscode, cpython 等）は容量を圧迫するため初期は除外し、容量推移を見て追加を判断する。

### テーブル
| テーブル | 取得方法 | 備考 |
|---|---|---|
| repositories | 毎回全量（日次スナップショット） | `topics` は子テーブル `repositories__topics` |
| issues | `since`（updated_at）による増分。リポジトリ単位のカーソル | PR も含む（`pull_request__*` 列の有無で判別）。境界の 1 件は次回も再取得される |
| releases | created_at による増分。リポジトリ単位のカーソル | 初回は API 上限の直近 1000 件まで |

- 共通の付与列: `_repo_full_name`, `_extracted_at`（取得時刻 UTC）, dlt の `_dlt_load_id`, `_dlt_id`。
- `body` 等の本文テキストは容量削減のため除外。
- 新規追加リポジトリの Issue/PR は初回のみ直近 30 日分（`INITIAL_LOOKBACK_DAYS`）。追加以前の履歴は存在しない。

### 既知の制約
- 作成済みリリースの編集は取得しない。
- 削除された Issue / リリースは検知できない。
- `GITHUB_TOKEN` のレート制限（1,000 req/h/リポジトリ）に近づいた場合はリセットまで待機する。

## Future
コントリビューター、スター履歴、PR レビュー、コミットなどは後から「ソース追加」として拡張する。
