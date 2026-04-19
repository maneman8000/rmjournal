# Queue を使った SVG レンダリング CPU 制限回避計画

## 背景・現状の問題

### CPU 超過エラー

Cloudflare Workers Free プランの CPU 制限は **10ms/リクエスト** だが、SVG レンダリング処理（`rmscene` ライブラリによる `.rm` ファイルのパースと SVG 変換）が CPU 時間を大幅に超過している。

ログ例：
```
CpuLimitExceeded: Python Worker exceeded CPU time limit
  at rm_content_to_svg()
    → read_tree() → build_tree() → read_blocks() → CrdtId.__init__()
```

実測値：
- 失敗した実行: `cpuTimeMs: 450ms`（10ms 制限の 45 倍）
- 成功した実行（処理なし）: `cpuTimeMs: 140ms`

### なぜ Queue か

Cloudflare Queues の Consumer Worker は CPU 時間制限が**大幅に緩い**：

| 実行タイプ | CPU 制限 |
|---|---|
| Workers Free（通常） | 10ms |
| Workers Paid（通常） | 30ms |
| Queue Consumer（デフォルト） | **30秒** |
| Queue Consumer（最大設定） | **5分**（Paid プランのみ） |

Queue Consumer は **Free プランでもデフォルト 30 秒**の CPU 時間が使える。

> **注意**: `wrangler.jsonc` の `limits.cpu_ms` は **Free プランでは使用不可**。  
> エラー: `CPU limits are not supported for the Free plan. [code: 100328]`  
> 拡張設定（最大5分）は Paid プランのみ対応。

Queues 自体は Free プランで **1日 1,000,000 メッセージ**まで無料。

---

## 検証結果（2026年4月）

### ✅ Python Workers で `queue()` ハンドラが動作することを確認済み

- Producer（送信）: `RENDER_QUEUE.send(msg)` → 成功
- Consumer（受信）: `queue(self, batch, env=None, ctx=None)` → 成功
- R2 への書き込みも Consumer 内で正常動作

### 確認した注意事項

#### `queue()` のシグネチャ

`scheduled()` と同様に Workers ランタイムが4引数を渡すため、`env` と `ctx` のデフォルト引数が必要：

```python
# 正しいシグネチャ
async def queue(self, batch, env=None, ctx=None):
    ...
```

#### `message.body` は `JsProxy`（JS オブジェクト）

`dict.get()` は使えない。`getattr()` で属性アクセスする：

```python
# 誤（dict として扱おうとする）
image_key = body["image_key"]  # → TypeError

# 正（JsProxy として属性アクセス）
image_key = str(getattr(body, "image_key"))
```

#### Queue 送信時のメッセージは `to_js()` 変換が必要

```python
from pyodide.ffi import to_js
from js import Object

msg = to_js(
    {"tmp_key": tmp_key, "target_date": str(target_date)},
    dict_converter=Object.fromEntries,
)
await self.env.RENDER_QUEUE.send(msg)
```

---

## 設計方針

### 基本方針

**1 trigger（または Cron 実行）= 1 Queue メッセージ**

速度よりシンプルさを優先。ページごとに分割する必要はない。
Queue Consumer が当日のすべてのページを一括でレンダリングする。

### 処理の分割

| 処理 | 実行場所 | 理由 |
|---|---|---|
| reMarkable API からドキュメント取得 | Cron Worker（現状維持） | subrequest が必要、CPU は軽い |
| ページの日付チェック | Cron Worker（現状維持） | CPU 軽微 |
| `.rm` ファイル取得 | Cron Worker（現状維持） | subrequest が必要 |
| `.rm` ファイルを R2 tmp/ に一時保存 | Cron Worker（新規追加） | Queue の 128KB 制限を回避 |
| **1メッセージを Queue に送信** | Cron Worker（新規追加） | `{ "target_date", "tmp_keys": [...], "image_keys": [...] }` |
| **SVG レンダリング（全ページ）** | **Queue Consumer（新規）** | CPU ボトルネック（30秒以内） |
| R2 への SVG 保存（全ページ） | Queue Consumer（新規） | レンダリング直後に実行 |
| R2 tmp/ の一時ファイル削除 | Queue Consumer（新規） | レンダリング完了後にクリーンアップ |
| `generate_daily_page()` | Queue Consumer（新規） | 全ページ完了後に呼ぶ（対応案 A） |
| `generate_index_page()` | Cron Worker（現状維持） | 現状維持 |

