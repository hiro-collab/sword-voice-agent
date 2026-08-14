'use strict'

/**
 * Launcher の「利用者が開く画面」と「ローカル参照先」を組み立てる枝モジュール。
 *
 * 根幹との関係:
 * - server.js がこの一覧を公開状態へ載せる。
 * - private service plan は Projection Visual の正式な投影 URL だけを再利用する。
 *
 * このモジュールがしないこと:
 * - サービスの起動・停止、HTTPアクセス、プロセス確認、権限判断は行わない。
 * - operator / stage-output / passive の役割を入れ替えない。
 *
 * 人間が画面構成を確認するときは、まずこのファイルを見る。
 */

const SURFACE_GROUP = Object.freeze({
  OPEN_IN_BROWSER: 'Open in browser',
  LOCAL_API_OR_FEED: 'Local APIs and feeds',
  BACKGROUND_REFERENCE: 'Background links'
})

// Quick Links の見た目を選ぶ公開分類。URLの意味や起動権限は持たない。
const SURFACE_PRESENTATION_KIND = Object.freeze({
  UI: 'ui',
  STAGE: 'stage',
  DIAGNOSTIC: 'diagnostic',
  THOUGHT: 'thought',
  DISPLAY: 'display',
  API: 'api',
  CAMERA: 'camera',
  WEBSOCKET: 'websocket',
  SPEECH: 'speech',
  BACKGROUND: 'background'
})

const normalizeLoopbackHttpUrl = (value) =>
  String(value || '').replace(/^http:\/\/localhost(?=:|\/|$)/u, 'http://127.0.0.1')

const buildProjectionVisualUrls = (aituberHost, aituberPort) => {
  const origin = `http://${aituberHost}:${aituberPort}`
  return Object.freeze({
    operator: `${origin}/projection-visual/`,
    // Fire/Thunder を受け取る唯一の正式な production receiver。
    stageOutput: `${origin}/projection-visual/?mode=stage-output&hud=0`,
    // display-state 互換表示。production effect receiver ではない。
    passive: `${origin}/projection-visual/?mode=passive&hud=0`
  })
}

const buildLauncherSurfaceCatalog = (options, selectedServiceIds) => {
  const selected = new Set(selectedServiceIds || [])
  const voicevoxUrl = normalizeLoopbackHttpUrl(
    options.VoicevoxUrl || 'http://127.0.0.1:50021'
  ).replace(/\/$/u, '')
  const thoughtCoreHost =
    options.ThoughtCoreHost === '0.0.0.0' ? '127.0.0.1' : options.ThoughtCoreHost
  const thoughtCoreUrl = `http://${thoughtCoreHost}:${options.ThoughtCorePort}`
  const projection = buildProjectionVisualUrls('127.0.0.1', options.AituberPort)
  const mediaUrl = encodeURIComponent(
    'http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true'
  )
  const wsUrl = encodeURIComponent(`ws://127.0.0.1:${options.MediapipePort}`)
  const browserMonitorUrl = `http://127.0.0.1:${options.MediapipeBrowserMonitorPort}/browser_camera_hub_viewer.html?mediaUrl=${mediaUrl}&wsUrl=${wsUrl}&target=sword_sign`

  return [
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.UI,
      name: 'Expression runtime',
      url: `http://127.0.0.1:${options.AituberPort}`,
      enabled: selected.has('aituber_kit')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.UI,
      name: 'Projection Visual',
      url: projection.operator,
      enabled: selected.has('aituber_kit')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.STAGE,
      name: 'Projection Stage Output',
      url: projection.stageOutput,
      enabled: selected.has('aituber_kit')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.DIAGNOSTIC,
      name: 'Projection Effect Diagnostic',
      url: `http://127.0.0.1:${options.AituberPort}/operator/projection-effect-diagnostic/`,
      enabled: selected.has('aituber_kit')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.STAGE,
      name: 'Passive Projection',
      url: projection.passive,
      enabled: selected.has('aituber_kit')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.DIAGNOSTIC,
      name: 'Body map inspector',
      url: `http://127.0.0.1:${options.AituberPort}/body-map-inspector?fov=60&scale=1`,
      enabled: selected.has('aituber_kit')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.THOUGHT,
      name: 'Thought Core API index',
      url: thoughtCoreUrl,
      enabled: selected.has('thought_core_api')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.DISPLAY,
      name: 'Display runtime GUI/API',
      url: `http://127.0.0.1:${options.TouchDesignerGuiPort}`,
      enabled: selected.has('touchdesigner_control_gui')
    },
    {
      group: SURFACE_GROUP.OPEN_IN_BROWSER,
      presentationKind: SURFACE_PRESENTATION_KIND.UI,
      name: 'Action bridge operator',
      url: `http://127.0.0.1:${options.HomeAssistantBridgePort}/operator`,
      enabled: selected.has('home_assistant_bridge')
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.API,
      name: 'Action bridge health',
      url: `http://127.0.0.1:${options.HomeAssistantBridgePort}/health`,
      enabled: selected.has('home_assistant_bridge')
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.API,
      name: 'Environment display state',
      url: `http://127.0.0.1:${options.EnvironmentStatePort}/indicators/current`,
      enabled: selected.has('environment_state_server')
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.CAMERA,
      name: 'Reflex browser monitor',
      url: browserMonitorUrl,
      enabled: selected.has('mediapipe_camera_hub_stack') && options.MediapipeMode === 'mediamtx'
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.CAMERA,
      name: 'Reflex camera video',
      url: 'http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true',
      enabled: selected.has('mediapipe_camera_hub_stack') && options.MediapipeMode === 'mediamtx'
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.WEBSOCKET,
      name: 'Reflex Camera Hub WebSocket',
      url: `ws://127.0.0.1:${options.MediapipePort}`,
      enabled: selected.has('mediapipe_camera_hub_stack')
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.WEBSOCKET,
      name: 'Vision snapshot WebSocket',
      url: `ws://127.0.0.1:${options.VisionSnapshotProcessorPort}`,
      enabled:
        selected.has('vision_snapshot_processor') &&
        options.MediapipeMode === 'mediamtx'
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.SPEECH,
      name: 'VOICEVOX',
      url: voicevoxUrl,
      enabled: selected.has('voicevox')
    },
    {
      group: SURFACE_GROUP.LOCAL_API_OR_FEED,
      presentationKind: SURFACE_PRESENTATION_KIND.THOUGHT,
      name: 'Thought Core health',
      url: `${thoughtCoreUrl}/health`,
      enabled: selected.has('thought_core_api')
    },
    {
      group: SURFACE_GROUP.BACKGROUND_REFERENCE,
      presentationKind: SURFACE_PRESENTATION_KIND.BACKGROUND,
      name: 'Thought Core watcher',
      url: 'no browser URL',
      enabled: selected.has('thought_core_watcher')
    },
    {
      group: SURFACE_GROUP.BACKGROUND_REFERENCE,
      presentationKind: SURFACE_PRESENTATION_KIND.DISPLAY,
      name: 'Display UDP receiver',
      url: '127.0.0.1:9001',
      enabled: true
    }
  ]
}

module.exports = {
  SURFACE_GROUP,
  SURFACE_PRESENTATION_KIND,
  buildLauncherSurfaceCatalog,
  buildProjectionVisualUrls,
  normalizeLoopbackHttpUrl
}
