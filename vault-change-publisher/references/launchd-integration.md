# launchd Integration

`launchd`は04:00にdaily専用runnerを起動するだけとする。汎用automation ID dispatcherへdaily publication権限を追加しない。

```text
launchd 04:00
  -> daily dedicated runner
  -> deterministic standing-task snapshot into collection staging
  -> fixed fetch + lightweight Vault isolation snapshot (no local commit patches)
  -> collection Codex process (Web/search, run staging write only)
  -> deterministic artifact validation
  -> deterministic collision-safe artifact target plan
  -> short cooperative publication lock + bounded per-Vault stable recapture
  -> per-Vault mode hint (sweep / own_only / blocked)
  -> target/snapshot conflict on retry-safe boundary: unlock and bounded full replan/review
  -> materialize local-only commit metadata and patches for publication only
  -> post-collection authorization snapshot into isolated review input
  -> captured dirty Git blobs and local-only commit patches into isolated review input
  -> core artifact + residual sweep review Codex process (read-only, no search/network)
  -> deterministic local commit helper (sweep or isolated-index own_only, no network)
  -> initial fixed runner pushes (manifest-bound object IDs -> refs/heads/main)
  -> deterministic push evidence hunk + sealed HEAD/index/worktree candidates
  -> evidence review Codex process (read-only, no search/network)
  -> deterministic evidence commit + fixed Agents main push
```

## Runtime Files

| Tracked source | Runtime destination |
|---|---|
| `assets/run-daily-it-news-vulnerability-check.sh` | daily automation workdir |
| `assets/daily-it-news.collect.prompt.md` | same workdir |
| `assets/daily-it-news.review.prompt.md` | same workdir |
| `assets/daily-it-news.evidence-review.prompt.md` | same workdir |
| `summarize-it-news/scripts/collect-public-sources.py` | daily automation workdir |
| `summarize-it-news/references/it-news-sources.json` | same workdir |
| `assets/automation.env.example` | copy manually to ignored `automation.local.env` and fill local values |
| `references/collection-result.schema.json` | same workdir |
| `references/publication-review-result.schema.json` | same workdir |
| `references/publication-commit-result.schema.json` | same workdir |
| `references/evidence-review-result.schema.json` | same workdir |
| `references/automation-result.schema.json` | same workdir |
| `scripts/resolve-runtime-context.py` | same workdir |
| `scripts/fetch-vault-main.py` | same workdir |
| `scripts/capture-vault-state.py` | same workdir |
| `scripts/determine-publication-modes.py` | same workdir |
| `scripts/validate-collection-result.py` | same workdir |
| `scripts/install-verified-artifacts.py` | same workdir |
| `scripts/commit-reviewed-publication.py` | same workdir |
| `scripts/validate-publication-review.py` | same workdir |
| `scripts/push-committed-heads.py` | same workdir |
| `scripts/prepare-publication-evidence.py` | same workdir |
| `scripts/commit-push-publication-evidence.py` | same workdir |
| `scripts/evidence_hunk.py` | same workdir |
| `scripts/git_diff_digest.py` | same workdir |
| `scripts/isolated_git_transport.py` | same workdir |
| `scripts/atomic_file_ops.py` | same workdir |
| `scripts/trusted_gitleaks.py` | same workdir |
| `scripts/gitleaks-default.toml` | same workdir |
| `scripts/prepare-codex-output-schema.py` | same workdir |
| `scripts/validate-canonical-result.py` | same workdir |
| `scripts/stage-standing-task.py` | same workdir |
| `scripts/stage-dirty-review-inputs.py` | same workdir |
| `scripts/interpret-automation-result.sh` | same workdir |

Tracked sourceがmainへmergeされた後に配備し、各source/destinationのSHA-256とfile mode一致を確認する。task worktreeをproduction runtime pathとして参照しない。特にdirect executionされる`collect-public-sources.py`はtracked sourceとproduction runtimeの双方がexecutableであることをdeployment gateで検証する。

正本のresult schemaはstate-dependent constraintを保持する。runnerは各run配下へCodex Structured Outputs対応subsetを生成してAPIへ渡し、生成結果は各phaseのdeterministic validatorで正本契約に照合する。互換schemaだけをpublication可否の判定に使わない。