---

## アーキテクチャ変更

### Before（現在）

```
Cron Trigger / /trigger
  └─ _run_sync()
      ├─ list_docs() → reMarkable API
      ├─ get_doc() → KV / reMarkable API
      ├─ get_blob(.content) → reMarkable API
      ├─ for each target page:
      │   ├─ get_blob(.rm) → reMarkable API
      │   ├─ rm_content_to_svg()  ← CPU ボトルネック
      │   └─ R2.put(SVG)
      ├─ generate_daily_page() → R2
      └─ generate_index_page() → R2
```

### After（Queue 導入後）

```
Cron Trigger / /trigger
  └─ _run_sync()
      ├─ list_docs() → reMarkable API
      ├─ get_doc() → KV / reMarkable API
      ├─ get_blob(.content) → reMarkable API
      ├─ for each target page:
      │   ├─ get_blob(.rm) → reMarkable API
      │   └─ R2.put("tmp/render/<id>.rm")  ← 一時保存
      ├─ Queue.send({                       ← 1メッセージ
      │       "target_date": "2026-04-19",
      │       "tmp_keys": ["tmp/render/a.rm", "tmp/render/b.rm", ...],
      │       "image_keys": ["2026/04/19/images/a.svg", ...],
      │   })
      └─ generate_index_page() → R2

Queue Consumer（CPU 30秒）
  └─ queue(self, batch, env=None, ctx=None)
      ├─ for each (tmp_key, image_key):
      │   ├─ R2.get(tmp_key) → rm_content
      │   ├─ rm_content_to_svg()  ← CPU 30秒まで使える
      │   ├─ R2.put(image_key, SVG)
      │   └─ R2.delete(tmp_key)
      └─ generate_daily_page(target_date) → R2
```

---

## メッセージサイズ対策（R2 一時保存方式）

Queue のメッセージサイズ上限は **128KB**。`.rm` ファイルを直接 Queue に入れると超過する可能性があるため、R2 に一時保存してキーのみを Queue に渡す。

メッセージの中身はキー文字列のリストのみなので、サイズは問題ない：

```python
# Cron Worker 側（sync.py）
tmp_keys = []
image_keys = []

for page_id, rm_content in target_page_contents:
    tmp_key = f"tmp/render/{doc.id}_{page_id}.rm"
    image_key = f"{date_prefix}/images/{doc.id}_{page_id}.svg"
    await ctx.storage.put(tmp_key, rm_content)
    tmp_keys.append(tmp_key)
    image_keys.append(image_key)

# 1メッセージを送信
from pyodide.ffi import to_js
from js import Object, Array

msg = to_js(
    {
        "target_date": str(ctx.target_date),
        "tmp_keys": tmp_keys,
        "image_keys": image_keys,
    },
    dict_converter=Object.fromEntries,
)
await ctx.render_queue.send(msg)
```

---

## Queue Consumer の実装

