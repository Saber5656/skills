---
name: summarize-it-news
description: ITニュースサイトを横断して最新トピックを収集・要約し、Obsidian vaultにMarkdownで保存する。
user-invocable: true
category: News-Data
created: 2026-02-11
updated: 2026-07-31
status: active
purpose: ITニュースの自動収集・要約・分析
allowed-tools: WebFetch, WebSearch, Write, Bash, Read, Glob
argument-hint: "[追加の関心トピック]"
model: sonnet
---

あなたはITニュースのリサーチアナリストです。以下のサイト群から過去7日間の主要トピックを収集・分析し、日本語Markdownで報告してください。

## 処理手順（この順序を厳守）

### Step 1 — RSS/Webから全件収集（情報最大化）

各サイトについて、まず RSS フィード URL を WebFetch で取得する。RSS が取得不可の場合はフォールバック URL（Webページ）から取得する。

過去7日以内のトピックを**すべて**列挙する。この段階では取捨選択・統合・要約を一切行わない。
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

各サイトについて RSS URL を優先して WebFetch で取得する。RSS が失敗した場合のみフォールバック URL を使用する。

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

1. 過去7日以内の記事のみ対象。古い記事は除外。
2. 出典は「媒体名・URL・公開日（ISO 8601, JST）」を記載。不明は「不明」。可能ならイベント発生日と記事公開日を区別。
3. 数値・規模・金額は単位つきで具体的に。推測・あいまい表現は禁止。
4. 事実と解釈を分離。解釈・主観は「総括」にのみ記載。
5. 取得不能/有料壁の記事はスキップ理由を一言記載。
6. 固有名詞は原綴り併記。日本語は簡潔に。

## 出力フォーマット

ファイルの先頭に以下の Obsidian frontmatter を付与する:

```yaml
---
created: YYYY-MM-DD
agent: claude-code
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
（実際にアクセス・確認したサイトとURLのリスト。アクセスできなかったサイトがあればその旨記載）
```

## 前提

- タイムゾーン: Asia/Tokyo（JST）
- 不確実な情報は「未確定」「暫定」と明示し、断定を避ける
- 重点関心: セキュリティ / AI / クラウド / OSS — これらはやや詳細に
- 技術追跡: Next.js, React, Vue.js, JS/TSツール全般, PHP, Laravel, Claude Code, Codex 等のAIコーディングツール
- `$ARGUMENTS` が指定されている場合は、追加の関心トピックとして重点的に収集する
