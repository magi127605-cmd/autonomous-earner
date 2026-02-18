"""
Autonomous Earner - 24/7 PDCA Daemon
=====================================
30分ごとにClaude Code CLI (Sonnet 4.6) を起動し、
AIが自律的に市場調査・コンテンツ生成・デプロイ・分析を行う。

起動: python daemon.py
停止: Ctrl+C or .env の ENABLED=false

スケジュール:
  火・水: フル稼働（24h）
  木・金・土・日・月: 18:00〜翌7:00のみ稼働（9-18時はオーナーが使うため停止）
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ================================
# パス設定
# ================================
PROJECT_DIR = Path(__file__).resolve().parent
STATE_DIR = PROJECT_DIR / "state"
LOG_DIR = PROJECT_DIR / "logs"
ENV_FILE = PROJECT_DIR / ".env"

# ================================
# ログ設定
# ================================
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("autonomous-earner")
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = RotatingFileHandler(
    LOG_DIR / "daemon.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

if sys.stderr is not None:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    logger.addHandler(_console_handler)

# ================================
# 設定
# ================================
CYCLE_INTERVAL = 1800  # 30分（秒）
CLAUDE_TIMEOUT = 600   # 10分（1セッションの最大実行時間）
MAX_SESSIONS_PER_DAY = 48
CLAUDE_MODEL = "sonnet"  # Sonnet 4.6 — Opus級の性能、低コスト
MAX_CONSECUTIVE_ERRORS = 3

JST = timezone(timedelta(hours=9))

# スケジュール: 火(1)・水(2) はフル稼働、それ以外は 18:00-翌7:00 のみ稼働
# weekday(): 月=0, 火=1, 水=2, 木=3, 金=4, 土=5, 日=6
FULL_DAYS = {1, 2}         # 火・水
BLOCKED_HOUR_START = 7     # この時刻以降は停止（JST）
BLOCKED_HOUR_END = 18      # この時刻以降は稼働再開（JST）


def load_env() -> dict:
    """シンプルな.env読み込み"""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def is_enabled() -> bool:
    """.env の ENABLED フラグを確認"""
    env = load_env()
    return env.get("ENABLED", "true").lower() in ("true", "1", "yes")


def is_in_blocked_schedule() -> bool:
    """オーナーの作業時間帯かどうか判定（JSTベース）"""
    now_jst = datetime.now(JST)
    weekday = now_jst.weekday()

    # 火・水はフル稼働 → ブロックなし
    if weekday in FULL_DAYS:
        return False

    # それ以外の曜日: 7:00〜18:00 はブロック
    hour = now_jst.hour
    if BLOCKED_HOUR_START <= hour < BLOCKED_HOUR_END:
        return True

    return False


def seconds_until_unblocked() -> int:
    """ブロック解除まで何秒か（概算）"""
    now_jst = datetime.now(JST)
    # 今日の18:00まで
    unblock_time = now_jst.replace(hour=BLOCKED_HOUR_END, minute=0, second=0, microsecond=0)
    if now_jst >= unblock_time:
        # 既に過ぎている場合は明日の18:00（ただし明日がフル稼働日なら0）
        return 60  # 1分後に再チェック
    diff = (unblock_time - now_jst).total_seconds()
    return max(int(diff), 60)


def get_session_count_today() -> int:
    """今日のセッション実行回数を取得"""
    counter_file = STATE_DIR / "session_counter.json"
    if not counter_file.exists():
        return 0
    try:
        data = json.loads(counter_file.read_text(encoding="utf-8"))
        if data.get("date") == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
            return data.get("count", 0)
        return 0
    except Exception:
        return 0


def increment_session_count():
    """セッション実行回数をインクリメント"""
    counter_file = STATE_DIR / "session_counter.json"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = get_session_count_today() + 1
    counter_file.write_text(
        json.dumps({"date": today, "count": count}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_next_task() -> dict | None:
    """queue.json から最初の pending タスクを取得"""
    queue_file = STATE_DIR / "queue.json"
    if not queue_file.exists():
        return None
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        for task in data.get("tasks", []):
            if task.get("status") == "pending":
                return task
    except Exception:
        pass
    return None


def get_strategy_phase() -> str:
    """strategy.json からフェーズを取得"""
    strategy_file = STATE_DIR / "strategy.json"
    if not strategy_file.exists():
        return "init"
    try:
        data = json.loads(strategy_file.read_text(encoding="utf-8"))
        return data.get("phase", "init")
    except Exception:
        return "init"


def get_session_count() -> int:
    """memory.json から累計セッション数を取得"""
    memory_file = STATE_DIR / "memory.json"
    if not memory_file.exists():
        return 0
    try:
        data = json.loads(memory_file.read_text(encoding="utf-8"))
        return data.get("session_count", 0)
    except Exception:
        return 0


def build_task_prompt(task: dict) -> str:
    """タスクを具体的なアクション指示に変換"""
    task_type = task.get("type", "")
    params = task.get("params", {})
    task_id = task.get("id", 0)

    if task_type == "deploy":
        return (
            f"You are an autonomous AI. Execute task #{task_id}: DEPLOY.\n"
            f"Steps:\n"
            f"1. cd into the site/ directory and run: npm run build\n"
            f"2. cd back to the project root\n"
            f"3. git add -A && git commit -m 'Auto-deploy: update site' && git push\n"
            f"4. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"5. Update state/metrics.json: increment total_deploys\n"
            f"6. Update state/memory.json: increment session_count, update last_session\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "write_article":
        keyword = params.get("keyword", "AI tools")
        article_type = params.get("type", "review")
        target_words = params.get("target_words", 2000)
        return (
            f"You are an autonomous AI. Execute task #{task_id}: WRITE ARTICLE.\n"
            f"Topic: \"{keyword}\" (type: {article_type}, target: {target_words} words)\n"
            f"Steps:\n"
            f"1. Use WebSearch to research the top 3 articles for \"{keyword}\"\n"
            f"2. Write a comprehensive, SEO-optimized article that is BETTER than what exists\n"
            f"3. Save it to site/src/content/blog/ with filename format: YYYY-MM-DD-slug.md\n"
            f"4. Include frontmatter: title, description, pubDate\n"
            f"5. git add the new article, commit with message 'Add article: {keyword}', and git push\n"
            f"6. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"7. Update state/metrics.json: increment articles_written and total_articles\n"
            f"8. Update state/memory.json: increment session_count, update last_session\n"
            f"9. Add any new article ideas to state/queue.json as new pending tasks\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "schema_update":
        desc = params.get("description", "Update schema")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: SCHEMA UPDATE.\n"
            f"Description: {desc}\n"
            f"Steps:\n"
            f"1. Read the current site/src/content.config.ts (or equivalent content config)\n"
            f"2. Make the described changes\n"
            f"3. Ensure existing articles still work with the new schema\n"
            f"4. git add, commit, push\n"
            f"5. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"6. Update state/memory.json: increment session_count, update last_session\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "research":
        desc = params.get("description", "Market research")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: RESEARCH.\n"
            f"Topic: {desc}\n"
            f"Steps:\n"
            f"1. Use WebSearch to research the topic thoroughly\n"
            f"2. Save findings to research/ directory as a markdown file\n"
            f"3. Based on findings, add new actionable tasks to state/queue.json\n"
            f"4. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"5. Update state/beliefs.json if findings change any hypotheses\n"
            f"6. Update state/memory.json: increment session_count, update last_session, add to learned[]\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "check_performance":
        return (
            f"You are an autonomous AI. Execute task #{task_id}: CHECK PERFORMANCE.\n"
            f"This is the MOST IMPORTANT task for self-growth. You must measure real results.\n"
            f"Steps:\n"
            f"1. Read state/metrics.json to see current stats\n"
            f"2. Use WebSearch: site:magi127605-cmd.github.io/autonomous-earner — count how many pages are indexed\n"
            f"3. For each article in site/src/content/blog/, WebSearch its target keyword and check if your article appears in top 100 results\n"
            f"4. Record results in state/metrics.json under a new 'search_performance' field:\n"
            f"   {{\"date\": \"YYYY-MM-DD\", \"indexed_pages\": N, \"rankings\": {{\"keyword\": position_or_null}}}}\n"
            f"5. Update state/beliefs.json with REAL DATA:\n"
            f"   - If articles are indexed: increase 'long_form_reviews_rank_better' posterior\n"
            f"   - If not indexed after 7+ days: decrease it\n"
            f"   - Compare which article TYPES rank better: reviews vs comparisons vs guides\n"
            f"6. Based on results, update state/strategy.json bandit_allocation:\n"
            f"   - Increase allocation for article types that rank well\n"
            f"   - Decrease allocation for types that don't\n"
            f"7. If any article is NOT indexed after 7 days, add a 'rewrite' task to queue.json\n"
            f"8. Log all findings to state/decisions.log with specific numbers\n"
            f"9. Update state/memory.json: increment session_count, add learnings about what works\n"
            f"10. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "notify_human":
        title = params.get("title", "Action needed")
        body = params.get("body", "Please check the autonomous-earner system.")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: NOTIFY HUMAN.\n"
            f"Create a GitHub Issue to request human action.\n"
            f"Steps:\n"
            f"1. Run: gh issue create --repo magi127605-cmd/autonomous-earner "
            f"--title \"{title}\" --body \"{body}\"\n"
            f"2. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"3. Update state/memory.json: increment session_count\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "rewrite_article":
        slug = params.get("slug", "")
        reason = params.get("reason", "Improve SEO performance")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: REWRITE ARTICLE.\n"
            f"Article: site/src/content/blog/{slug}\n"
            f"Reason: {reason}\n"
            f"Steps:\n"
            f"1. Read the current article\n"
            f"2. Use WebSearch to check the top 3 ranking articles for the same keyword\n"
            f"3. Identify what they do better (structure, depth, freshness, keywords)\n"
            f"4. Rewrite the article to be strictly BETTER than the top results\n"
            f"5. git add, commit with 'Rewrite: {slug}', push\n"
            f"6. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"7. Update state/memory.json: add to learned[] what you changed and why\n"
            f"8. Log the rewrite decision to state/decisions.log\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "strategy_review":
        return (
            f"You are an autonomous AI. Execute task #{task_id}: STRATEGY REVIEW.\n"
            f"Steps:\n"
            f"1. Read ALL state files: memory.json, strategy.json, beliefs.json, metrics.json\n"
            f"2. Read state/decisions.log to review past decisions\n"
            f"3. Analyze: What is working? What is NOT working? What data do you have?\n"
            f"4. Use WebSearch to check current market trends in your niche\n"
            f"5. Update state/strategy.json:\n"
            f"   - Adjust bandit_allocation based on real performance data\n"
            f"   - Update phase if appropriate (building→growing→optimizing)\n"
            f"   - Revise content_plan if current approach isn't working\n"
            f"6. Update state/beliefs.json with Bayesian updates using REAL numbers\n"
            f"7. Add new tasks to queue.json based on updated strategy\n"
            f"8. Log all strategic decisions to decisions.log with data-backed reasoning\n"
            f"9. Update memory.json: increment session_count, add strategic learnings\n"
            f"10. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "cross_post":
        slug = params.get("slug", "")
        platforms = params.get("platforms", "devto,medium,hashnode")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: CROSS-POST ARTICLE.\n"
            f"Article: site/src/content/blog/{slug}\n"
            f"Target platforms: {platforms}\n"
            f"Steps:\n"
            f"1. Check if state/platform_keys.json exists with API keys\n"
            f"2. If keys exist: run `python scripts/cross_post.py site/src/content/blog/{slug} --platform {platforms}`\n"
            f"3. If keys DON'T exist: create a notify_human task in queue.json requesting platform API keys\n"
            f"4. Record results (posted URLs) in state/metrics.json under 'cross_posts' field\n"
            f"5. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"6. Update state/memory.json: increment session_count\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "seo_technical":
        desc = params.get("description", "Technical SEO fix")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: SEO TECHNICAL FIX.\n"
            f"Description: {desc}\n"
            f"Steps:\n"
            f"1. Implement the described technical SEO fix\n"
            f"2. Verify the fix works (build the site if needed)\n"
            f"3. git add, commit, push\n"
            f"4. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"5. Update state/memory.json: increment session_count\n"
            f"Do NOT ask questions. Execute now."
        )

    elif task_type == "seo_optimization":
        desc = params.get("description", "SEO optimization")
        return (
            f"You are an autonomous AI. Execute task #{task_id}: SEO OPTIMIZATION.\n"
            f"Description: {desc}\n"
            f"Steps:\n"
            f"1. Read the relevant articles and identify SEO improvements\n"
            f"2. Apply optimizations (meta tags, keywords, internal links, etc.)\n"
            f"3. git add, commit, push\n"
            f"4. Update state/queue.json: set task #{task_id} status to 'completed'\n"
            f"5. Update state/memory.json: increment session_count\n"
            f"Do NOT ask questions. Execute now."
        )

    else:
        desc = params.get("description", task_type)
        return (
            f"You are an autonomous AI. Execute task #{task_id}: {task_type.upper()}.\n"
            f"Description: {desc}\n"
            f"Execute this task, then update state/queue.json (mark #{task_id} completed), "
            f"state/memory.json (increment session_count), and state/decisions.log.\n"
            f"Do NOT ask questions. Execute now."
        )


def build_fallback_prompt() -> str:
    """キューが空の場合のフォールバックプロンプト"""
    phase = get_strategy_phase()
    session_count = get_session_count()
    now = datetime.now(timezone.utc).isoformat()

    if phase == "init":
        return (
            f"You are an autonomous AI revenue engine. This is your FIRST session.\n"
            f"Current time: {now}\n"
            f"Steps:\n"
            f"1. Read all files in state/ to check current state\n"
            f"2. Use WebSearch to research profitable niches for an SEO affiliate blog\n"
            f"3. Decide on a niche and revenue model\n"
            f"4. Write findings to strategy.json and memory.json\n"
            f"5. Add your first tasks to queue.json (write_article, deploy, etc.)\n"
            f"6. Log decisions to decisions.log\n"
            f"Do NOT ask questions. Execute now."
        )

    # building/growing/optimizing — 自分で次のアクションを決めさせる
    periodic_note = ""
    if session_count > 0 and session_count % 10 == 0:
        periodic_note = (
            "PERIODIC REVIEW: This is session #{session_count}. "
            "Review beliefs.json and strategy.json. Update bandit allocations based on results. "
        )

    return (
        f"You are an autonomous AI revenue engine. Phase: {phase}. Session: {session_count}.\n"
        f"Current time: {now}\n"
        f"{periodic_note}"
        f"Your task queue is EMPTY. Decide what to do next:\n"
        f"1. Read state/strategy.json and state/memory.json\n"
        f"2. Based on current phase and strategy, determine the highest-value action\n"
        f"3. Execute that action (write article, research keywords, improve site, etc.)\n"
        f"4. Add new tasks to state/queue.json for future sessions\n"
        f"5. Update state/memory.json: increment session_count, update last_session\n"
        f"6. Log your decision to state/decisions.log\n"
        f"Do NOT ask questions. Execute now."
    )


def maybe_inject_periodic_tasks():
    """セッション数に応じて定期タスクをキューに自動追加"""
    session_count = get_session_count()
    if session_count <= 0:
        return

    queue_file = STATE_DIR / "queue.json"
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
    except Exception:
        return

    tasks = data.get("tasks", [])
    next_id = data.get("next_id", len(tasks) + 1)
    pending_types = {t["type"] for t in tasks if t.get("status") == "pending"}
    added = False

    # 3セッションごと: check_performance（まだキューになければ）
    if session_count % 3 == 0 and "check_performance" not in pending_types:
        tasks.append({
            "id": next_id,
            "type": "check_performance",
            "priority": "high",
            "params": {"description": f"Periodic performance check at session {session_count}"},
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        })
        next_id += 1
        added = True
        logger.info(f"  定期タスク追加: check_performance (session {session_count})")

    # 10セッションごと: strategy_review
    if session_count % 10 == 0 and "strategy_review" not in pending_types:
        tasks.append({
            "id": next_id,
            "type": "strategy_review",
            "priority": "high",
            "params": {"description": f"Periodic strategy review at session {session_count}"},
            "created": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        })
        next_id += 1
        added = True
        logger.info(f"  定期タスク追加: strategy_review (session {session_count})")

    if added:
        data["tasks"] = tasks
        data["next_id"] = next_id
        queue_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def build_prompt() -> str:
    """実行するプロンプトを構築"""
    # 定期タスクの自動注入
    maybe_inject_periodic_tasks()

    task = get_next_task()
    if task:
        logger.info(f"  タスク: #{task['id']} {task['type']} — {task.get('params', {})}")
        return build_task_prompt(task)
    else:
        logger.info("  キュー空 — フォールバックプロンプト使用")
        return build_fallback_prompt()


def find_claude_cmd() -> str:
    """Claude Code CLIのパスを解決"""
    # npm global install
    npm_path = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
    if npm_path.exists():
        return str(npm_path)
    # PATH上にあれば
    return "claude"


def run_claude_session() -> bool:
    """Claude Codeセッションを1回実行。成功時True。"""
    claude_cmd = find_claude_cmd()
    prompt_text = build_prompt()

    # プロンプトをファイルにも保存（デバッグ用）
    prompt_file = STATE_DIR / "_current_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    cmd = [
        claude_cmd,
        "--print",
        "--dangerously-skip-permissions",
        "--verbose",
        "--model", CLAUDE_MODEL,
        "-p", prompt_text,
    ]

    logger.info("Claude Code セッション開始")
    logger.info(f"  コマンド: {' '.join(cmd[:4])}...")
    logger.info(f"  プロンプト: {prompt_text[:150]}...")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("CLAUDECODE", None)  # ネスト検知を回避

        timed_out = False

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_DIR),
            env=env,
        )

        def _kill():
            nonlocal timed_out
            timed_out = True
            proc.kill()

        timer = threading.Timer(CLAUDE_TIMEOUT, _kill)
        timer.start()

        try:
            stdout, stderr = proc.communicate()
        finally:
            timer.cancel()

        if timed_out:
            logger.warning(f"セッションタイムアウト ({CLAUDE_TIMEOUT}秒)")
            return False

        if proc.returncode != 0:
            logger.error(f"セッションエラー (rc={proc.returncode})")
            if stderr:
                logger.error(f"  stderr: {stderr[:500]}")
            return False

        # 応答のサマリーをログ
        if stdout:
            lines = stdout.strip().split("\n")
            logger.info(f"セッション完了: {len(lines)} 行の出力")
            # 最後の数行をログ
            for line in lines[-5:]:
                logger.info(f"  > {line[:200]}")

            # 成功判定: ツール実行したかチェック（state更新の痕跡）
            output_lower = stdout.lower()
            if any(kw in output_lower for kw in [
                "completed", "written", "deployed", "updated", "committed",
                "published", "created", "pushed", "saved"
            ]):
                logger.info("  → ツール実行あり（成功と判定）")
            else:
                logger.warning("  → ツール実行の痕跡なし（計画だけの可能性）")
        else:
            logger.info("セッション完了（出力なし）")

        return True

    except FileNotFoundError:
        logger.error("claude コマンドが見つかりません")
        return False
    except Exception as e:
        logger.error(f"予期しないエラー: {e}")
        return False


def main():
    """メインループ"""
    logger.info("=" * 60)
    logger.info("Autonomous Earner Daemon 起動")
    logger.info(f"  プロジェクト: {PROJECT_DIR}")
    logger.info(f"  サイクル間隔: {CYCLE_INTERVAL}秒 ({CYCLE_INTERVAL // 60}分)")
    logger.info(f"  1日上限: {MAX_SESSIONS_PER_DAY} セッション")
    logger.info(f"  モデル: {CLAUDE_MODEL}")
    logger.info(f"  タイムアウト: {CLAUDE_TIMEOUT}秒")
    logger.info(f"  フル稼働日: 火・水")
    logger.info(f"  制限日（木金土日月）: {BLOCKED_HOUR_END}:00〜翌{BLOCKED_HOUR_START}:00 のみ稼働")
    logger.info("=" * 60)

    consecutive_errors = 0

    try:
        while True:
            # キルスイッチ確認
            if not is_enabled():
                logger.info("ENABLED=false — 待機中（60秒後に再確認）")
                time.sleep(60)
                continue

            # スケジュール確認
            if is_in_blocked_schedule():
                now_jst = datetime.now(JST)
                wait_sec = seconds_until_unblocked()
                logger.info(
                    f"オーナー作業時間帯 (JST {now_jst.strftime('%H:%M')} "
                    f"{now_jst.strftime('%A')}) — {wait_sec // 60}分後に再チェック"
                )
                time.sleep(min(wait_sec, 1800))  # 最大30分ごとに再チェック
                continue

            # 1日のセッション上限確認
            today_count = get_session_count_today()
            if today_count >= MAX_SESSIONS_PER_DAY:
                logger.info(
                    f"本日のセッション上限到達 ({today_count}/{MAX_SESSIONS_PER_DAY})"
                    " — 明日まで待機"
                )
                time.sleep(3600)
                continue

            # セッション実行
            logger.info(
                f"セッション #{today_count + 1}/{MAX_SESSIONS_PER_DAY} 開始"
            )
            success = run_claude_session()

            if success:
                increment_session_count()
                consecutive_errors = 0
                logger.info(
                    f"次のセッションまで {CYCLE_INTERVAL}秒 "
                    f"({CYCLE_INTERVAL // 60}分) 待機"
                )
            else:
                consecutive_errors += 1
                logger.warning(
                    f"連続エラー: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}"
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        "連続エラー上限到達。30分待機してリトライ。"
                    )
                    time.sleep(1800)
                    consecutive_errors = 0
                    continue

            # 次のサイクルまで待機
            time.sleep(CYCLE_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Ctrl+C で停止")
    finally:
        logger.info("Autonomous Earner Daemon 終了")


if __name__ == "__main__":
    main()
