# System Requirements

## Purpose

このシステムは、刀印ジェスチャーを入力ゲートにして、音声入力、Dify 応答、家電操作、読み上げ、アバター表示、TouchDesigner 演出をローカル環境でつなぐ。

## Success Conditions

- Camera Hub が刀印状態を topic として配信する。
- Vision Snapshot Processor が室内照明などの snapshot vision state を topic として配信する。
- sword-voice-agent が gesture topic を受けて、録音開始/停止の意図を生成する。
- ai-talk-core が録音、STT、handoff 保存を担当する。
- Dify watcher が handoff を Dify Chat App に送り、応答を受け取る。
- Home Assistant bridge が Dify tool side effect を安全に扱う。
- TTS service が Dify 応答を読み上げる。
- AITuberKit Projection Visual が発話、HUD、背景表示を担う。
- Environment State Server が Dify 用 state と表示用 indicator を分けて返す。
- TouchDesigner は UDP trigger と表示用 URL を使って演出する。

## Non Goals

- Camera Hub 以外が物理カメラを開くこと。
- Vision Snapshot Processor が物理カメラを直接開くこと。
- Environment State Server が gesture 推論や映像配信を行うこと。
- Projection や HUD が制御 state の authority になること。
- archives 配下の履歴文書を要求仕様として使うこと。
- API key、token、個人パスを README や fixture に固定すること。

## Operating Assumptions

- 主な開発環境は Windows と PowerShell。
- AITuberKit は `aituber-kit/` の別アプリとして起動する。
- Dify、VOICEVOX、TouchDesigner は外部アプリとして扱い、Home Control Stack の PID 管理対象にしない。
- Home Control Stack の停止処理は、管理台帳に載っている PID だけを扱う。
- loopback 外に公開する場合は、明示的な許可、token、Origin 制限を必要とする。

## Required Local Inputs

- Dify API URL と app API key。
- `mediapipe-sword-sign` の gesture model。
- Chrome のマイク権限。
- 必要に応じて Home Assistant action 設定。
- 必要に応じて AITuberKit の client ID と Message Receiver 設定。

## User Flow

1. Home Control Stack を起動する。
2. AITuberKit Projection Visual を開く。
3. 刀印を出す。
4. マイク入力が有効になった状態で話す。
5. Dify 応答、TTS、AITuberKit 発話、HUD、家電操作結果を確認する。