collection Codex processへiCloud上のstanding taskを直接読ませない。resolverが検証した正本taskをrunner側の専用helperでrun stagingへ0600・exclusive createし、collection contextにはsnapshot pathだけを渡す。これによりcollectionのVault非アクセス境界を維持し、launchd配下のNode/CodexにVault全体のTCC権限を要求しない。

authorization taskはnetwork-enabled collection終了後に別のreview input directoryへ0600・exclusive createし、no-network publication phaseへsnapshot pathだけを渡す。collection processからauthorization evidenceを参照可能にしない。

Codexへ渡すpromptはCLI引数へ展開せずstdinから供給し、macOSのargument-size上限に依存しない。publication/evidence reviewへinlineするcontextは、deterministic helperが使用する完全なcontext fileのSHA-256を保持したまま、review判断に不要な全tracked pathの`index_entries`列だけを除いたbounded projectionとする。reviewは`index_sha256`、staged path、dirty/history metadata、sealed snapshotを使い、完全なindex列をLLM contextへ複製しない。

pre/post-collectionの軽量captureはlocal-only commit patchを生成しない。collection成功後、Vaultが変化していてもcollectionを失敗扱いにせず、HEAD、index、dirty content/mode/mtime、Git control-planeの差をVaultごとの`own_only`制約へ変換する。publication時点がbounded retryで安定したVaultだけ、dirty fileをcapture済みGit blob OIDからreview inputへ0600・exclusive createし、local-ahead commitのhash、parents、tree、message、changed paths、first-parent patch digestとpatchを同じisolated review inputへ0600・exclusive createする。片方のsnapshotだけが安定しない場合は、そのVaultの最新identityを`vault_state_snapshot_unstable`として封印し、live bytesを再読込せず`blocked`にする。安定したpeer Vaultのreview・publicationは継続する。sealed residual guardはdirty candidateとreview済みHEADのno-index差分だけを検査し、新規machine-home path、`.obsidian/`、pinned Gitleaks v8不合格を`deferred`へ変換してVault単位のmode floorを`own_only`にする。guard不合格entryのbytesはreviewerへ渡さず、既存fileを変更しない。publication reviewはVault上のdirty fileやlocal-only historyを直接読まず、manifestでidentityとSHA-256に結合されたsnapshotだけを検査する。

cooperative publication lockはstable snapshotとmode hintの固定にだけ使い、長時間のreview中は保持しない。解放前にlock fileのPIDが実行中runner自身と一致することを確認し、別processのlockへ置き換わっていた場合は削除しない。lock解放後の競合はexact snapshot、expected parent、CASでfail closedする。

plan後のtarget競合またはcommit前のretry-safeなsnapshot driftを検出した場合は、artifactも既存差分も上書きせずlockを解放し、最新stateからtarget plan、mode hint、reviewをbounded replanする。target競合が上限まで継続した場合は競合したVaultだけを`artifact_target_replan_exhausted`で`blocked`にし、peer Vaultを続行する。review後のinstaller直前競合が3回継続した場合も、committerが報告したroot-cause Vaultだけを固定して4回目の最終peer-only plan/review/publicationを行う。installerは`O_EXCL` target fdからSHA-256、stable device/inode identity、size、modeをreceiptへ封印し、File Providerで揮発する`mtime/ctime`だけの非同期正規化は許容する。別inode、content、size、mode変更はfail closedとし、失敗cleanupもprivate quarantineへのrename後にidentityを検証して同inodeだけを除去する。別inodeならentry typeに依存しないdescriptor-relative no-replace renameで元のpathへ戻し、競合時は上書きや削除をせずquarantineへ保持して停止する。片側だけ今回commitを作成済みでも未pushであり、peerがmutation前にretry-safe失敗した場合に限り、その今回commitをold OID付きCASで取り消し、共有indexと同receiptに一致するartifact inodeをexact backupへ復元して再計画する。別processのpartial resumeではpathnameからreceiptを再生成しない。receipt不一致時は第三者fileを削除しない。既存commit、既存dirty/staged差分、push済みcommitはrollbackしない。

