You are an autonomous AI. Execute task #9: DEPLOY.
Steps:
1. cd into the site/ directory and run: npm run build
2. cd back to the project root
3. git add -A && git commit -m 'Auto-deploy: update site' && git push
4. Update state/queue.json: set task #9 status to 'completed'
5. Update state/metrics.json: increment total_deploys
6. Update state/memory.json: increment session_count, update last_session
Do NOT ask questions. Execute now.