```python
async def queue(self, batch, env=None, ctx=None):
    """Queue Consumer: SVG レンダリングを実行する"""
    from renderer.svg import rm_content_to_svg
    from renderer.canvas import PAPER_PRO
    from exporter import export_svg_to_storage
    from journal.web import generate_daily_page
    from datetime import date

    storage = R2StorageProvider(self.env.R2_BUCKET)

    for message in batch.messages:
        try:
            body = message.body
            # body は JsProxy なので getattr でアクセス
            target_date_str = str(getattr(body, "target_date"))
            tmp_keys = list(getattr(body, "tmp_keys"))    # JS Array → Python list
            image_keys = list(getattr(body, "image_keys"))

            # 全ページをレンダリング
            for tmp_key, image_key in zip(tmp_keys, image_keys):
                rm_content = await storage.get(str(tmp_key))
                if rm_content is None:
                    raise ValueError(f"tmp file not found: {tmp_key}")

                svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
                await export_svg_to_storage(svg_data, storage, str(image_key))
                await storage.delete(str(tmp_key))

            # 全ページ完了後に daily page を生成
            target_date = date.fromisoformat(target_date_str)
            await generate_daily_page(target_date, storage)

            message.ack()
        except Exception as e:
            _logger.error(f"[queue-render] Failed: {e}")
            message.retry()
```

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `wrangler.jsonc` | Queue バインディング・Consumer 設定（完了済み） |
| `src/worker.py` | テスト用 `queue()` を本番実装に置き換え |
| `src/journal/sync.py` | `process_document_pages()` でレンダリングを R2一時保存に変更、`process_journal()` で1メッセージ送信・`generate_daily_page()` 呼び出し削除 |
| `src/journal/cli.py` | `JournalContext` に `render_queue` フィールド追加 |

---

## 実装手順

### Step 1: Queue 作成（完了済み）

```bash
uv run pywrangler queues create rmjournal-render-queue
```

### Step 2: `wrangler.jsonc` の設定（完了済み）

```jsonc
"queues": {
    "producers": [
        { "binding": "RENDER_QUEUE", "queue": "rmjournal-render-queue" }
    ],
    "consumers": [
        { "queue": "rmjournal-render-queue", "max_batch_size": 1, "max_batch_timeout": 0 }
    ]
}
// limits.cpu_ms は Free プラン非対応のため設定しない
// Queue Consumer のデフォルト CPU 制限（30秒）を使用
```

### Step 3: `cli.py` に `render_queue` フィールドを追加

```python
@dataclass
class JournalContext:
    target_date: date
    storage: StorageProvider
    client: RemarkableClient
    render_queue: Optional[object] = None  # 追加
```

### Step 4: `sync.py` の変更

`process_document_pages()` の SVG レンダリング部分を R2 一時保存に変更し、`process_journal()` で1メッセージを送信する。`generate_daily_page()` の呼び出しを削除（Queue Consumer 側に移動）。

### Step 5: `worker.py` のテスト用 `queue()` を本番実装に置き換え

上記「Queue Consumer の実装」コードに差し替える。

---

## 懸念事項・制約

### JS Array のアンラップ

`message.body` の `tmp_keys` / `image_keys` は JS Array（`JsProxy`）として渡されるため、`list()` でアンラップが必要。実装時に動作確認が必要。

### Free プランの Queue 制限

- 1日 1,000,000 メッセージ（rmjournal の用途では問題なし）
- Queue Consumer のデフォルト CPU: 30秒（Free プランで設定変更不可）

---

## 実装前確認事項

- [x] Python Workers で `queue()` ハンドラが使えるか確認 → **動作確認済み**
- [x] `queue()` シグネチャ: `(self, batch, env=None, ctx=None)` → **確認済み**
- [x] `message.body` が JsProxy であり `getattr()` でアクセスすること → **確認済み**
- [x] Queue 送信時に `to_js()` + `Object.fromEntries` 変換が必要 → **確認済み**
- [x] `limits.cpu_ms` は Free プラン非対応 → **確認済み（コメントアウト）**
- [x] 1 trigger = 1 メッセージ設計を採用（シンプルさ優先）
- [x] `generate_daily_page()` の実行タイミング → **対応案 A を採用（Queue Consumer 側）**
- [ ] JS Array（`tmp_keys` / `image_keys`）の `list()` アンラップ動作確認
- [ ] `.rm` ファイルの最大サイズ確認（R2 一時保存で回避予定）
