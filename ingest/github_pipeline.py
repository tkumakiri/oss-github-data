"""GitHub API から OSS リポジトリのデータを取得し、data/raw に Parquet で追記する。

設計方針（docs/adr/0001 参照）
- raw は append-only。上書き・削除しない。重複（同一IDの複数バージョン）は raw に残り、dbt 側で扱う。
- 増分カーソルはリポジトリ単位で dlt の state に保存する。state は destination（data/raw）にも
  書かれるので、使い捨ての Actions ランナーでも前回の続きから取得できる。

環境変数
- GITHUB_TOKEN           : API トークン（Actions では自動付与。無しだと 60 req/h）
- RAW_BUCKET_URL         : 出力先（既定: <repo>/data/raw）。ローカル検証時は別ディレクトリを指定する
- ONLY_REPOS             : カンマ区切りで対象を絞る（検証用）
- INITIAL_LOOKBACK_DAYS  : 新規リポジトリの Issue/PR 初回取得期間（既定: 30）
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import dlt
import yaml
from dlt.sources.helpers.rest_client import RESTClient
from dlt.sources.helpers.rest_client.auth import BearerTokenAuth
from dlt.sources.helpers.rest_client.paginators import HeaderLinkPaginator

ROOT = Path(__file__).resolve().parent.parent
API_BASE = "https://api.github.com"
PER_PAGE = 100
RELEASES_MAX_PAGES = 10
RATE_LIMIT_FLOOR = 20

# 容量削減のため除外する大きなテキスト列
DROP_FIELDS = {"body", "body_html", "body_text"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_repos() -> list[str]:
    repos = yaml.safe_load((ROOT / "repos.yml").read_text())["repositories"]
    only = os.environ.get("ONLY_REPOS")
    if only:
        wanted = {r.strip() for r in only.split(",") if r.strip()}
        repos = [r for r in repos if r in wanted]
    return repos


def make_client() -> RESTClient:
    token = os.environ.get("GITHUB_TOKEN")
    return RESTClient(
        base_url=API_BASE,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        auth=BearerTokenAuth(token) if token else None,
        paginator=HeaderLinkPaginator(),
    )


def _respect_rate_limit(response: Any) -> None:
    """残りリクエスト数が少なくなったらリセット時刻まで待つ（初回の大量取得対策）。"""
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset = response.headers.get("X-RateLimit-Reset")
    if remaining is None or reset is None or int(remaining) > RATE_LIMIT_FLOOR:
        return
    wait = max(0, int(reset) - int(time.time())) + 5
    print(f"rate limit nearly exhausted (remaining={remaining}); sleeping {wait}s")
    time.sleep(wait)


def _clean(record: dict[str, Any], repo: str, extracted_at: str) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if k not in DROP_FIELDS}
    out["_repo_full_name"] = repo
    out["_extracted_at"] = extracted_at
    return out


@dlt.source(name="github")
def github_source(repos: list[str], lookback_days: int):
    client = make_client()
    extracted_at = _now_iso()

    @dlt.resource(name="repositories", write_disposition="append")
    def repositories() -> Iterator[dict[str, Any]]:
        """毎回、対象リポジトリの現在の状態を全量取得する（日次スナップショット）。"""
        for repo in repos:
            resp = client.get(f"/repos/{repo}")
            resp.raise_for_status()
            _respect_rate_limit(resp)
            yield _clean(resp.json(), repo, extracted_at)

    @dlt.resource(name="issues", write_disposition="append")
    def issues() -> Iterator[list[dict[str, Any]]]:
        """前回以降に更新された Issue / PR を取得する（PR は pull_request 列を持つ）。

        since は「以上」で比較されるため、境界の1件は次回も再取得される（raw 上の重複として許容）。
        """
        state = dlt.current.resource_state().setdefault("since_by_repo", {})
        default_since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for repo in repos:
            since = state.get(repo, default_since)
            max_updated = since
            for page in client.paginate(
                f"/repos/{repo}/issues",
                params={
                    "state": "all",
                    "since": since,
                    "sort": "updated",
                    "direction": "asc",
                    "per_page": PER_PAGE,
                },
            ):
                _respect_rate_limit(page.response)
                rows = [_clean(r, repo, extracted_at) for r in page]
                if rows:
                    max_updated = max(max_updated, max(r["updated_at"] for r in rows))
                    yield rows
            state[repo] = max_updated

    @dlt.resource(name="releases", write_disposition="append")
    def releases() -> Iterator[list[dict[str, Any]]]:
        """前回以降に作成されたリリースを取得する。初回は API 上限（直近 1000 件）まで。

        既知の制約: 作成済みリリースの編集（名称変更・prerelease 解除等）は取得しない。
        初回 1000 件より古いリリースは取得できない。
        """
        state = dlt.current.resource_state().setdefault("last_created_by_repo", {})
        for repo in repos:
            last_created = state.get(repo)
            newest = last_created
            done = False
            for page_no, page in enumerate(
                client.paginate(f"/repos/{repo}/releases", params={"per_page": PER_PAGE}),
                start=1,
            ):
                _respect_rate_limit(page.response)
                rows = []
                for r in page:
                    if last_created and r["created_at"] <= last_created:
                        done = True
                        continue
                    rows.append(_clean(r, repo, extracted_at))
                if rows:
                    page_max = max(r["created_at"] for r in rows)
                    newest = page_max if newest is None else max(newest, page_max)
                    yield rows
                # GitHub のリスト API は 1000 件（10 ページ）までしか返さない
                if done or page_no >= RELEASES_MAX_PAGES:
                    break
            if newest:
                state[repo] = newest

    return repositories, issues, releases


def main() -> None:
    bucket_url = os.environ.get("RAW_BUCKET_URL", str(ROOT / "data" / "raw"))
    lookback_days = int(os.environ.get("INITIAL_LOOKBACK_DAYS", "30"))
    repos = load_repos()

    pipeline = dlt.pipeline(
        pipeline_name="github_raw",
        pipelines_dir=str(ROOT / ".dlt_pipelines"),
        destination=dlt.destinations.filesystem(
            bucket_url=bucket_url,
            layout="{table_name}/dt={YYYY}-{MM}-{DD}/{load_id}.{file_id}.{ext}",
        ),
        dataset_name="github",
    )
    info = pipeline.run(
        github_source(repos, lookback_days), loader_file_format="parquet"
    )
    print(info)
    for table, count in (pipeline.last_trace.last_normalize_info.row_counts or {}).items():
        print(f"  {table}: {count} rows")


if __name__ == "__main__":
    main()
