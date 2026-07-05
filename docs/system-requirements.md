# System Requirements

## 目的

Sword Agent System は、ジェスチャー、音声入力、環境認識、家電操作、読み上げ、アバター表示、TouchDesigner投影をローカル環境でつなぐ AI身体OS です。

現在の主経路は `thought-core-v0` です。

## 成功条件

- MediaPipe Camera Hub がカメラ状態と刀印状態を topic として配信する。
- Vision Snapshot Processor が部屋の明るさなどの snapshot vision state を配信する。
- ai-talk-core が録音、STT、handoff 保存を担当する。
- Thought Core が turn単位の思考、状態確認、家電操作、再観測、応答を担当する。
- Environment State Server が state query と indicator を返す。
- Home Assistant bridge が allowlist された action を単発実行する。
- AITuber Kit Projection Visual が会話、HUD、アバター表示、背景表示を担う。
- VOICEVOX またはTTSサービスが応答を読み上げる。
- TouchDesigner制御GUIがUDP連携状態を確認できる。
- TouchDesigner本体は、必要に応じてプロジェクター投影用の `.toe` を開ける。

## 非目標

- Camera Hub 以外が物理カメラを直接開くこと。
- Vision Snapshot Processor が物理カメラを直接開くこと。
- Environment State Server が家電操作を実行すること。
- Home Control Server が意味レベルのretryや最終成功判定を行うこと。
- Projection Visual やHUDが制御stateのauthorityになること。
- Thought Core が Home Assistant のservice名やentity名を直接生成すること。
- API key、token、個人パスをREADMEやfixtureに固定すること。
- `archives/` 配下の履歴文書を現行仕様として使うこと。

## 前提

- 主な開発環境は Windows と PowerShell 7。
- system cell root は `<workspace>\sword-agent-os`。
- control plane repo は `<workspace>\sword-agent-os\control-plane\core`。
- 大きな機能repoは `organs/` 配下に置く。
- `.cache/home-control-stack` は現行互換runtimeとして残す。
- `runtime/` と `local/` は将来の正規配置として段階的に使う。

## 必要なローカル入力

- LLM API key。
- Home Assistant long-lived access token。
- `HOME_CONTROL_API_TOKEN`。
- MediaPipe sword sign model。
- Chrome のマイク権限。
- Webカメラとマイク。
- 必要に応じて TouchDesigner、VOICEVOX。

## 基本ユーザーフロー

1. `thought-core-v0` profile で Home Control Stack を起動する。
2. AITuber Kit Projection Visual を開く。
3. Chrome のマイク権限を許可する。
4. 刀印または画面操作で入力を開始する。
5. 音声で質問や家電操作を依頼する。
6. Thought Core が Environment State を観測し、必要なら Home Assistant bridge へ実行を依頼する。
7. 実行後に再観測し、結果を発話とHUDに反映する。
8. 必要に応じて TouchDesigner の `.toe` を開き、プロジェクターへ投影する。
