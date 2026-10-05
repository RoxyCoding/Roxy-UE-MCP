# UE Agent Toolkit (UE 5.8)

AI Agent（Claude / Codex など）が Unreal Editor 内の開発作業を **作成 → 確認 → Compile → Error 取得 → 修正 → 検証** のループで実行するための Toolset 群です。

- **通信は Epic 公式の Unreal MCP (`ModelContextProtocol` プラグイン) をそのまま使用**します。独自サーバーはありません。
- Toolset は Epic の `ToolsetRegistry` に Python で登録され、公式 MCP の `list_toolsets` / `describe_toolset` / `call_tool` から発見・実行できます。
- Epic 公式 Toolset（EditorToolset, UMGToolSet, NiagaraToolsets, Sequencer, AutomationTest, ConfigSettings, Logs など）と**重複しない部分**を実装しています。
- 一覧: [Docs/TOOLS.md](Docs/TOOLS.md)（18 Toolsets / 195 Tools）

## 構成

```
Plugins/
  UEAgentToolkit/            ← Python のみ（コンパイル不要）
    Content/Python/
      init_unreal.py         ← Toolset / Agent Skill 登録
      agent_toolkit/
        core/                ← 結果エンベロープ, エラー, decorator, 解決, ログ, バックアップ, ini 編集
        toolsets/            ← 1 ファイル = 1 Toolset
        skills/              ← Agent Skill（作業手順・レシピ）
        tests/               ← unittest（Registry 経由で JSON 入出力を検証）
    Scripts/run_tests.sh
    Docs/TOOLS.md
  UEAgentToolkitNative/      ← 任意の C++ Editor プラグイン（Python API が無い機能のみ）
    Source/…                 ← Interface 実装, Macro, RPC/Replication Condition, Compiler/Message Log,
                                Behavior Tree（Simple Parallel 含む）/ Blackboard / EQS 編集, AnimBP State Machine,
                                Montage Section, BlendSpace, Sound Cue, MetaSound 部分編集, Landscape 作成, Widget Animation,
                                汎用プロパティ設定（テキスト形式・ネストパス）, 実行時フレーム統計
    Binaries/Win64/          ← UE 5.8 Win64 ビルド済み
    build.sh
```

Native プラグインが無い場合でも Python 側は動作し、該当 Tool は `NOT_SUPPORTED`（理由と対処付き）を返します。

## 導入

1. `Plugins/UEAgentToolkit` と（推奨）`Plugins/UEAgentToolkitNative` を対象プロジェクトの `Plugins/` にコピー。
2. `.uproject` で以下を有効化: `PythonScriptPlugin`, `EditorScriptingUtilities`, `ModelContextProtocol`, `AllToolsets`（または必要な Epic Toolset）, `EnhancedInput`, `UEAgentToolkit`, `UEAgentToolkitNative`。
3. MCP サーバー起動: Editor 設定 *Model Context Protocol > Auto Start Server* を ON（ポート 8000, パス `/mcp`）、または起動引数 `-ModelContextProtocolStartServer`。
   ※ `-unattended` 起動時は設定による自動起動が無効なので引数を使ってください。
4. Agent 側の MCP 設定（Claude Code の例。既に `unreal-mcp` として登録済み）:
   ```json
   { "unreal-mcp": { "type": "http", "url": "http://127.0.0.1:8000/mcp" } }
   ```
5. Native を再ビルドする場合（Editor を閉じて）: `Plugins/UEAgentToolkitNative/build.sh`

## Tool の返り値

Tool は次の JSON を**文字列**として返します（MCP の結果テキスト `{"returnValue": "<JSON>"}`）。
出力スキーマが 1 行で済むため `describe_toolset` が軽く、`details` は通常のネストしたオブジェクトです。

```json
{
  "success": false,
  "tool": "compile_blueprint",
  "target": "/Game/Characters/BP_Player",
  "modified": false,
  "dirtied_packages": [],
  "errors": [{"code": "COMPILE_FAILED", "message": "...", "target": "...",
              "likely_causes": ["[EventGraph] Add Movement Input: ..."], "retryable": false}],
  "warnings": [],
  "details": {"status": "error", "errors": [{"graph": "EventGraph", "node": "K2Node_CallFunction_0", "message": "..."}]}
}
```

- エラーコード: `INVALID_ARGUMENT, ASSET_NOT_FOUND, ACTOR_NOT_FOUND, OBJECT_NOT_FOUND, CLASS_NOT_FOUND, ALREADY_EXISTS, WRONG_TYPE, AMBIGUOUS, COMPILE_FAILED, EDITOR_STATE, CONFIRMATION_REQUIRED, NOT_SUPPORTED, UE_OPERATION_FAILED, INTERNAL_ERROR`
- 失敗時も `details` にレポート（Compile 結果、Dry Run プレビュー等）が入ります。
- 例外で落ちることはなく、必ず構造化結果を返します。

## 安全策

