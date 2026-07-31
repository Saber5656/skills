# launchd Integration

`launchd` は時刻と `run.sh` の起動だけを担当する。

```text
launchd 04:00
  -> run.sh
  -> run-codex-automation.sh
  -> codex exec <thin prompt>
  -> personal-vulnerability-advisor scheduled mode
  -> vault-change-publisher
```

## Runtime Files

| Source in skills repo | Runtime destination |
|---|---|
| `assets/run-codex-automation.sh` | `AutomationWorkspaces/codex/run-codex-automation.sh` |
| `assets/daily-it-news-vulnerability-check.prompt.md` | `AutomationWorkspaces/codex/daily-it-news-vulnerability-check/prompt.md` |
| `references/automation-result.schema.json` | 同workdirの `automation-result.schema.json` |
| `scripts/interpret-automation-result.sh` | 同workdirの `interpret-automation-result.sh` |

tracked sourceがmainへmergeされた後に4ファイルを配備し、SHA-256一致を確認する。task worktreeをproduction runtime pathとして参照しない。

## Runner Context

runnerは `codex exec` へ渡すpromptの先頭に、実際に生成した値を追加する。

```text
Runtime context:
- run_id: <RUN_ID>
- started_at: <STARTED_AT>
- result_schema: <RESULT_SCHEMA>
```

これによりPVA scheduled modeは今回runの生成時刻とpathを検証できる。固定promptにrun IDをハードコードしない。

## Required Sandbox Scope

tracked runner templateはdaily jobのwrite scopeからskills repoとdotfilesを除外する。daily jobだけに次を追加する。

- 2 Vault working treeへのexact `--add-dir`
- 2 Vaultの外部gitdirへのexact `--add-dir`
- publicationのfetch/pushに必要なnetwork access
- structured output schema

他automationへ権限を広げない。

## Deployment Gate

1. skills repoの変更がreview・merge済み。
2. runtime filesをbackup。
3. 4 source filesをcopyしchecksum一致を確認。
4. `zsh -n`、JSON parse、plist lint。
5. TCC権限復旧後、実Vault E2Eを1回だけ実施。
