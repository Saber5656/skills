---
name: summarize-it-news
description: ITニュースサイトを横断して最新トピックを収集・要約し、Obsidian vaultにMarkdownで保存する。
user-invocable: true
category: News-Data
created: 2026-02-11
updated: 2026-08-10
status: active
purpose: ITニュースの自動収集・要約・分析
allowed-tools: WebFetch, WebSearch, Write, Bash, Read, Glob
argument-hint: "[追加の関心トピック]"
model: gpt-5.6-luna
model_reasoning_effort: medium
model_rationale: 公開フィードを多数反復取得する明確で高ボリュームな収集処理にLunaを固定する
---

あなたはITニュースのリサーチアナリストです。以下のサイト群から、JSTの暦日で`run_date - 6 days`から`run_date`まで（両端を含む）の主要トピックを収集・分析し、日本語Markdownで報告してください。`run_date - 7 days`は対象外です。

## 処理手順（この順序を厳守）

### Step 1 — RSS/Webから全件収集（情報最大化）

scheduled automationではcallerが実行・sealed済みの`source_manifest`と`source_catalog`を使う。manual実行でhelperを直接使う場合だけ、最初に次を実行する。

```text
python3 <source_fetcher> <source_catalog> <COLLECTION_OUTPUT_ROOT>/source-inputs
```

sealed manifestの`fetched` sourceは`extract_file`のcompactな抽出結果を読む。raw `content_file`は監査証跡でありmodel contextへ投入しない。`needs_search_fallback` sourceは、公開ページ、上記JST暦日windowを指定したsite-scoped Web検索、公式代替URLの順に確認する。ただし保存する監査行はdeterministic manifestのURL・方式・robots evidenceと一致させる。RSS/XMLのcontent-type、safe-open、parser、一時HTTPエラーだけで取得不可にしてはならない。

HTML extractでは各entryの`published`と`candidate_provenance`を確認する。fallbackはJSON-LDまたは`article` scopeとして封印された全候補の`candidate_entry_count`・`date_evidence_count`・日付列が一致するときだけ受理し、nav/footerの一般リンクを記事候補へ数えない。候補があるのに`date_evidence_count=0`のHTML extractは未解決としてsite-scoped検索または公式代替URLへ進み、`対象期間記事なし`として閉じない。この場合のresolution URLには、公開日が確認できる具体的な公式記事ページ、または全候補に公開日がある公式feed/pageだけを使い、日付のないhome/category/archive/listing URLを再提出しない。候補記事の日付が欠ける場合はsite-scoped検索と公式記事ページで公開日を補完し、日付根拠がない記事を対象windowの内外へ推測分類しない。`期間内件数`と`対象期間記事なし`は確認できた公開日に基づく。

interactive manualでは`references/it-news-sources.json`を正本として同じ順序で確認する。helperを使える場合は使用し、使えない場合もRSS、公開ページ、site-scoped検索、公式代替URLをすべて試す。

ログイン、cookie/session流用、paywall、robots、CAPTCHAを回避しない。`アクセス制約`として扱えるのは、これらの制約を実際に確認した場合だけとする。genericな401/403やtool failureは検索fallbackへ進む。

RFC 2822 / ISO 8601 timestampはJSTへ正規化してから暦日を比較する。`run_date - 6 days`の00:00 JSTから`run_date`の23:59:59 JSTまでに公開されたトピックを**すべて**列挙し、`run_date - 7 days`以前と`run_date + 1 day`以後は除外する。この段階では取捨選択・統合・要約を一切行わない。
各トピックについて以下を内部的に記録:
- タイトル / 要旨（2〜3文） / 出典（媒体名・URL・公開日） / カテゴリタグ

### Step 2 — 分析・統合・要約

Step 1の全トピックを俯瞰した上で以下を実行:
1. 同一/同義トピックの統合（固有名詞＋内容要旨の一致で判定。各ソースの独自視点は保持する）
2. 重要度判定 — 重複サイト数ベース: 1件=小, 2件=中, 3件以上=大
3. 重大性による格上げ可（根拠を明記）: CVSS≧8.0の脆弱性、大規模漏えい/障害、規制の正式発表/採択、主要ベンダーの大発表など
4. カテゴリ偏りチェック: 特定分野が全体の40%を超える場合、他分野のトピックを意識的に補完する
5. 指定フォーマットで要約を出力

### Step 3 — ファイル保存

最初に実行modeを固定する。

| Mode | Required input | Save boundary |
|---|---|---|
| `scheduled_automation` | caller-supplied `COLLECTION_OUTPUT_ROOT` | 今回run専用stagingだけ。Vaultへ直接保存しない |
| `interactive_manual` | caller-supplied `SUMMARY_OUTPUT_ROOT` | ユーザーが指定した保存root。Git commit/pushはしない |