| 仕組み | 内容 |
|---|---|
| Undo | 変更系 Tool は自動で UE Transaction（`AgentToolkit: <tool>`）。`begin_transaction`/`end_transaction` で複数呼び出しを 1 Undo に統合、`undo`/`redo`。 |
| 確認 | 削除・一括リネーム・Redirector 修正・リストア等は `confirm=true` 必須。未指定時は `CONFIRMATION_REQUIRED` + プレビュー。 |
| Dry Run | `move_assets`, `save_dirty_assets`, `organize_actors_into_folders`, `set_project_maps_and_modes`, `register_default_mapping_context` 等。 |
| Backup | 削除前に自動バックアップ（`Saved/AgentToolkit/Backups`）。`create_backup` / `create_save_point` / `restore_backup` / `restore_save_point`。 |
| Config | `.ini` 書き換え前に自動バックアップ。対象キー以外は保持。 |
| 原子性 | 不正な引数は変更前に検証。途中失敗時は追加した変数/コンポーネントをロールバック。 |
| PIE ガード | 変更系 Tool は Play-In-Editor 中は `EDITOR_STATE` で拒否。 |
| 変更記録 | `get_change_journal`（`Saved/AgentToolkit/journal.jsonl`）、`list_modified_assets`、各結果の `dirtied_packages`。 |

## テスト

```bash
Plugins/UEAgentToolkit/Scripts/run_tests.sh            # Editor(-nullrhi) で全テスト（約1分）
MODE=cmd Plugins/UEAgentToolkit/Scripts/run_tests.sh   # Commandlet（Undo 等 Editor UI 依存テストは skip）
Plugins/UEAgentToolkit/Scripts/run_tests.sh "" test_inspector,test_assets
```
Editor 内では Session Frontend の `AI.Toolsets.UEAgentToolkit` からも実行できます。
現状: **75 tests / 全パス**（Tool 登録・Schema、正常系、無効 Asset、存在しない Object、Compile Error、Editor 状態、Undo/Redo、ロールバック）。

## Epic 公式 Toolset との分担（主なもの）

| 用途 | 使う Toolset |
|---|---|
| Blueprint 作成・親変更・関数グラフ・Graph DSL | `editor_toolset…BlueprintTools`（作成）+ `BlueprintAuthoringTools`（型付き変数/コンポーネント/関数/RPC/Interface/名前指定のノード編集）+ `BlueprintGraphTools`（JSON によるグラフ一括構築、ノード種類/ピンの事前調査、ノード検索、接続範囲の取得、自動整列、変数削除、関数引数追加、親クラス変更） |
| Material Graph 基本操作 / Material Instance | `MaterialTools`, `MaterialInstanceTools` + `MaterialAuthoringTools`（パラメータ一括作成、設定、Compile Error） |
| Widget / UMG | `UMGToolSet`, `MVVMToolset` + `UMGTools`（Canvas レイアウト、名前指定のプロパティ、Widget Animation） |
| Niagara | `NiagaraToolset_System` など |
| Sequencer | `animation_toolset…SequencerTools` 他 |
| Automation / Functional Test | `AutomationTestToolset` |
| Project Settings 任意セクション | `ConfigSettingsToolset` |
| Static/Skeletal Mesh, DataTable, StringTable | `editor_toolset` 各 Toolset |
| Live Coding | `LiveCodingToolset` |
| Gameplay Tags / GAS / PCG / Physics Asset / Plugin | 各公式 Toolset |

## 既知の制約

- **ノードメニュー文字列はエディタ言語でローカライズ**されます（例: `ポーン|インプット|AddMovementInput`）。本 Toolkit は関数パス・イベント関数名・変数名で言語非依存にノードを作ります。
- Asset Registry の参照情報は**保存済みファイル基準**。未保存の参照は検出できないため、削除/未使用判定の前に保存してください（警告を返します）。
- **Editor 通知（Slate Notification）は取得不可**：エンジンに通知一覧を列挙する公開 API が無く、エンジン改変なしでは実装できません。代替として Message Log（`get_message_log`）と Output Log を使ってください。
- `get_frame_stats` は描画中のみ有効（`-nullrhi` や描画前は 0 と警告）。PIE 中の取得を推奨。
- MetaSound：新規は `build_metasound_source`（一括構築）、既存アセットは Native プラグイン経由でノード ID 指定の部分編集（`inspect_metasound` → `add_metasound_node` / `connect_metasound_pins` / `set_metasound_input_defaults` / `add_metasound_graph_input` など）。Native 無しでは部分編集不可。
- EQS 編集はランタイムデータを直接編集し、エディタ用グラフは次回 EQS エディタを開いたときに再生成されます（ノード配置のみリセット）。
- IK Retarget：テンプレートに一致しないスケルトンでは自動チェーン生成されません。チェーン未定義の IK Rig でのリターゲットはエンジンがアサートで落ちるため、ツール側で事前に拒否します。
- Landscape のスカルプト／レイヤーペイント、Behavior Tree の Subtree ノード、Widget Animation の Event／Material トラックは未対応。
- Packaging は UAT を別プロセスで実行。保存されていない変更は含まれません（警告）。プラグインにランタイムコードがあるとコンテンツのみのプロジェクトでもコードビルドが走ります。
- `add_mapping_context_to_blueprint` はシングルプレイ向け（`GetPlayerController(0)`）。マルチは `register_default_mapping_context` を推奨。
- Commandlet モードでは Undo バッファ・アクターファクトリ・新規 Input Action のノードメニュー反映が無効（MCP 実運用の通常 Editor では有効）。
