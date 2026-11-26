# oss-github-data

有名 OSS リポジトリの GitHub データを毎日取得し、`data/raw/` に Parquet で蓄積するデータ専用リポジトリ。
モデリングは [`oss-github-dwh`](../oss-github-dwh) で行う。

- 仕組み: GitHub Actions（毎日 06:17 JST） → dlt → `data/raw/github/<table>/dt=YYYY-MM-DD/*.parquet` → commit
- 設計判断: [`docs/adr/`](docs/adr/)
- 対象リポジトリ: [`repos.yml`](repos.yml)

## ローカル実行（検証用）

```sh
uv sync
# data/raw を汚さないよう別ディレクトリに出力し、対象を絞る
RAW_BUCKET_URL=/tmp/raw_test ONLY_REPOS=duckdb/duckdb INITIAL_LOOKBACK_DAYS=3 \
  uv run python ingest/github_pipeline.py
```

`GITHUB_TOKEN` を設定しない場合は 60 req/h に制限される。

## 運用
- 手動実行: Actions → ingest → Run workflow（`only_repos` で対象を絞れる）
- 対象追加: `repos.yml` に 1 行追加して push
- 容量監視: `data/_meta/repo_size.csv`