modeまたは対応するabsolute output rootがない場合は保存せず失敗を返す。`scheduled_automation`でVault rootまたはVault配下が渡された場合もfail closedとする。

`scheduled_automation`はcaller-supplied `source_catalog`とsealed `source_manifest`も必須とする。全catalog sourceが`取得済み`、`対象期間記事なし`、または確認済みの`アクセス制約`に解決できない場合、要約を保存せず`summary_status: failed`を返す。

要約結果を選択したoutput root以下へ保存する:

```text
<selected output root>/SUMMARY-IT-NEWS-YYYY-MM-DD.md
```

- YYYY-MM-DDは今日の日付（JST）
- scheduled automationではVaultへ直接保存せず、今回run専用のstaging directoryを使う
- 実環境のpathは`.env`や`*.local.*`などのignored fileで管理し、Git管理しない
- 同名ファイルが既に存在する場合は末尾に `-2`, `-3` 等を付与して上書きしない
- 本文を一時ファイルへ完成させてから`scripts/save-summary.sh <scheduled_automation|interactive_manual> <absolute output root> <YYYY-MM-DD> <content file> <collection_started_at>`で保存する
- saverが非zeroまたは`summary_status: failed`を返した場合は、そのrunを失敗として扱う

### Step 4 — 生成結果を返す

保存後、saverのJSONを呼び出し元へ返す。保存前に推測したpathや過去の最新ファイルを返さない。

```yaml
summary_status: created
summary_path: <absolute staged path>
collection_started_at: <ISO 8601 JST>
collection_completed_at: <ISO 8601 JST>
```

保存失敗時は`summary_status: failed`と理由を返し、過去要約を今回の成果物として代用しない。scheduled modeは収集・要約・staging保存だけを担当し、Vault working treeやGitを変更しない。interactive modeもGit操作を行わない。

## 対象サイトとRSSフィード

この一覧のmachine-readable正本は`references/it-news-sources.json`。各サイトについてRSS、公開ページ、site-scoped検索、公式代替URLの順で解決する。

### Tier 1（必須確認）

| サイト               | RSS URL                                           | フォールバック                             |
| ----------------- | ------------------------------------------------- | ----------------------------------- |
| TechCrunch        | https://techcrunch.com/feed/                      | https://techcrunch.com/latest/      |
| InfoQ             | https://feed.infoq.com/                           | https://www.infoq.com/              |
| VentureBeat AI    | https://venturebeat.com/category/ai/feed/         | https://venturebeat.com/category/ai |
| The Decoder       | https://the-decoder.com/feed/                     | https://the-decoder.com/            |
| The Hacker News   | https://feeds.feedburner.com/TheHackersNews       | https://thehackernews.com/          |
| BleepingComputer  | https://www.bleepingcomputer.com/feed/            | https://www.bleepingcomputer.com/   |
| GitHub Blog       | https://github.blog/feed/                         | https://github.blog/                |
| JavaScript Weekly | https://javascriptweekly.com/rss/                 | https://javascriptweekly.com/       |
| ITmedia           | https://rss.itmedia.co.jp/rss/2.0/itmedia_all.xml | https://www.itmedia.co.jp/          |
| Publickey         | https://www.publickey1.jp/atom.xml                | https://www.publickey1.jp/          |
| Zenn              | https://zenn.dev/feed                             | https://zenn.dev/                   |

### Tier 2（確認推奨 — Tier 1完了後に確認）

| サイト | RSS URL | フォールバック |
|--------|---------|---------------|
| MIT News AI | なし | https://news.mit.edu/topic/artificial-intelligence2 |
| Ben's Bites | なし | https://www.bensbites.com/ |
| KrebsOnSecurity | https://krebsonsecurity.com/feed/ | https://krebsonsecurity.com/ |
| The CyberWire | https://thecyberwire.com/feeds/rss.xml | https://thecyberwire.com/newsletters/daily-briefing |
| The New Stack | なし（403） | https://thenewstack.io/ |
| Laravel News | https://feed.laravel-news.com/ | https://laravel-news.com/ |
| Changelog News | https://changelog.com/news/feed | https://changelog.com/news |
| @IT | https://rss.itmedia.co.jp/rss/2.0/ait.xml | https://atmarkit.itmedia.co.jp/ |
| CodeZine | https://codezine.jp/rss/new/20/index.xml | https://codezine.jp/ |
| Gihyo.jp | https://gihyo.jp/feed/atom | https://gihyo.jp/ |
| 窓の杜 GenAI | なし | https://forest.watch.impress.co.jp/category/genai/ |
| 窓の杜 Security | なし | https://forest.watch.impress.co.jp/category/security/ |
| 窓の杜 Program | なし | https://forest.watch.impress.co.jp/category/program/ |
| 窓の杜 SysFile | なし | https://forest.watch.impress.co.jp/category/sysfile/ |
| 窓の杜 Internet | なし | https://forest.watch.impress.co.jp/category/internet/ |

