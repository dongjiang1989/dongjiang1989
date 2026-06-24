#!/usr/bin/env python3
"""
GitHub Biweekly Activity Report Generator for dongjiang1989
Generates comprehensive biweekly markdown reports following the reference format.
"""

import os
import sys
import time
import json
import subprocess
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# ── Configuration ──────────────────────────────────────────────────────────────
USERNAME = "dongjiang1989"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
BASE_API = "https://api.github.com"
ANCHOR_DATE = datetime(2026, 6, 8, tzinfo=timezone.utc)  # Period anchor
GLOBAL_START = datetime(2025, 1, 1, tzinfo=timezone.utc)
REPORT_DIR = Path(__file__).resolve().parent.parent / "report"
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]

if not GITHUB_TOKEN:
    try:
        GITHUB_TOKEN = subprocess.check_output(
            ["gh", "auth", "token"], text=True
        ).strip()
    except Exception:
        print("ERROR: No GitHub token found. Set GITHUB_TOKEN or run `gh auth login`.")
        sys.exit(1)

session = requests.Session()
session.headers.update({
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "github-biweekly-report",
})


# ── Utility Functions ──────────────────────────────────────────────────────────

def api_get(endpoint, params=None, max_retries=3):
    """GET request to GitHub API with retry and rate-limit handling."""
    url = f"{BASE_API}{endpoint}" if not endpoint.startswith("http") else endpoint
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - int(time.time()), 5)
                print(f"  Rate limited. Waiting {wait}s ...")
                time.sleep(wait)
                continue
            elif resp.status_code == 422:
                return None
            else:
                print(f"  API {resp.status_code}: {endpoint}")
                return None
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            print(f"  Request error: {e}")
            return None
    return None


def search_api(query, per_page=100, media_type=None):
    """Search API with pagination support (returns up to per_page items)."""
    headers = {}
    if media_type:
        headers["Accept"] = media_type
    data = api_get("/search/" + query.split("?")[0],
                   params=dict(p.split("=") for p in query.split("?", 1)[1].split("&")) if "?" in query else None)
    return data


def search_items(endpoint, query, per_page=100, max_items=1000):
    """Paginated search returning list of items."""
    items = []
    page = 1
    while True:
        data = api_get(f"/search/{endpoint}", {
            "q": query, "per_page": min(per_page, 100), "page": page
        })
        if not data or not data.get("items"):
            break
        items.extend(data["items"])
        if len(items) >= max_items or len(data["items"]) < per_page:
            break
        page += 1
        time.sleep(2)  # respect search rate limit
    return items[:max_items]


def parse_dt(s):
    """Parse ISO datetime string."""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def fmt_date(d):
    """Format date as YYYY-MM-DD."""
    return d.strftime("%Y-%m-%d")


def fmt_short(d):
    """Format date as MM.DD."""
    return d.strftime("%m.%d")


def fmt_cn(d):
    """Format date as Chinese style: YYYY年M月D日."""
    return f"{d.year}年{d.month}月{d.day}日"


def repo_name_from_url(url):
    """Extract owner/repo from GitHub API URL."""
    if not url:
        return "unknown"
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return "unknown"


# ── Period Calculation ─────────────────────────────────────────────────────────

def generate_periods(anchor, global_start, ref_end):
    """
    Generate 14-day biweekly periods anchored at `anchor`.
    Goes backwards to global_start and forwards to ref_end.
    """
    periods = []

    # Backwards from anchor
    d = anchor
    while d >= global_start:
        ps = d
        pe = d + timedelta(days=13)
        periods.append((ps, pe))
        d -= timedelta(days=14)

    # Forwards from anchor + 14
    d = anchor + timedelta(days=14)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    while d <= today:
        ps = d
        pe = d + timedelta(days=13)
        periods.append((ps, pe))
        d += timedelta(days=14)

    periods.sort(key=lambda x: x[0])
    return periods


# ── Data Fetching ──────────────────────────────────────────────────────────────

def fetch_user_profile():
    return api_get(f"/users/{USERNAME}") or {}


def fetch_all_repos():
    repos = []
    page = 1
    while True:
        data = api_get(f"/users/{USERNAME}/repos", {"per_page": 100, "page": page, "sort": "updated"})
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    return repos


