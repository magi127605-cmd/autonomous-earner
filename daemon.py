"""
Autonomous Earner - 24/7 PDCA Daemon
=====================================
2時間ごとにClaude Code CLIを起動し、
AIが自律的に市場調査・コンテンツ生成・デプロイ・分析を行う。

起動: python daemon.py
停止: Ctrl+C or .env の ENABLED=false
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
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
CYCLE_INTERVAL = 7200  # 2時間（秒）
CLAUDE_TIMEOUT = 600   # 10分（1セッションの最大実行時間）
MAX_SESSIONS_PER_DAY = 12
MAX_CONSECUTIVE_ERRORS = 3


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


def build_prompt_file() -> Path:
    """Claude Codeに渡すプロンプトをファイルに書き出す（エンコーディング問題回避）"""
    prompt_file = PROJECT_DIR / "state" / "_current_prompt.md"
    orchestrator_path = PROJECT_DIR / "orchestrator.md"
    now = datetime.now(timezone.utc).isoformat()

    prompt = (
        f"Read the file {orchestrator_path} and follow its instructions. "
        f"Then read all files in {STATE_DIR}/ to load your current state. "
        f"Then execute the next action based on your current phase. "
        f"Update all state files before finishing. "
        f"Current time: {now}. Do not ask questions. Execute immediately."
    )
    prompt_file.write_text(prompt, encoding="utf-8")
    return prompt_file


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
    prompt_file = build_prompt_file()

    prompt_text = prompt_file.read_text(encoding="utf-8")

    cmd = [
        claude_cmd,
        "--print",
        "--dangerously-skip-permissions",
        "--verbose",
        "-p", prompt_text,
    ]

    logger.info("Claude Code セッション開始")
    logger.info(f"  コマンド: {' '.join(cmd[:4])}...")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

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
    logger.info(f"  サイクル間隔: {CYCLE_INTERVAL}秒 ({CYCLE_INTERVAL // 3600}時間)")
    logger.info(f"  1日上限: {MAX_SESSIONS_PER_DAY} セッション")
    logger.info(f"  タイムアウト: {CLAUDE_TIMEOUT}秒")
    logger.info("=" * 60)

    consecutive_errors = 0

    try:
        while True:
            # キルスイッチ確認
            if not is_enabled():
                logger.info("ENABLED=false — 待機中（60秒後に再確認）")
                time.sleep(60)
                continue

            # 1日のセッション上限確認
            today_count = get_session_count_today()
            if today_count >= MAX_SESSIONS_PER_DAY:
                logger.info(
                    f"本日のセッション上限到達 ({today_count}/{MAX_SESSIONS_PER_DAY})"
                    " — 明日まで待機"
                )
                # 次の0時まで待つ（簡易的に1時間ごとに再チェック）
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
