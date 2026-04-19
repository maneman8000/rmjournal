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
    {"tmp_key": tmp_key, "image_key": image_key},
    dict_converter=Object.fromEntries,
)
await self.env.RENDER_QUEUE.send(msg)
```

---

## 設計方針

### 基本方針

SVG レンダリング処理（CPU ボトルネック）のみを Queue 経由に移行する。他の処理（reMarkable API 取得、R2 保存判断、HTML 生成）は現在の Worker のままとする。

### 分割する処理

| 処理 | 実行場所 | 理由 |
|---|---|---|
| reMarkable API からドキュメント取得 | Cron Worker（現状維持） | subrequest が必要、CPU は軽い |
| ページの日付チェック | Cron Worker（現状維持） | CPU 軽微 |
| `.rm` ファイル取得 | Cron Worker（現状維持） | subrequest が必要 |
| `.rm` ファイルを R2 tmp/ に一時保存 | Cron Worker（新規追加） | Queue の 128KB 制限を回避 |
| **SVG レンダリング（`rm_content_to_svg()`）** | **Queue Consumer（新規）** | CPU ボトルネック |
| R2 への SVG 保存 | Queue Consumer（新規） | レンダリング直後に実行 |
| `generate_daily_page()` | Queue Consumer（新規） | 対応案 A 採用（後述） |
| R2 tmp/ の一時ファイル削除 | Queue Consumer（新規） | レンダリング完了後にクリーンアップ |
| `generate_index_page()` | Cron Worker（現状維持） | 現状維持 |

---

## アーキテクチャ変更

### Before（現在）

```
Cron Trigger
  └─ _run_sync()
      ├─ list_docs() → reMarkable API
      ├─ get_doc() → KV / reMarkable API
      ├─ get_blob(.content) → reMarkable API
      ├─ get_blob(.rm) → reMarkable API
      ├─ rm_content_to_svg()  ← CPU ボトルネック
      ├─ R2.put(SVG)
      ├─ generate_daily_page() → R2
      └─ generate_index_page() → R2
```

### After（Queue 導入後）

```
Cron Trigger
  └─ _run_sync()
      ├─ list_docs() → reMarkable API
      ├─ get_doc() → KV / reMarkable API
      ├─ get_blob(.content) → reMarkable API
      ├─ get_blob(.rm) → reMarkable API
      ├─ R2.put(tmp/render/<id>.rm)  ← 一時保存
      ├─ Queue.send({ tmp_key, image_key, page_index, total_pages, target_date })
      └─ generate_index_page() → R2

Queue Consumer
  └─ queue(self, batch, env=None, ctx=None)
      ├─ R2.get(tmp_key) → rm_content
      ├─ rm_content_to_svg()  ← CPU 30秒まで使える（Free プラン）
      ├─ R2.put(image_key, SVG)
      ├─ R2.delete(tmp_key)   ← 一時ファイル削除
      └─ （page_index == total_pages - 1 のとき）
          └─ generate_daily_page() → R2  ← 対応案 A
```

---

## メッセージサイズ対策（R2 一時保存方式）

Queue のメッセージサイズ上限は **128KB**。`.rm` ファイルを Base64 エンコードすると超過する可能性があるため、`.rm` ファイルは R2 に一時保存してキーを Queue に渡す。

```python
# Cron Worker 側（sync.py）
tmp_key = f"tmp/render/{doc_id}_{page_id}.rm"
await ctx.storage.put(tmp_key, rm_content)

from pyodide.ffi import to_js
from js import Object
msg = to_js(
    {
        "tmp_key": tmp_key,
        "image_key": image_key,
        "page_index": page_index,      # 0-indexed
        "total_pages": total_pages,    # 当日処理するページの総数
        "target_date": str(ctx.target_date),
    },
    dict_converter=Object.fromEntries,
)
await ctx.render_queue.send(msg)
```

---

## `generate_daily_page()` の実行タイミング（対応案 A を採用）

Queue は非同期のため、Cron Worker が Queue にメッセージを送った直後に `generate_daily_page()` を呼んでも SVG がまだ R2 に存在しない。

**対応案 A（採用）**: `generate_daily_page()` を Queue Consumer 側で呼ぶ。

メッセージに `page_index`（0-indexed）と `total_pages` を含め、最後のメッセージ（`page_index == total_pages - 1`）処理時に `generate_daily_page()` を呼ぶ：

```python
# Queue Consumer 側（worker.py）
page_index = int(getattr(body, "page_index", 0))
total_pages = int(getattr(body, "total_pages", 1))
target_date_str = str(getattr(body, "target_date", ""))

# SVG レンダリングと保存
svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
await storage.put(image_key, ...)
await storage.delete(tmp_key)