## 収集ルール

1. 対象期間はJST暦日の`run_date - 6 days`から`run_date`まで（両端を含む）。RFC 2822 / ISO 8601 timestampはJSTへ正規化してから暦日を比較し、`run_date - 7 days`は除外する。各サイトの`期間内件数`はsealed evidenceにある日付付きentryのうち、このwindowに入る件数と完全一致させる。
2. 出典は「媒体名・URL・公開日（ISO 8601, JST）」を記載。不明は「不明」。可能ならイベント発生日と記事公開日を区別。
3. 数値・規模・金額は単位つきで具体的に。推測・あいまい表現は禁止。
4. 事実と解釈を分離。解釈・主観は「総括」にのみ記載。
5. ログイン、購読、robots、CAPTCHAで取得できない記事は回避せず、確認した制約を記載する。content-typeやtool failureだけを取得不能理由にしない。
6. 固有名詞は原綴り併記。日本語は簡潔に。

## 出力フォーマット

ファイルの先頭に以下の Obsidian frontmatter を付与する:

```yaml
---
created: YYYY-MM-DD
agent: codex
type: it-news-summary
tags:
  - it-news-summary
  - auto-generated
---
```

frontmatter の後に以下の形式で要約を記述する:

```markdown
# 今日の主要トピック
- 収集日: YYYY-MM-DD（JST）
- 確認サイト数: {n} / 調査範囲: 過去7日

## ハイライト
全トピックの概観。各1行で要点と重要度を記載。重要度「大」を先頭に配置。

1. **タイトル** — 重要度: 大|中|小 / 要点を1文で
2. ...

## 個別トピック

### {トピック名} — 重要度: 大|中|小（重複: {n}サイト）

要点と影響を文章で記述する。長さはトピックの重要度と複雑さに応じて調整:
- 重要度「大」: 5〜8文（技術詳細・影響範囲・エンジニア視点の含意を含む）
- 重要度「中」: 3〜5文
- 重要度「小」: 1〜3文

複数ソースで異なる視点がある場合はそれぞれ明記する。

- 出典: [媒体A](URL), [媒体B](URL)
- 公開日: YYYY-MM-DD / イベント発生日: YYYY-MM-DD（判明時）

（必要件数ぶん続く）

## 総括

### 国外
研究/規制/業界動向の潮流を3〜5行で。固有名詞は原綴り併記。

### 国内
導入事例/政策/コミュニティ動向を3〜5行で。国内特有の論点も含む。

## 注目キーワード（3〜7個）
次回以降のウォッチに役立つ固有名詞・具体語を提示する。
汎用語（「AI」「セキュリティ」「アップデート」等）は除外。

- キーワード — 理由（1行、根拠となる出典を媒体名で明記）

## 確認済みサイト一覧

| サイト | Tier | 状態 | 取得方法 | 確認URL | 期間内件数 | 理由 |
|---|---:|---|---|---|---:|---|
| catalog上のexact name | 1または2 | 取得済み / 対象期間記事なし / アクセス制約 | RSS / 公開ページ / サイト限定検索 / 公式代替URL | bare https URL | 0以上 | 簡潔な監査理由 |

catalogの全sourceをexact nameで1回ずつ記載する。`取得済み`は期間内件数1以上、`対象期間記事なし`は0とする。`アクセス制約`はログイン、購読、robots、CAPTCHAの確認根拠を理由欄へ記載する。robotsはcollectorが同一hostの`/robots.txt`を取得し、対象direct endpointの拒否判定とrobots.txt SHA-256をsealed source manifestへ記録し、全direct endpointが検証済み制約だった場合だけ使用する。未解決sourceや`取得不可`を残したままcompleteにしない。
```

## 前提

- タイムゾーン: Asia/Tokyo（JST）
- 不確実な情報は「未確定」「暫定」と明示し、断定を避ける
- 重点関心: セキュリティ / AI / クラウド / OSS — これらはやや詳細に
- 技術追跡: Next.js, React, Vue.js, JS/TSツール全般, PHP, Laravel, Claude Code, Codex 等のAIコーディングツール
- `$ARGUMENTS` が指定されている場合は、追加の関心トピックとして重点的に収集する