def fetch_starred_with_dates():
    items = []
    page = 1
    while True:
        data = api_get(f"/users/{USERNAME}/starred", {
            "per_page": 100, "page": page,
        }, )
        # Need special header
        url = f"{BASE_API}/users/{USERNAME}/starred"
        try:
            resp = session.get(url, params={"per_page": 100, "page": page},
                               headers={"Accept": "application/vnd.github.star+json"}, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if not data:
                    break
                items.extend(data)
                if len(data) < 100:
                    break
                page += 1
                time.sleep(1)
            else:
                break
        except Exception:
            break
    return items


def fetch_period_data(start, end):
    """Fetch all GitHub data for a biweekly period."""
    s = fmt_date(start)
    e = fmt_date(end)
    stats = {
        "prs": [], "issues": [], "commits": [],
        "events": [], "starred": [], "pr_details": {},
    }

    # PRs
    print(f"  Fetching PRs ({s} ~ {e}) ...")
    prs = search_items("issues",
                       f"author:{USERNAME} is:pr created:{s}..{e}", per_page=100)
    stats["prs"] = prs
    time.sleep(2)

    # Issues
    print(f"  Fetching Issues ({s} ~ {e}) ...")
    issues = search_items("issues",
                          f"author:{USERNAME} is:issue created:{s}..{e}", per_page=100)
    stats["issues"] = issues
    time.sleep(2)

    # Commits
    print(f"  Fetching Commits ({s} ~ {e}) ...")
    commits = search_items("commits",
                           f"author:{USERNAME} author-date:{s}..{e}", per_page=100)
    stats["commits"] = commits
    time.sleep(2)

    # Events (only recent ~90 days)
    events_90d_ago = datetime.now(timezone.utc) - timedelta(days=89)
    if start >= events_90d_ago or end >= events_90d_ago:
        print(f"  Fetching Events ...")
        page = 1
        all_events = []
        while page <= 3:
            data = api_get(f"/users/{USERNAME}/events", {"per_page": 100, "page": page})
            if not data:
                break
            all_events.extend(data)
            if len(data) < 100:
                break
            page += 1
            time.sleep(1)
        # Filter to period
        filtered = []
        for ev in all_events:
            created = parse_dt(ev.get("created_at"))
            if created and start <= created <= end + timedelta(days=1):
                filtered.append(ev)
        stats["events"] = filtered

    return stats


# ── Analysis Functions ─────────────────────────────────────────────────────────

def analyze_daily_activity(stats, start, end):
    """Build daily activity counts."""
    daily = defaultdict(int)
    daily_tasks = defaultdict(list)

    for pr in stats["prs"]:
        d = parse_dt(pr.get("created_at"))
        if d:
            key = fmt_date(d)
            daily[key] += 1
            daily_tasks[key].append(f"PR #{pr['number']} ({repo_name_from_url(pr.get('repository_url', ''))})")

    for issue in stats["issues"]:
        d = parse_dt(issue.get("created_at"))
        if d:
            key = fmt_date(d)
            daily[key] += 1
            daily_tasks[key].append(f"Issue #{issue['number']}")

    for commit in stats["commits"]:
        d = parse_dt(commit.get("commit", {}).get("author", {}).get("date"))
        if d:
            key = fmt_date(d)
            daily[key] += 1
            repo = commit.get("repository", {}).get("full_name", "")
            msg = commit.get("commit", {}).get("message", "")[:50]
            daily_tasks[key].append(f"{repo}: {msg}")

    return daily, daily_tasks


def analyze_repo_contributions(stats):
    """Group activity by repository."""
    repo_stats = defaultdict(lambda: {"prs": 0, "issues": 0, "commits": 0, "total": 0, "tasks": []})

    for pr in stats["prs"]:
        repo = repo_name_from_url(pr.get("repository_url", ""))
        repo_stats[repo]["prs"] += 1
        repo_stats[repo]["total"] += 1
        repo_stats[repo]["tasks"].append(f"PR #{pr['number']}: {pr['title'][:60]}")

    for issue in stats["issues"]:
        repo = repo_name_from_url(issue.get("repository_url", ""))
        repo_stats[repo]["issues"] += 1
        repo_stats[repo]["total"] += 1
        repo_stats[repo]["tasks"].append(f"Issue #{issue['number']}: {issue['title'][:60]}")

    for commit in stats["commits"]:
        repo = commit.get("repository", {}).get("full_name", "unknown")
        repo_stats[repo]["commits"] += 1
        repo_stats[repo]["total"] += 1
        msg = commit.get("commit", {}).get("message", "").split("\n")[0][:60]
        repo_stats[repo]["tasks"].append(f"Commit: {msg}")

    return dict(sorted(repo_stats.items(), key=lambda x: x[1]["total"], reverse=True))


def analyze_org_contributions(repo_stats):
    """Group activity by organization."""
    org_stats = defaultdict(lambda: {"total": 0, "repos": defaultdict(int), "types": set()})

    for repo, data in repo_stats.items():
        org = repo.split("/")[0] if "/" in repo else USERNAME
        org_stats[org]["total"] += data["total"]
        org_stats[org]["repos"][repo] = data["total"]
        if data["prs"]:
            org_stats[org]["types"].add("PR")
        if data["issues"]:
            org_stats[org]["types"].add("Issue")
        if data["commits"]:
            org_stats[org]["types"].add("Push")

    return dict(sorted(org_stats.items(), key=lambda x: x[1]["total"], reverse=True))


def categorize_prs(prs):
    """Categorize PRs into merged and open."""
    merged = []
    open_prs = []
    for pr in prs:
        if pr.get("state") == "closed" and pr.get("pull_request", {}).get("merged_at"):
            merged.append(pr)
        elif pr.get("state") == "open":
            open_prs.append(pr)
        elif pr.get("state") == "closed":
            merged.append(pr)  # closed PRs are typically merged
    return merged, open_prs


# ── Report Generation ──────────────────────────────────────────────────────────

def generate_report(start, end, stats, user_profile, all_repos, prev_stats=None, prev_start=None, prev_end=None):
    """Generate a complete biweekly report in markdown.
    prev_stats: stats dict for the previous biweekly period (for comparison).
    """
    s_str = fmt_short(start)
    e_str = fmt_short(end)
    period_cn = f"{fmt_cn(start)} — {fmt_cn(end)}"

    total_prs = len(stats["prs"])
    total_issues = len(stats["issues"])
    total_commits = len(stats["commits"])
    total_events = len(stats["events"])
    total_activity = total_prs + total_issues + total_commits

    # Analysis
    daily, daily_tasks = analyze_daily_activity(stats, start, end)
    repo_stats = analyze_repo_contributions(stats)
    org_stats = analyze_org_contributions(repo_stats)
    merged_prs, open_prs = categorize_prs(stats["prs"])

    total_repos_involved = len(repo_stats)
    total_orgs = len(org_stats)

    # Find peak day
    peak_day = max(daily.items(), key=lambda x: x[1]) if daily else ("N/A", 0)

    # Work distribution
    org_pcts = {}
    for org, data in org_stats.items():
        pct = data["total"] / max(total_activity, 1) * 100
        org_pcts[org] = pct

    lines = []

    # ── Header ──
    lines.append(f"# GitHub 工作总结报告：{USERNAME}")
    lines.append("")
    lines.append(f"> **统计周期**：{period_cn}")
    name = user_profile.get("name", USERNAME)
    bio = user_profile.get("bio", "") or ""
    company = user_profile.get("company", "") or ""
    location = user_profile.get("location", "") or ""
    public_repos = user_profile.get("public_repos", 0)
    followers = user_profile.get("followers", 0)
    following = user_profile.get("following", 0)
    identity_parts = [p for p in [company, location] if p]
    identity = " | ".join(identity_parts) if identity_parts else ""
    lines.append(f"> **用户**：[{USERNAME}](https://github.com/{USERNAME})（{name}）")
    if identity:
        lines.append(f"> **身份**：{identity}")
    lines.append(f"> **简介**：{bio}")
    lines.append(f"> **公开仓库数**：{public_repos} | **Followers**：{followers} | **Following**：{following}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 1: Summary & Analysis (MOVED TO FRONT) ──
    lines.append("## 一、总结与分析")
    lines.append("")

    lines.append("### 📈 关键数据")
    lines.append("")
    lines.append("| 维度 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 涉及组织数 | **{total_orgs}** 个 |")
    lines.append(f"| 涉及仓库数 | **{total_repos_involved}** 个 |")
    lines.append(f"| Pull Request 数 | **{total_prs}** 个（已合并 {len(merged_prs)}，开放 {len(open_prs)}） |")
    lines.append(f"| Issue 数 | **{total_issues}** 个 |")
    lines.append(f"| Commit 数 | **{total_commits}** 次 |")
    if total_events:
        lines.append(f"| 活动事件数 | **{total_events}** 次 |")
    lines.append(f"| 总活动量 | **{total_activity}** |")
    lines.append("")

    lines.append("### 🎯 工作重心分布")
    lines.append("")
    lines.append("```")
    for org, pct in sorted(org_pcts.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * max(1, int(pct / 5))
        lines.append(f"{org:<30} {bar:<20} {pct:.0f}%")
    lines.append("```")
    lines.append("")

    # Key summary points
    lines.append("### 💡 核心总结")
    lines.append("")
    summary_points = _generate_summary_points(org_stats, repo_stats, merged_prs, open_prs,
                                               total_activity, peak_day, stats)
    for i, point in enumerate(summary_points, 1):
        lines.append(f"{i}. {point}")
    lines.append("")

    # ── Biweekly Comparison (if previous period data available) ──
    if prev_stats is not None and prev_start is not None and prev_end is not None:
        prev_total = len(prev_stats["prs"]) + len(prev_stats["issues"]) + len(prev_stats["commits"])
        curr_total = total_activity
        prev_pr_count = len(prev_stats["prs"])
        prev_issue_count = len(prev_stats["issues"])
        prev_commit_count = len(prev_stats["commits"])

        def _trend(curr, prev):
            diff = curr - prev
            if diff > 0:
                return f"📈 +{diff}"
            elif diff < 0:
                return f"📉 {diff}"
            else:
                return "➡️ 持平"

        def _pct_change(curr, prev):
            if prev == 0:
                return "N/A" if curr == 0 else "🆕 新增"
            pct = (curr - prev) / prev * 100
            if pct > 0:
                return f"+{pct:.0f}%"
            elif pct < 0:
                return f"{pct:.0f}%"
            else:
                return "0%"

        lines.append("### 📊 与上一个双周对比")
        lines.append("")
        prev_period_str = f"{fmt_short(prev_start)} - {fmt_short(prev_end)}"
        curr_period_str = f"{fmt_short(start)} - {fmt_short(end)}"
        lines.append(f"> 对比周期：{prev_period_str} → {curr_period_str}")
        lines.append("")
        lines.append("| 指标 | 上双周 | 本双周 | 变化 | 趋势 |")
        lines.append("|------|--------|--------|------|------|")
        lines.append(f"| **总活动量** | {prev_total} | **{curr_total}** | {_pct_change(curr_total, prev_total)} | {_trend(curr_total, prev_total)} |")
        lines.append(f"| Pull Request | {prev_pr_count} | {total_prs} | {_pct_change(total_prs, prev_pr_count)} | {_trend(total_prs, prev_pr_count)} |")
        lines.append(f"| Issue | {prev_issue_count} | {total_issues} | {_pct_change(total_issues, prev_issue_count)} | {_trend(total_issues, prev_issue_count)} |")
        lines.append(f"| Commit | {prev_commit_count} | {total_commits} | {_pct_change(total_commits, prev_commit_count)} | {_trend(total_commits, prev_commit_count)} |")

        # Repo-level comparison
        prev_repo_stats = analyze_repo_contributions(prev_stats)
        prev_repos = set(prev_repo_stats.keys())
        curr_repos = set(repo_stats.keys())
        new_repos = curr_repos - prev_repos
        dropped_repos = prev_repos - curr_repos

        if new_repos or dropped_repos:
            lines.append("")
            if new_repos:
                lines.append(f"- 🆕 **新增活跃仓库**：{', '.join(f'`{r}`' for r in sorted(new_repos))}")
            if dropped_repos:
                lines.append(f"- ❌ **不再活跃的仓库**：{', '.join(f'`{r}`' for r in sorted(dropped_repos))}")

        # Top org comparison
        prev_org_stats = analyze_org_contributions(prev_repo_stats)
        prev_top_org = list(prev_org_stats.keys())[0] if prev_org_stats else "N/A"
        curr_top_org = list(org_stats.keys())[0] if org_stats else "N/A"
        if prev_top_org != curr_top_org:
            lines.append(f"- 🔄 **工作重心转移**：从 **{prev_top_org}** 转向 **{curr_top_org}**")
        else:
            lines.append(f"- ✅ **工作重心稳定**：继续聚焦 **{curr_top_org}**")

        lines.append("")

    lines.append("---")
    lines.append("")

    # ── Section 2: Summary Dashboard ──
    lines.append("## 二、总览仪表盘")
    lines.append("")
    lines.append("| 指标 | 数量 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| 📊 **总活动** | **{total_activity}** | PR + Issue + Commit |")
    lines.append(f"| 📝 **Pull Request** | {total_prs} | 已合并 {len(merged_prs)}，开放 {len(open_prs)} |")
    lines.append(f"| 📋 **Issue** | {total_issues} | 创建 Issue |")
    lines.append(f"| 💻 **Commit** | {total_commits} | 代码提交 |")
    if total_events:
        # Count event types
        event_types = defaultdict(int)
        for ev in stats["events"]:
            event_types[ev.get("type", "Other")] += 1
        for etype, count in sorted(event_types.items(), key=lambda x: x[1], reverse=True):
            emoji_map = {
                "PushEvent": "📝", "PullRequestEvent": "🔀",
                "IssuesEvent": "📋", "IssueCommentEvent": "💬",
                "CreateEvent": "🌿", "DeleteEvent": "🗑️",
                "PullRequestReviewEvent": "👀", "PullRequestReviewCommentEvent": "💭",
                "WatchEvent": "⭐", "ReleaseEvent": "🚀", "ForkEvent": "🍴",
                "PublicEvent": "🌐", "GollumEvent": "📖",
            }
            emoji = emoji_map.get(etype, "📌")
            desc_map = {
                "PushEvent": "代码推送", "PullRequestEvent": "PR 操作",
                "IssuesEvent": "Issue 操作", "IssueCommentEvent": "Issue 评论",
                "CreateEvent": "创建分支/标签", "DeleteEvent": "删除分支",
                "PullRequestReviewEvent": "PR Review", "PullRequestReviewCommentEvent": "PR Review 评论",
                "WatchEvent": "Star 收藏", "ReleaseEvent": "版本发布", "ForkEvent": "Fork",
                "PublicEvent": "仓库公开", "GollumEvent": "Wiki 编辑",
            }
            desc = desc_map.get(etype, etype)
            lines.append(f"| {emoji} **{etype}** | {count} | {desc} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 3: Daily Activity Heatmap ──
    lines.append("## 三、每日活动热力图")
    lines.append("")
    lines.append("| 日期 | 活动数 | 活跃度 | 主要工作 |")
    lines.append("|------|--------|--------|----------|")

    d = start
    while d <= end:
        date_str = fmt_date(d)
        weekday = WEEKDAY_CN[d.weekday()]
        count = daily.get(date_str, 0)
        if count == 0:
            bar = "░░░░░"
            tasks = "—"
        else:
            bar_len = min(count, 20)
            bar = "█" * bar_len + "░" * max(0, 5 - bar_len)
            task_list = daily_tasks.get(date_str, [])
            # Summarize tasks
            repos_in_day = set()
            for t in task_list:
                if "/" in t:
                    repo = t.split(":")[0].split("(")[-1].strip()
                    if "/" in repo:
                        repos_in_day.add(repo)
            if len(task_list) <= 3:
                tasks = "; ".join(t[:50] for t in task_list)
            else:
                tasks = f"{len(task_list)} 项活动"
                if repos_in_day:
                    tasks += f"（{', '.join(list(repos_in_day)[:3])}）"

        marker = "**" if count >= 5 else ""
        lines.append(f"| {marker}{date_str}（{weekday}）{marker} | {marker}{count}{marker} | {bar} | {tasks} |")
        d += timedelta(days=1)

    lines.append("")
    if peak_day[1] > 0:
        lines.append(f"> **活跃高峰**：{peak_day[0]}（{peak_day[1]} 次活动）为最高峰。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 4: Org/Repo Contribution Matrix ──
    lines.append("## 四、组织/仓库贡献矩阵")
    lines.append("")
    lines.append("### 4.1 按组织统计")
    lines.append("")
    lines.append("| 组织 | 活动数 | 占比 | 核心仓库 | 贡献类型 |")
    lines.append("|------|--------|------|----------|----------|")

    for org, data in org_stats.items():
        pct = data["total"] / max(total_activity, 1) * 100
        top_repos = sorted(data["repos"].items(), key=lambda x: x[1], reverse=True)[:3]
        repo_str = ", ".join(f"{r}({c})" for r, c in top_repos)
        types_str = ", ".join(sorted(data["types"]))
        highlight = "**" if pct > 20 else ""
        lines.append(f"| {highlight}{org}{highlight} | {data['total']} | {pct:.1f}% | {repo_str} | {types_str} |")

    lines.append("")
    lines.append("### 4.2 仓库详细 Top 10")
    lines.append("")
    lines.append("| 排名 | 仓库 | 活动数 | PR数 | Issue数 | Commit数 | 关键工作 |")
    lines.append("|------|------|--------|------|---------|---------|----------|")

    top_repos = list(repo_stats.items())[:10]
    for rank, (repo, data) in enumerate(top_repos, 1):
        # Key work: first task summary
        key_works = []
        for t in data["tasks"][:3]:
            short = t[:50]
            key_works.append(short)
        key_str = "; ".join(key_works) if key_works else "—"
        lines.append(f"| {rank} | **{repo}** | {data['total']} | {data['prs']} | {data['issues']} | {data['commits']} | {key_str} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 5: PR Detail ──
    lines.append("## 五、Pull Request 详细清单")
    lines.append("")

    lines.append("### 5.1 已合并/已关闭的 PR ✅")
    lines.append("")
    if merged_prs:
        lines.append("| # | 仓库 | PR | 标题 | 状态 |")
        lines.append("|---|------|----|------|------|")
        for i, pr in enumerate(merged_prs, 1):
            repo = repo_name_from_url(pr.get("repository_url", ""))
            num = pr["number"]
            title = pr["title"][:70]
            url = pr.get("html_url", "")
            state = "✅ 已合并" if pr.get("pull_request", {}).get("merged_at") else "🔒 已关闭"
            lines.append(f"| {i} | {repo} | [#{num}]({url}) | {title} | {state} |")
    else:
        lines.append("> 本周期无已合并的 PR。")
    lines.append("")

    lines.append("### 5.2 进行中 / 开放的 PR 🔄")
    lines.append("")
    if open_prs:
        lines.append("| # | 仓库 | PR | 标题 | 状态 |")
        lines.append("|---|------|----|------|------|")
        for i, pr in enumerate(open_prs, 1):
            repo = repo_name_from_url(pr.get("repository_url", ""))
            num = pr["number"]
            title = pr["title"][:70]
            url = pr.get("html_url", "")
            lines.append(f"| {i} | {repo} | [#{num}]({url}) | {title} | 🔄 开放 |")
    else:
        lines.append("> 本周期无开放的 PR。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 6: Issue Activity ──
    lines.append("## 六、Issue 活动清单")
    lines.append("")
    if stats["issues"]:
        lines.append("| # | 仓库 | Issue | 标题 | 状态 |")
        lines.append("|---|------|-------|------|------|")
        for i, issue in enumerate(stats["issues"], 1):
            repo = repo_name_from_url(issue.get("repository_url", ""))
            num = issue["number"]
            title = issue["title"][:70]
            url = issue.get("html_url", "")
            state = "🟢 开放" if issue["state"] == "open" else "✅ 已关闭"
            lines.append(f"| {i} | {repo} | [#{num}]({url}) | {title} | {state} |")
    else:
        lines.append("> 本周期无 Issue 活动。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 7: Key Project Analysis ──
    lines.append("## 七、关键项目深度分析")
    lines.append("")
    top_projects = list(repo_stats.items())[:5]
    if top_projects:
        for repo, data in top_projects:
            lines.append(f"### {repo}（{data['total']} 次活动）")
            lines.append("")
            if data["prs"]:
                lines.append(f"- **PR**: {data['prs']} 个")
            if data["issues"]:
                lines.append(f"- **Issue**: {data['issues']} 个")
            if data["commits"]:
                lines.append(f"- **Commit**: {data['commits']} 次")
            lines.append(f"- **主要工作**:")
            for task in data["tasks"][:5]:
                lines.append(f"  - {task[:80]}")
            lines.append("")
    else:
        lines.append("> 本周期无项目活动数据。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 8: Commit Activity ──
    lines.append("## 八、Commit 活动详情")
    lines.append("")
    if stats["commits"]:
        commit_by_repo = defaultdict(list)
        for c in stats["commits"]:
            r = c.get("repository", {}).get("full_name", "unknown")
            commit_by_repo[r].append(c)

        lines.append("| 仓库 | Commit数 | 最新提交 | 主要变更 |")
        lines.append("|------|---------|---------|---------|")
        for repo, commits in sorted(commit_by_repo.items(), key=lambda x: len(x[1]), reverse=True):
            latest = commits[0]
            latest_date = latest.get("commit", {}).get("author", {}).get("date", "")[:10]
            msgs = [c.get("commit", {}).get("message", "").split("\n")[0][:50] for c in commits[:3]]
            msg_str = "; ".join(msgs)
            lines.append(f"| {repo} | {len(commits)} | {latest_date} | {msg_str} |")
    else:
        lines.append("> 本周期无 Commit 活动。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 9: Star Activity ──
    lines.append("## 九、Star 活动（关注的仓库）")
    lines.append("")
    period_stars = stats.get("starred", [])
    if period_stars:
        lines.append("| 仓库 | 时间 | 说明 |")
        lines.append("|------|------|------|")
        for star in period_stars:
            repo_info = star.get("repo", {})
            full_name = repo_info.get("full_name", "")
            starred_at = star.get("starred_at", "")[:10]
            desc = repo_info.get("description", "") or ""
            lines.append(f"| [{full_name}](https://github.com/{full_name}) | {starred_at} | {desc[:60]} |")
    else:
        lines.append("> 本周期无 Star 活动记录（Star 数据仅近期可查）。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Section 10: Release Records ──
    lines.append("## 十、Release 发版记录")
    lines.append("")
    release_events = [e for e in stats.get("events", []) if e.get("type") == "ReleaseEvent"]
    if release_events:
        lines.append("| 仓库 | 版本 | 时间 |")
        lines.append("|------|------|------|")
        for ev in release_events:
            repo = ev.get("repo", {}).get("name", "")
            tag = ev.get("payload", {}).get("release", {}).get("tag_name", "")
            created = ev.get("created_at", "")[:10]
            lines.append(f"| {repo} | **{tag}** | {created} |")
    else:
        lines.append("> 本周期无 Release 记录（Release 数据仅在 Events API 可查时段内有效）。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Footer ──
    lines.append(f"*报告生成时间：{fmt_date(datetime.now(timezone.utc))} | 数据来源：GitHub Search API, Events API*")
    lines.append("")

    return "\n".join(lines)


def _generate_summary_points(org_stats, repo_stats, merged_prs, open_prs, total_activity, peak_day, stats):
    """Generate bullet-point summary for the analysis section."""
    points = []

    # Top org
    if org_stats:
        top_org = list(org_stats.keys())[0]
        top_org_data = org_stats[top_org]
        top_pct = top_org_data["total"] / max(total_activity, 1) * 100
        points.append(
            f"**{top_org}** 是最大贡献方向（{top_pct:.0f}%），"
            f"涉及 {len(top_org_data['repos'])} 个仓库，共 {top_org_data['total']} 次活动。"
        )

    # Merged PRs
    if merged_prs:
        repos_with_merged = set()
        for pr in merged_prs:
            repos_with_merged.add(repo_name_from_url(pr.get("repository_url", "")))
        points.append(
            f"共 **{len(merged_prs)}** 个 PR 已合并，覆盖 {len(repos_with_merged)} 个仓库。"
        )

    # Open PRs
    if open_prs:
        points.append(f"**{len(open_prs)}** 个 PR 仍在开放/Review 中。")

    # Peak day
    if peak_day[1] > 0:
        points.append(
            f"活跃高峰出现在 **{peak_day[0]}**（{peak_day[1]} 次活动），展现了高强度的工作节奏。"
        )

    # Issue count
    if stats["issues"]:
        points.append(f"创建了 **{len(stats['issues'])}** 个 Issue，涉及项目规划、Bug 追踪和社区互动。")

    # Commit count
    if stats["commits"]:
        commit_repos = set(c.get("repository", {}).get("full_name", "") for c in stats["commits"])
        points.append(f"在 {len(commit_repos)} 个仓库提交了 **{len(stats['commits'])}** 次代码。")

    return points


# ── Index Generation ───────────────────────────────────────────────────────────

def generate_index(reports):
    """Generate report/README.md index file.
    Also scans the report directory for any existing reports not in the current run.
    """
    # Merge with existing reports on disk
    import re
    existing = {}
    for period, filepath, counts in reports:
        existing[filepath.name] = (period, filepath, counts)

    # Scan report directory for all report files
    if REPORT_DIR.exists():
        pattern = re.compile(r"github_activity_report_(\d{4})\.(\d{2})\.(\d{2})_to_(\d{4})\.(\d{2})\.(\d{2})\.md")
        for f in REPORT_DIR.glob("github_activity_report_*.md"):
            if f.name not in existing:
                m = pattern.match(f.name)
                if m:
                    sy, sm, sd = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    ey, em, ed = int(m.group(4)), int(m.group(5)), int(m.group(6))
                    try:
                        ps = datetime(sy, sm, sd, tzinfo=timezone.utc)
                        pe = datetime(ey, em, ed, tzinfo=timezone.utc)
                        # Try to read counts from the file
                        pr_count, issue_count, commit_count = "—", "—", "—"
                        try:
                            content = f.read_text(encoding="utf-8")
                            # Look for the summary table row
                            pr_match = re.search(r"\| Pull Request \| (\d+)", content)
                            issue_match = re.search(r"\| Issue \| (\d+)", content)
                            commit_match = re.search(r"\| Commit \| (\d+)", content)
                            if pr_match:
                                pr_count = int(pr_match.group(1))
                            if issue_match:
                                issue_count = int(issue_match.group(1))
                            if commit_match:
                                commit_count = int(commit_match.group(1))
                        except Exception:
                            pass
                        existing[f.name] = ((ps, pe), f, (pr_count, issue_count, commit_count))
                    except ValueError:
                        pass

    all_reports = sorted(existing.values(), key=lambda x: x[0][0], reverse=True)

    lines = []
    lines.append("# GitHub 双周工作报告索引")
    lines.append("")
    lines.append(f"> 用户：[{USERNAME}](https://github.com/{USERNAME})")
    lines.append(f"> 统计范围：2025-01-01 至今")
    lines.append(f"> 报告数量：{len(all_reports)} 份")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("| 序号 | 统计周期 | 报告链接 | PR数 | Issue数 | Commit数 |")
    lines.append("|------|----------|----------|------|---------|---------|")

    for i, (period, filepath, counts) in enumerate(all_reports, 1):
        start, end = period
        period_str = f"{start.strftime('%Y.%m.%d')} - {end.strftime('%Y.%m.%d')}"
        filename = filepath.name
        lines.append(f"| {i} | {period_str} | [{filename}](./{filename}) | {counts[0]} | {counts[1]} | {counts[2]} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*索引更新时间：{fmt_date(datetime.now(timezone.utc))}*")
    lines.append("")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate GitHub biweekly reports")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD), overrides auto calculation")
    parser.add_argument("--end", help="End date (YYYY-MM-DD), overrides auto calculation")
    parser.add_argument("--periods", help="Comma-separated period ranges: 'start1..end1,start2..end2'")
    parser.add_argument("--all", action="store_true", help="Generate all periods from 2025-01-01 to today")
    parser.add_argument("--latest", action="store_true", help="Generate only the latest period")
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"GitHub Biweekly Report Generator")
    print(f"User: {USERNAME}")
    print(f"{'='*60}")
    print()

    # Fetch user profile
    print("Fetching user profile ...")
    user_profile = fetch_user_profile()
    print(f"  {user_profile.get('login', USERNAME)} | {user_profile.get('public_repos', 0)} repos | {user_profile.get('followers', 0)} followers")
    print()

    # Fetch repos
    print("Fetching all repos ...")
    all_repos = fetch_all_repos()
    print(f"  Found {len(all_repos)} repos")
    print()

    # Fetch starred repos (for recent periods)
    print("Fetching starred repos ...")
    all_starred = fetch_starred_with_dates()
    print(f"  Found {len(all_starred)} starred repos")
    print()

    # Determine periods to generate
    if args.periods:
        # Manual period specification
        periods = []
        for p in args.periods.split(","):
            parts = p.strip().split("..")
            if len(parts) == 2:
                ps = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                pe = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                periods.append((ps, pe))
    elif args.start and args.end:
        ps = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        pe = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        periods = [(ps, pe)]
    elif args.latest:
        # Find the latest period that includes today
        all_periods = generate_periods(ANCHOR_DATE, GLOBAL_START, datetime.now(timezone.utc))
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        periods = [p for p in all_periods if p[0] <= today <= p[1]]
        if not periods:
            periods = [all_periods[-1]]  # fallback to last period
    else:
        # Default: generate all periods
        periods = generate_periods(ANCHOR_DATE, GLOBAL_START, datetime.now(timezone.utc))

    print(f"Generating {len(periods)} biweekly report(s) ...")
    print()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_infos = []
    prev_stats = None
    prev_start = None
    prev_end = None

    for idx, (ps, pe) in enumerate(periods, 1):
        s_str = fmt_short(ps)
        e_str = fmt_short(pe)
        print(f"[{idx}/{len(periods)}] Period: {s_str} - {e_str}")

        # Fetch data
        stats = fetch_period_data(ps, pe)

        # Assign stars for this period
        period_stars = []
        for star in all_starred:
            starred_at = parse_dt(star.get("starred_at", ""))
            if starred_at and ps <= starred_at <= pe + timedelta(days=1):
                period_stars.append(star)
        stats["starred"] = period_stars

        total = len(stats["prs"]) + len(stats["issues"]) + len(stats["commits"])

        if total == 0 and not stats["events"]:
            print(f"  No activity found. Generating minimal report.")
            report = generate_report(ps, pe, stats, user_profile, all_repos,
                                     prev_stats=prev_stats, prev_start=prev_start, prev_end=prev_end)
            filename = f"github_activity_report_{ps.strftime('%Y.%m.%d')}_to_{pe.strftime('%Y.%m.%d')}.md"
            filepath = REPORT_DIR / filename
            filepath.write_text(report, encoding="utf-8")
            print(f"  Generated (empty): {filename}")
            report_infos.append(((ps, pe), filepath, (len(stats["prs"]), len(stats["issues"]), len(stats["commits"]))))
            # Update prev for next iteration
            prev_stats = stats
            prev_start = ps
            prev_end = pe
            print()
            continue

        # Generate report with comparison to previous period
        report = generate_report(ps, pe, stats, user_profile, all_repos,
                                 prev_stats=prev_stats, prev_start=prev_start, prev_end=prev_end)
        filename = f"github_activity_report_{ps.strftime('%Y.%m.%d')}_to_{pe.strftime('%Y.%m.%d')}.md"
        filepath = REPORT_DIR / filename
        filepath.write_text(report, encoding="utf-8")
        print(f"  Generated: {filename} (PRs:{len(stats['prs'])}, Issues:{len(stats['issues'])}, Commits:{len(stats['commits'])})")
        report_infos.append(((ps, pe), filepath, (len(stats["prs"]), len(stats["issues"]), len(stats["commits"]))))
        # Update prev for next iteration
        prev_stats = stats
        prev_start = ps
        prev_end = pe
        print()

    # Generate index
    print("Generating index (report/README.md) ...")
    index_content = generate_index(report_infos)
    index_path = REPORT_DIR / "README.md"
    index_path.write_text(index_content, encoding="utf-8")
    print(f"  Generated: report/README.md ({len(report_infos)} reports)")
    print()
    print("Done!")


if __name__ == "__main__":
    main()