# 最後のページのレンダリング完了後に daily page を生成
if page_index == total_pages - 1:
    from datetime import date
    target_date = date.fromisoformat(target_date_str)
    await generate_daily_page(target_date, storage)
```

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
        {
            "binding": "RENDER_QUEUE",
            "queue": "rmjournal-render-queue"
        }
    ],
    "consumers": [
        {
            "queue": "rmjournal-render-queue",
            "max_batch_size": 1,
            "max_batch_timeout": 0
        }
    ]
}
// limits.cpu_ms は Free プラン非対応のため設定しない
// Queue Consumer のデフォルト CPU 制限（30秒）を使用
```

### Step 3: `sync.py` の変更

`process_document_pages()` で SVG レンダリングの代わりに R2 一時保存 + Queue 送信：

```python
# 変更前
svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
await export_svg_to_storage(svg_data, ctx.storage, image_key)
processed_pages.append(page_id)

# 変更後
tmp_key = f"tmp/render/{doc.id}_{page_id}.rm"
await ctx.storage.put(tmp_key, rm_content)

from pyodide.ffi import to_js
from js import Object
msg = to_js(
    {
        "tmp_key": tmp_key,
        "image_key": image_key,
        "page_index": len(processed_pages),
        "total_pages": total_pages,  # ループ前に計算
        "target_date": str(ctx.target_date),
    },
    dict_converter=Object.fromEntries,
)
await ctx.render_queue.send(msg)
processed_pages.append(page_id)
```

`JournalContext` に `render_queue` フィールドを追加。`process_journal()` から `generate_daily_page()` の呼び出しを削除（Queue Consumer 側に移動）。

### Step 4: `worker.py` に Queue Consumer ハンドラを実装

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
            # body は JsProxy（JS オブジェクト）なので getattr でアクセス
            tmp_key = str(getattr(body, "tmp_key"))
            image_key = str(getattr(body, "image_key"))
            page_index = int(getattr(body, "page_index", 0))
            total_pages = int(getattr(body, "total_pages", 1))
            target_date_str = str(getattr(body, "target_date", ""))

            # R2 から一時ファイルを取得
            rm_content = await storage.get(tmp_key)
            if rm_content is None:
                raise ValueError(f"tmp file not found: {tmp_key}")

            # SVG レンダリング（CPU 30秒まで使える）
            svg_data = rm_content_to_svg(rm_content, dim=PAPER_PRO)
            await export_svg_to_storage(svg_data, storage, image_key)

            # 一時ファイルを削除
            await storage.delete(tmp_key)

            # 最後のページのレンダリング完了後に daily page を生成（対応案 A）
            if page_index == total_pages - 1 and target_date_str:
                target_date = date.fromisoformat(target_date_str)
                await generate_daily_page(target_date, storage)

            message.ack()
        except Exception as e:
            _logger.error(f"[queue-render] Failed: {e}")
            message.retry()
```

### Step 5: `_run_sync()` から `generate_daily_page()` の呼び出しを削除

`process_journal()` と `sync.py` から `generate_daily_page()` の呼び出しを削除し、Queue Consumer に移動する。

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `wrangler.jsonc` | Queue バインディング・Consumer 設定（完了済み） |
| `src/worker.py` | テスト用 `queue()` を本番実装に置き換え、`_run_sync()` の `generate_daily_page()` 呼び出し削除 |
| `src/journal/sync.py` | `process_document_pages()` でレンダリングを Queue 送信+R2一時保存に変更、`process_journal()` の `generate_daily_page()` 呼び出し削除 |
| `src/journal/cli.py` | `JournalContext` に `render_queue` フィールド追加 |

---

## 懸念事項・制約

### `total_pages` の事前計算

`process_document_pages()` のループ前に当日処理するページの総数が必要。`.content` ファイルのパース後にページ数を数えてからループするよう修正が必要。

### Queue の配信順序

`max_batch_size: 1` にしているため1メッセージずつ処理される。ただし Queue は FIFO だが配信が保証された順序でないため、`page_index == total_pages - 1` の判定が厳密でない場合がある（同日に複数ドキュメントがある場合）。

**対策**: `target_date` ごとにメッセージを追跡するか、`generate_daily_page()` のべき等性に頼る（複数回呼ばれても問題ない実装）。

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
- [x] `generate_daily_page()` の実行タイミング → **対応案 A を採用**
- [ ] `.rm` ファイルの最大サイズ確認（128KB 制限との兼ね合い）← R2 一時保存で回避予定
- [ ] `total_pages` 事前計算ロジックの実装
- [ ] Queue 配信順序の問題に対する `generate_daily_page()` のべき等性確認
