# ADR 0001: raw データを GitHub Actions で取得し Git リポジトリに Parquet で蓄積する

- Status: Accepted
- Date: 2026-11-24

## Context
- データモデリングを継続的に練習するため、自動で増え続けるデータが必要。
- 制約: すべて無料 / ローカル PC は常時起動していないためクラウドで定期実行したい。

## Decision
- GitHub Actions（public リポジトリ、標準ランナーは無料）で毎日 dlt を実行し、GitHub API のデータを取得する。
- 出力は dlt の filesystem destination で Parquet にし、このリポジトリの `data/raw/` に commit する。
- データ専用リポジトリとし、モデリング（dbt）は別リポジトリ `oss-github-dwh` に分離する（「ソースシステム」と「DWH」の分離）。
- raw は **append-only**。上書き・削除はしない。同一 ID の複数バージョンや境界の再取得による重複は raw に残す。
- DWH（DuckDB）は raw からいつでも再構築できるものとし、状態を持たせない。履歴（SCD 等）は raw の積み上げから組み立てる。
- 増分カーソルは dlt の state（`data/raw/github/_dlt_pipeline_state/`）に保存され、使い捨てランナーでも復元される。

## Layout
```
data/raw/github/<table>/dt=YYYY-MM-DD/<load_id>.<file_id>.parquet
data/raw/github/_dlt_loads/            # ロード履歴
data/raw/github/_dlt_pipeline_state/   # 増分カーソル
data/raw/github/_dlt_version/          # スキーマのバージョン
data/_meta/repo_size.csv               # 容量推移
```

## Consequences
- (+) 完全無料、コードとデータの履歴が Git に残る。
- (−) Git は過去のファイルを消せないため容量は単調増加。推奨上限 1GB に注意し `repo_size.csv` で監視する。
- (−) 公開リポジトリでは 60 日間活動がないと schedule が停止する（毎日の commit で通常は回避される）。
- (−) dbt snapshot は使えない（DWH が毎回再構築されるため）。