fetch後のhistory relationは収集の可否には使わない。`equal`または安全な`local_ahead`はpublication可能で、既存commit境界を維持したままreview・secret scan・fixed pushへ含める。unsafe local-ahead、`remote_ahead`、`diverged`、active Git operationは自動pull/rebaseで解消せず、該当Vaultだけ`blocked`とする。安定したdirty residualがguard/reviewを通れば`sweep`、失敗すればその内容を触らず`own_only`へdowngradeする。

`own_only`は共有indexへ`git add`しない。一時indexからartifact-only tree/commitを作成し、commit changed paths、expected parent、old OID付きCASを検証する。commit後はartifact entryだけを共有indexへ同期し、既存staged entry、dirty blob、mode、mtime、porcelainを再照合する。deferred residualが存在しても両Vaultの今回artifactとstanding evidenceがfixed push済みならterminal statusは0とする。

## Local Configuration

personal absolute paths、Vault names、machine layout、publisher account identityはtracked fileへ書かない。`automation.local.env`にはSaihai primary checkout、relative destination、承認taskのSHA-256 pin、GitHubへ紐付くpublisher Git name/emailを置き、runnerは`directory_paths.load_environment(checkout_root=..., environ={}, require_catalog=True)`でcanonical rootsを解決する。resolverがprivate identityを検証してruntime contextへbindし、commit helperはmutation直前に再検証する。承認taskが変わった場合は自動追従せず、内容を人間が再確認してpinを更新する。

File Provider配下のVaultでnetwork Git transportを起動するときは、Vault repoのlocal config、hooks、attributes、fsmonitor、filter、ssh commandを実行経路へ入れない。一時bare Git control planeを作り、Vaultのobject directoryをtransportのprimary object store（`GIT_OBJECT_DIRECTORY`）として固定し、fixed remote URL、object ID、`refs/heads/main`だけでfetch、`ls-remote`、fixed pushを行う。fetchはVaultのobject storeへimmutable objectを追加し得るが、Vaultのworktree、index、local branch refは変更しない。tracking ref更新は取得OIDとold OIDを照合した別のCASで行う。transportとlocal helper subprocessには用途別wall-clock deadlineを設定し、deadline超過時はprocess groupをbounded cleanupする。Gitleaks v8はdigest固定した`[extend] useDefault = true` config bytesを匿名fdへ複製し、`pass_fds`でscanner childへ渡して`/dev/fd/N`を全scanへ明示する。検証後のpath差替えやVault内`.gitleaks.toml`をconfig sourceにしない。non-force制約は変更しない。

push直前のremote OID照合とfixed pushはVaultごとに独立して行う。片方でremote race、network failure、または`blocked`を検出しても、もう片方の安全なpushは抑止しない。片側だけ公開できた場合は`partial_publication`と非0を返し、forceや履歴書換えは行わない。

standing task evidenceは、既存のHEAD/index/worktreeを別々のsealed inputとして扱う。review前にVaultを変更せず、承認後はHEAD candidateだけをcommitし、元のstaged/unstaged差分をそれぞれindex/worktree candidateからexactに復元する。review拒否、digest不一致、CAS失敗ではstanding taskのcontent、mode、mtime、Git statusを変更しない。

## Deployment Gate

1. source PRがreview・merge済み。
2. runtime filesとplistをbackup。
3. tracked files（`determine-publication-modes.py`を含む）をmode-preservingでcopyし、checksumとfile modeが一致。direct executionされる全Python helperと`collect-public-sources.py`は両方でexecutable。
4. ignored local configを人間が確認。
5. `zsh -n`、Python tests、JSON parse、`plutil -lint`。
6. unsafeな既存handoffをUser Vaultへ残し、content/mode/mtime/index statusを記録する。
7. 実Web E2Eを実施し、`complete/true/null`、User=`own_only`、handoff不変、両fixed push、evidence finalization、exit 0を確認する。
8. merge後に最新mainから再配備し、launchd one-shotでも同じ条件を確認する。
