const LANGUAGE_STORAGE_KEY = 'sword.launcher.language'
const supportedLanguages = new Set(['en', 'ja'])

const readInitialLanguage = () => {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
    if (supportedLanguages.has(stored)) {
      return stored
    }
  } catch {
    // Local storage is optional; the launcher still works without persistence.
  }
  return String(window.navigator?.language || '').toLowerCase().startsWith('ja') ? 'ja' : 'en'
}

/**
 * ブラウザUIの枝。
 * server.jsの公開APIだけを使い、lifecycleの意味やprivate planを再実装しない。
 * 画面リンクの役割分類はsurface catalog、翻訳と描画はこのUIに集約する。
 */

const state = {
  language: readInitialLanguage(),
  profiles: [],
  selectedProfileId: 'thought-core-v0',
  configIdentity: null,
  options: {},
  busy: false,
  operation: 'idle',
  operationDetail: 'Waiting for an action.',
  operationProgress: {
    percent: 0,
    label: '0%',
    visible: false
  },
  remoteBusy: false,
  remoteOperation: null,
  latestServices: {},
  latestEndpoints: [],
  latestStatusTimestamp: '',
  startupTiming: null,
  diagnosticSurfaces: null,
  videoInputDevices: [],
  videoInputEnumerationClass: 'pending',
  videoInputSelectionClass: 'no_selection',
  demoSafeSettings: {
    rows: [],
    summary: { total: 0, enabled: 0, enabled_appliance: 0, enabled_readiness: 0 }
  },
  demoReadinessStatus: {
    rows: []
  },
  latestLogTailRaw: ''
}

const translations = {
  en: {
    'language.aria': 'Language',
    'operation.progressAria': 'Stack startup progress',
    'mission.aria': 'Launcher summary',
    'metric.readiness': 'Stack readiness',
    'metric.loading': 'Loading',
    'metric.readinessDetail': 'Checking local services',
    'metric.activeProfile': 'Active profile',
    'metric.profilePending': 'Configuration pending',
    'metric.onlineServices': 'Online services',
    'metric.awaitingStatus': 'Awaiting status',
    'metric.attention': 'Attention',
    'metric.noSignal': 'No signal yet',
    'launch.title': 'Launch configuration',
    'launch.profile': 'Profile',
    'launch.llmProvider': 'Conversation LLM',
    'provider.configured': 'Use environment setting',
    'provider.openai': 'OpenAI-compatible API',
    'provider.codex': 'Codex CLI (Terra / medium)',
    'provider.codexLuna': 'Codex CLI (Luna / low)',
    'provider.description': 'Changes only the Thought Core child process started by this launcher. Codex CLI stays response-only and read-only.',
    'launch.mediapipeStartup': 'MediaPipe startup',
    'launch.cameraName': 'Connected camera',
    'launch.refreshCameras': 'Refresh cameras',
    'launch.cameraSelectionPending': 'Checking connected cameras.',
    'launch.cameraSelectionAvailable': '{count} connected camera(s).',
    'launch.cameraSelectionMissing': 'The saved camera is currently missing. Selection retained.',
    'launch.cameraSelectionAmbiguous': 'The saved camera identity is ambiguous. Start is blocked until it is reselected.',
    'launch.cameraSelectionManual': 'Advanced manual camera name is active.',
    'launch.cameraSelectionNone': 'No connected camera is currently listed.',
    'launch.cameraSelectionUnavailable': 'Camera enumeration is unavailable. Saved selection retained.',
    'launch.cameraChoose': 'Choose a connected camera',
    'launch.cameraMissingSuffix': 'missing',
    'launch.cameraManualTitle': 'Advanced manual camera name',
    'launch.cameraManualDescription': 'Use only for a virtual or late-attached camera that cannot be listed.',
    'launch.cameraManualName': 'Manual camera name',
    'launch.cameraManualApply': 'Use manual name',
    'launch.cameraWidth': 'Width',
    'launch.cameraHeight': 'Height',
    'launch.cameraFps': 'Requested FPS',
    'launch.cameraInputCodec': 'Input codec',
    'launch.cameraInputCodecAuto': 'Auto',
    'launch.cameraRequestDescription': 'Requested capture settings; runtime diagnostics remain the authority for achieved FPS.',
    'mediapipe.normal': 'Normal',
    'mediapipe.normalTitle': 'Normal: MediaMTX video plus CameraHub WebSocket',
    'mediapipe.cameraHub': 'CameraHub only',
    'mediapipe.cameraHubTitle': 'Diagnostics: CameraHub WebSocket only',
    'mediapipe.gui': 'Python GUI',
    'mediapipe.guiTitle': 'Diagnostics: CameraHub plus Python GUI',
    'summary.title': 'Launch summary',
    'summary.aria': 'Launch configuration summary',
    'summary.services': 'Services',
    'summary.diagnostics': 'Diagnostics',
    'summary.provider': 'Provider',
    'summary.ports': 'Ports',
    'summary.demoSafe': 'Demo-safe',
    'summary.enabled': 'enabled',
    'summary.servicesNoun': 'services',
    'summary.off': 'Off',
    'summary.togglesOn': 'toggles on',
    'summary.noTestToggles': 'No test toggles',
    'summary.fallbackOnly': 'Fallback only',
    'summary.providerAllowed': 'Provider allowed',
    'summary.checkConflict': 'Check conflict',
    'summary.set': 'set',
    'summary.duplicatePorts': 'Duplicate port values',
    'summary.coreBindings': 'Core bindings',
    'launchScope.aria': 'Start Stack service scope',
    'launchScope.statement': 'Start Stack starts enabled/expected services only.',
    'launchScope.enabled': 'Enabled: {value}',
    'launchScope.skipped': 'Skipped: {value}',
    'value.pending': 'Pending',
    'value.none': 'none',
    'ports.title': 'Core ports',
    'ports.bindings': 'Port bindings',
    'services.drawerTitle': 'Core services',
    'services.drawerSummary': 'Normal system cell controls',
    'diagnostics.drawerTitle': 'Diagnostics',
    'diagnostics.drawerSummary': 'Camera and test-mode controls',
    'demoSafe.drawerTitle': 'Demo settings',
    'demoSafe.drawerSummary': '{enabled}/{total} enabled',
    'demoSafe.enabled': 'Enabled',
    'demoSafe.restoreRequired': 'Restore',
    'demoSafe.maxActions': 'Max actions',
    'demoSafe.maxDuration': 'Max seconds',
    'demoSafe.actions': 'Actions',
    'demoSafe.timing': 'Timing',
    'demoSafe.stimulus': 'Stimulus',
    'demoSafe.readiness': 'Readiness',
    'demoSafe.defaultOff': 'Default off',
    'demoSafe.noneEnabled': 'All off',
    'demoSafe.notApplicable': 'N/A',
    'demoSafe.appliance': 'Appliance',
    'demoSafe.audio': 'Audio',
    'demoSafe.avatar': 'Avatar',
    'demoSafe.display': 'Display',
    'demoSafe.general': 'General',
    'advanced.title': 'Advanced overrides',
    'advanced.subtitle': 'Paths and external services',
    'advanced.actionBridgeConfigPath': 'Action bridge config path',
    'surface.control': 'CONTROL',
    'surface.read': 'READ',
    'services.title': 'Services',
    'startup.title': 'Startup timing',
    'startup.subtitle': 'Critical path',
    'startup.elapsed': 'Elapsed',
    'startup.waiting': 'Waiting',
    'startup.ready': 'Ready',
    'startup.operational': 'Operational',
    'startup.degraded': 'Running without camera input',
    'startup.maxWait': 'max wait',
    'startup.maxWaitUnset': 'not set',
    'startup.service': 'Service',
    'startup.state': 'State',
    'startup.actual': 'Actual',
    'startup.secondsUnit': 's',
    'startup.editMaxWait': 'Edit max wait for {name}',
    'startup.critical': 'Critical path',
    'startup.noTiming': 'No startup timing yet',
    'diagnosticSurfaces.title': 'Diagnostic surfaces',
    'diagnosticSurfaces.subtitle': 'Source/static readiness',
    'diagnosticSurfaces.ready': 'ready',
    'diagnosticSurfaces.hold': 'hold',
    'diagnosticSurfaces.noLive': 'No live capture by default',
    'diagnosticSurfaces.nextRoute': 'Next route',
    'quickLinks.title': 'Quick Links',
    'quickLinks.subtitle': 'Local surfaces',
    'command.title': 'Command preview',
    'log.title': 'Launcher log',
    'log.recentOutput': 'Recent output',
    'log.metaAria': 'Launcher log display mode',
    'log.outputAria': 'Launcher log lines',
    'log.viewMode': 'Grouped by module',
    'log.copyMode': 'Copy keeps plain text',
    'port.expression': 'Expression',
    'port.thoughtCore': 'Thought Core',
    'port.displayRuntime': 'Display runtime',
    'port.actionBridge': 'Action bridge',
    'port.environment': 'Environment',
    'port.mediapipe': 'MediaPipe',
    'port.visionWs': 'Vision WS',
    'button.refresh': 'Refresh',
    'button.start': 'Start Stack',
    'button.starting': 'Starting...',
    'button.stopStack': 'Stop Stack',
    'button.stopping': 'Stopping...',
    'button.reclaimPorts': 'Recover Ports',
    'button.reclaiming': 'Recovering...',
    'button.stopLauncher': 'Stop Launcher Only',
    'button.save': 'Save',
    'button.saving': 'Saving...',
    'button.copy': 'Copy',
    'saveState.working': 'Working',
    'saveState.locked': 'Locked',
    'saveState.ready': 'Ready',
    'saveState.starting': 'Starting',
    'saveState.stopping': 'Stopping',
    'saveState.saving': 'Saving',
    'saveState.saved': 'Saved',
    'saveState.copied': 'Copied',
    'saveState.logCopied': 'Log copied',
    'saveState.error': 'Error',
    'operation.idle': 'Launcher standby',
    'operation.starting': 'Starting stack',
    'operation.started': 'Stack online',
    'operation.stopping': 'Stopping stack',
    'operation.reclaim': 'Recovering managed ports',
    'operation.stopped': 'Stack stopped',
    'operation.saving': 'Saving config',
    'operation.blocked': 'Action blocked',
    'operation.error': 'Action failed',
    'operation.waiting': 'Waiting for an action.',
    'operation.remoteBusy': 'Another {type} operation is running. Started {time}.',
    'readiness.starting': 'Starting',
    'readiness.stopping': 'Stopping',
    'readiness.stopped': 'Stopped',
    'readiness.locked': 'Locked',
    'readiness.error': 'Action failed',
    'readiness.ready': 'Ready',
    'readiness.manual': 'Manual',
    'readiness.checkStack': 'Check stack',
    'readiness.warmingUp': 'Warming up',
    'progress.stopping': 'Stopping...',
    'progress.checking': 'Checking...',
    'progress.remaining': '{percent}% · {remaining} {noun} remaining',
    'progress.service': 'service',
    'progress.services': 'services',
    'status.awaiting': 'Awaiting status',
    'status.unknown': 'Unknown',
    'status.noLog': 'No launcher log yet.',
    'status.expectedOnline': '{online}/{total} expected services online',
    'status.startWatching': '{online}/{total} expected services online. Watching startup progress.',
    'status.startAccepted': 'Start command accepted. Waiting for service status.',
    'status.allOnline': 'All expected services are online. Updated {time}.',
    'status.updated': 'Updated {time}',
    'status.nominal': 'All expected services nominal',
    'status.attention': '{warn} warming, {down} down',
    'stop.noVerification': 'Stop command completed, but shutdown verification was not returned.',
    'stop.verified': 'Stop verified. {count} managed ports are closed and no recorded stack process remains.',
    'stop.pidRegistry': 'PID registry still exists',
    'stop.alivePids': 'alive PIDs: {value}',
    'stop.openPorts': 'open ports: {value}',
    'stop.timedOut': 'verification timed out',
    'stop.incomplete': 'Stop incomplete. {issues}.',
    'stop.incompleteGeneric': 'Stop incomplete. Check the launcher log for remaining processes.',
    'service.systemCell': 'system cell',
    'service.profileOff': 'profile off',
    'service.requires': 'Requires {targets} to be enabled before Start Stack can include this target.',
    'service.blocked': 'Blocked',
    'service.targetTitle': 'Startup target only; current runtime state is unchanged.',
    'service.includeAria': 'Include {name} when Start Stack runs',
    'service.target': 'Target',
    'service.skip': 'Skip',
    'service.fixed': 'Fixed',
    'service.tableAria': 'Runtime organ status',
    'service.header.state': 'State',
    'service.header.organ': 'Organ',
    'service.header.target': 'Start target',
    'endpoint.skipped': 'skipped',
    'endpoint.websocketReference': 'WebSocket reference',
    'endpoint.backgroundReference': 'background reference',
    'endpoint.reference': 'reference',
    'endpoint.stageView': 'passive clean view',
    'endpoint.operatorPreview': 'operator preview',
    'endpoint.diagnostic': 'operator diagnostic',
    'endpoint.localApi': 'local API',
    'endpoint.cameraFeed': 'camera feed',
    'endpoint.displayRuntime': 'display runtime',
    'endpoint.speechRuntime': 'speech runtime',
    'endpoint.openBrowser': 'open browser',
    'endpoint.open': 'open',
    'endpoint.group.openBrowser': 'Open in browser',
    'endpoint.group.localApis': 'Local APIs and feeds',
    'endpoint.group.backgroundLinks': 'Background links',
    'action.startSending': 'Start command is being sent. Waiting for the supervisor to spawn.',
    'action.startAcceptedWatching': 'Start command accepted. Watching services come online.',
    'action.stopRunning': 'Stop command is running. Waiting for the shutdown script.',
    'action.reclaimPorts': 'Checking for route-owned managed port residue.',
    'action.reclaimPortsRecovered': 'Managed port residue recovered.',
    'action.reclaimPortsNone': 'No route-owned managed port residue found.',
    'action.stopLauncherConfirm': 'Stop Sword System Launcher only? System cell services are not stopped by this button.',
    'action.stopLauncherShuttingDown': 'Launcher server is shutting down. System cell services are unchanged.',
    'action.launcherStopped': 'Launcher stopped. Close this tab or start it again from the terminal.',
    'action.savingConfig': 'Writing launcher configuration.',
    'error.operationInProgress': 'Another operation is already running.',
    'error.checkLog': 'Check the launcher log for details.'
  },
  ja: {
    'language.aria': '表示言語',
    'operation.progressAria': '起動の進行状況',
    'mission.aria': 'ランチャー概要',
    'metric.readiness': '起動準備',
    'metric.loading': '読み込み中',
    'metric.readinessDetail': 'このPC上の機能を確認中',
    'metric.activeProfile': '選択中の構成',
    'metric.profilePending': '設定待ち',
    'metric.onlineServices': '稼働中の機能',
    'metric.awaitingStatus': '状態取得待ち',
    'metric.attention': '注意',
    'metric.noSignal': 'まだ状態未取得',
    'launch.title': '起動設定',
    'launch.profile': '構成',
    'launch.llmProvider': '会話LLM',
    'provider.configured': '環境設定に従う',
    'provider.openai': 'OpenAI互換API',
    'provider.codex': 'Codex CLI（Terra / medium）',
    'provider.codexLuna': 'Codex CLI（Luna / low）',
    'provider.description': 'このランチャーが起動するThought Core子プロセスだけを切り替えます。Codex CLIは応答専用・読取専用です。',
    'launch.mediapipeStartup': 'カメラ入力の起動方式',
    'launch.cameraName': '接続中のカメラ',
    'launch.refreshCameras': 'カメラ一覧を更新',
    'launch.cameraSelectionPending': '接続中のカメラを確認しています。',
    'launch.cameraSelectionAvailable': '接続中のカメラ: {count}台',
    'launch.cameraSelectionMissing': '保存済みのカメラは現在未接続です。選択は保持しています。',
    'launch.cameraSelectionAmbiguous': '保存済みのカメラ識別が曖昧です。再選択するまで起動しません。',
    'launch.cameraSelectionManual': '詳細設定の手動カメラ名を使用します。',
    'launch.cameraSelectionNone': '接続中のカメラは見つかりませんでした。',
    'launch.cameraSelectionUnavailable': 'カメラ一覧を取得できません。保存済みの選択は保持しています。',
    'launch.cameraChoose': '接続中のカメラを選択',
    'launch.cameraMissingSuffix': '未接続',
    'launch.cameraManualTitle': '詳細: カメラ名を手動入力',
    'launch.cameraManualDescription': '一覧に出ない仮想カメラや後から接続したカメラに限って使用します。',
    'launch.cameraManualName': '手動カメラ名',
    'launch.cameraManualApply': '手動名を使用',
    'launch.cameraWidth': '幅',
    'launch.cameraHeight': '高さ',
    'launch.cameraFps': '要求FPS',
    'launch.cameraInputCodec': '入力codec',
    'launch.cameraInputCodecAuto': '自動',
    'launch.cameraRequestDescription': '撮像の要求値です。実際のFPSは実行時診断の値を確認してください。',
    'mediapipe.normal': '通常',
    'mediapipe.normalTitle': '通常: MediaMTX映像とCameraHub通信',
    'mediapipe.cameraHub': 'CameraHubのみ',
    'mediapipe.cameraHubTitle': '診断: CameraHub通信のみ',
    'mediapipe.gui': 'Pythonカメラ画面',
    'mediapipe.guiTitle': '診断: CameraHubとPythonカメラ画面',
    'summary.title': '起動内容',
    'summary.aria': '起動設定の要約',
    'summary.services': '起動機能',
    'summary.diagnostics': '診断',
    'summary.provider': '会話LLM',
    'summary.ports': 'ポート',
    'summary.demoSafe': 'デモ安全',
    'summary.enabled': '有効',
    'summary.servicesNoun': '機能',
    'summary.off': 'なし',
    'summary.togglesOn': '項目有効',
    'summary.noTestToggles': 'テスト項目なし',
    'summary.fallbackOnly': '簡易応答のみ',
    'summary.providerAllowed': '会話LLMを使用',
    'summary.checkConflict': '競合確認',
    'summary.set': '設定済み',
    'summary.duplicatePorts': '重複ポートあり',
    'summary.coreBindings': '中核ポート割り当て',
    'launchScope.aria': '起動対象の範囲',
    'launchScope.statement': 'Start Stack は有効な起動対象だけを開始します。',
    'launchScope.enabled': '有効: {value}',
    'launchScope.skipped': '起動しない: {value}',
    'value.pending': '待機中',
    'value.none': 'なし',
    'ports.title': '中核ポート',
    'ports.bindings': 'ポート割り当て',
    'services.drawerTitle': '起動する中核機能',
    'services.drawerSummary': '通常構成で起動する機能',
    'diagnostics.drawerTitle': '診断',
    'diagnostics.drawerSummary': 'カメラと診断用の項目',
    'demoSafe.drawerTitle': 'デモ設定',
    'demoSafe.drawerSummary': '{enabled}/{total} 有効',
    'demoSafe.enabled': '有効',
    'demoSafe.restoreRequired': '復元',
    'demoSafe.maxActions': '最大操作',
    'demoSafe.maxDuration': '最大秒数',
    'demoSafe.actions': '操作',
    'demoSafe.timing': '時間',
    'demoSafe.stimulus': '刺激',
    'demoSafe.readiness': '準備',
    'demoSafe.defaultOff': '初期値はオフ',
    'demoSafe.noneEnabled': 'すべてオフ',
    'demoSafe.notApplicable': '対象外',
    'demoSafe.appliance': '家電',
    'demoSafe.audio': '音声',
    'demoSafe.avatar': 'アバター',
    'demoSafe.display': '表示',
    'demoSafe.general': 'その他',
    'advanced.title': '詳細設定',
    'advanced.subtitle': 'パスと外部接続',
    'advanced.actionBridgeConfigPath': '家電操作ブリッジ設定パス',
    'surface.control': '操作',
    'surface.read': '確認',
    'services.title': '機能の状態',
    'startup.title': '起動タイミング',
    'startup.subtitle': '時間がかかっている箇所',
    'startup.elapsed': '経過',
    'startup.waiting': '待機中',
    'startup.ready': '準備済み',
    'startup.operational': '稼働',
    'startup.degraded': 'カメラ入力なしで稼働中',
    'startup.maxWait': '最大待ち',
    'startup.maxWaitUnset': '設定なし',
    'startup.service': '機能',
    'startup.state': '状態',
    'startup.actual': '実測',
    'startup.secondsUnit': '秒',
    'startup.editMaxWait': '{name} の最大待ち秒数を編集',
    'startup.critical': '律速箇所',
    'startup.noTiming': '起動タイミングはまだありません',
    'diagnosticSurfaces.title': '診断面',
    'diagnosticSurfaces.subtitle': 'ソース静的準備',
    'diagnosticSurfaces.ready': '準備済み',
    'diagnosticSurfaces.hold': '保留',
    'diagnosticSurfaces.noLive': '既定ではライブ取得しません',
    'diagnosticSurfaces.nextRoute': '次ルート',
    'quickLinks.title': '確認リンク',
    'quickLinks.subtitle': 'このPC上の画面',
    'command.title': '起動コマンド確認',
    'log.title': 'ランチャー記録',
    'log.recentOutput': '直近の出力',
    'log.metaAria': 'ランチャー記録の表示方式',
    'log.outputAria': 'ランチャー記録の行',
    'log.viewMode': '機能別に整理',
    'log.copyMode': 'コピーは通常テキスト',
    'port.expression': '表情表示',
    'port.thoughtCore': '思考中枢',
    'port.displayRuntime': '投影表示',
    'port.actionBridge': '操作ブリッジ',
    'port.environment': '環境状態',
    'port.mediapipe': 'MediaPipe',
    'port.visionWs': '視覚状態WS',
    'button.refresh': '更新',
    'button.start': '起動する',
    'button.starting': '起動中...',
    'button.stopStack': '全体を停止',
    'button.stopping': '停止中...',
    'button.reclaimPorts': '管理ポートを回収',
    'button.reclaiming': '回収中...',
    'button.stopLauncher': 'ランチャーだけ停止',
    'button.save': '保存',
    'button.saving': '保存中...',
    'button.copy': 'コピー',
    'saveState.working': '処理中',
    'saveState.locked': 'ロック中',
    'saveState.ready': '準備完了',
    'saveState.starting': '起動中',
    'saveState.stopping': '停止中',
    'saveState.saving': '保存中',
    'saveState.saved': '保存済み',
    'saveState.copied': 'コピー済み',
    'saveState.logCopied': 'ログをコピー済み',
    'saveState.error': 'エラー',
    'operation.idle': '起動待機中',
    'operation.starting': '起動処理中',
    'operation.started': '稼働中',
    'operation.stopping': '停止処理中',
    'operation.reclaim': '管理ポート回収中',
    'operation.stopped': '停止済み',
    'operation.saving': '設定保存中',
    'operation.blocked': '操作できません',
    'operation.error': '操作失敗',
    'operation.waiting': '操作待ちです。',
    'operation.remoteBusy': '別の{type}操作が実行中です。開始: {time}。',
    'readiness.starting': '起動中',
    'readiness.stopping': '停止中',
    'readiness.stopped': '停止済み',
    'readiness.locked': 'ロック中',
    'readiness.error': '操作失敗',
    'readiness.ready': '準備完了',
    'readiness.manual': '手動',
    'readiness.checkStack': '起動状態を確認',
    'readiness.warmingUp': '起動調整中',
    'progress.stopping': '停止中...',
    'progress.checking': '確認中...',
    'progress.remaining': '{percent}% · 残り{remaining}{noun}',
    'progress.service': '機能',
    'progress.services': '機能',
    'status.awaiting': '状態取得待ち',
    'status.unknown': '不明',
    'status.noLog': 'ランチャー記録はまだありません。',
    'status.expectedOnline': '{online}/{total} 起動対象が稼働中',
    'status.startWatching': '{online}/{total} 起動対象が稼働中。起動の進行を確認中。',
    'status.startAccepted': '起動コマンドを受理しました。機能の状態を待っています。',
    'status.allOnline': '起動対象はすべて稼働中です。更新: {time}。',
    'status.updated': '更新: {time}',
    'status.nominal': '起動対象はすべて正常',
    'status.attention': '起動調整中 {warn}、停止中 {down}',
    'stop.noVerification': '停止コマンドは完了しましたが、停止検証が返りませんでした。',
    'stop.verified': '停止を検証しました。管理対象ポート {count} 件は閉じており、記録済みの起動プロセスは残っていません。',
    'stop.pidRegistry': 'PIDレジストリが残っています',
    'stop.alivePids': '生存PID: {value}',
    'stop.openPorts': '開いているポート: {value}',
    'stop.timedOut': '検証タイムアウト',
    'stop.incomplete': '停止未完了。{issues}。',
    'stop.incompleteGeneric': '停止未完了。残存プロセスはランチャー記録を確認してください。',
    'service.systemCell': '中核システム',
    'service.profileOff': 'この構成では使わない',
    'service.requires': 'Start Stackにこの対象を含めるには、先に {targets} を起動対象にしてください。',
    'service.blocked': '対象外',
    'service.targetTitle': '起動対象だけを変更します。現在の実行状態は変わりません。',
    'service.includeAria': 'Start Stack実行時に {name} を含める',
    'service.target': '対象',
    'service.skip': '起動しない',
    'service.fixed': '固定',
    'service.tableAria': '実行中機能の状態',
    'service.header.state': '状態',
    'service.header.organ': '機能',
    'service.header.target': '起動対象',
    'endpoint.skipped': '対象外',
    'endpoint.websocketReference': 'WebSocket参照',
    'endpoint.backgroundReference': '裏側の参照',
    'endpoint.reference': '参照',
    'endpoint.stageView': '投影表示',
    'endpoint.operatorPreview': '操作プレビュー',
    'endpoint.diagnostic': '操作診断',
    'endpoint.localApi': 'ローカルAPI',
    'endpoint.cameraFeed': 'カメラ映像',
    'endpoint.displayRuntime': '表示実行画面',
    'endpoint.speechRuntime': '音声実行画面',
    'endpoint.openBrowser': 'ブラウザーを開く',
    'endpoint.open': '開く',
    'endpoint.group.openBrowser': 'ブラウザーで開く',
    'endpoint.group.localApis': 'ローカルAPIと状態配信',
    'endpoint.group.backgroundLinks': '裏側の参照リンク',
    'action.startSending': '起動コマンドを送信中です。管理プロセスの起動を待っています。',
    'action.startAcceptedWatching': '起動コマンドを受理しました。各機能が稼働するまで確認します。',
    'action.stopRunning': '停止コマンドを実行中です。シャットダウンスクリプトを待っています。',
    'action.reclaimPorts': '管理対象ポートに残ったプロセスを確認しています。',
    'action.reclaimPortsRecovered': '管理対象ポートの残存プロセスを回収しました。',
    'action.reclaimPortsNone': '回収できる管理対象ポートの残存プロセスはありません。',
    'action.stopLauncherConfirm': 'Sword System Launcherだけを停止しますか？このボタンでは中核システムの各機能は停止しません。',
    'action.stopLauncherShuttingDown': 'ランチャーサーバーを停止中です。中核システムの各機能は変更されません。',
    'action.launcherStopped': 'ランチャーを停止しました。このタブを閉じるか、ターミナルから再起動してください。',
    'action.savingConfig': 'ランチャー設定を書き込み中です。',
    'error.operationInProgress': '別の操作がすでに実行中です。',
    'error.checkLog': '詳細はランチャーログを確認してください。'
  }
}

const t = (key, values = {}) => {
  const table = translations[state.language] || translations.en
  const template = table[key] || translations.en[key] || key
  return template.replace(/\{([^}]+)\}/g, (_, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : `{${name}}`
  )
}

const coreSwitchFields = [
  'StopExisting',
  'EnableThoughtCore',
  'EnableThoughtCoreWatch',
  'ThoughtCoreNoProvider',
  'SkipAituber',
  'SkipHomeAssistantBridge',
  'SkipEnvironmentState',
  'SkipMediapipe',
  'SkipVisionSnapshotProcessor',
  'SkipTouchDesignerGui',
  'SkipVoicevoxCheck'
]

const diagnosticSwitchFields = [
  'MediapipeOpenBrowser',
  'MediapipeNoBrowser',
  'MediapipePythonGui',
  'EnableHomeControlFaultInjection'
]

const portFields = [
  'AituberPort',
  'ThoughtCorePort',
  'TouchDesignerGuiPort',
  'HomeAssistantBridgePort',
  'EnvironmentStatePort',
  'MediapipePort',
  'VisionSnapshotProcessorPort'
]

const numericOptionFields = [
  ...portFields,
  'MediapipeCameraWidth',
  'MediapipeCameraHeight',
  'MediapipeCameraFps'
]

const readyTimeoutFieldByService = Object.freeze({
  voicevox: 'VoicevoxReadyTimeoutSeconds',
  mediapipe: 'MediapipeReadyTimeoutSeconds'
})

const readyTimeoutOptionFields = Object.values(readyTimeoutFieldByService)

const readyTimeoutFieldForService = (serviceId) =>
  Object.hasOwn(readyTimeoutFieldByService, serviceId)
    ? readyTimeoutFieldByService[serviceId]
    : null

const textFields = [
  'MediapipeCameraInputCodec',
  'VoicevoxUrl',
  'HomeControlConfigPath'
]

const serviceLabels = {
  home_assistant_bridge: 'Action bridge',
  environment_state_server: 'Environment state',
  mediapipe: 'Reflex sensor',
  vision_snapshot_processor: 'Vision snapshot',
  aituber_kit: 'Expression runtime',
  touchdesigner_control_gui: 'Display runtime GUI',
  thought_core_api: 'Thought Core API',
  thought_core_watcher: 'Thought Core watcher',
  voicevox: 'VOICEVOX speech'
}
const serviceLabelsJa = {
  home_assistant_bridge: '操作ブリッジ',
  environment_state_server: '環境状態',
  mediapipe: 'カメラ反射入力',
  vision_snapshot_processor: '視覚状態の取得',
  aituber_kit: '表情表示',
  touchdesigner_control_gui: '投影表示GUI',
  thought_core_api: '思考中枢API',
  thought_core_watcher: '思考中枢の監視',
  voicevox: 'VOICEVOX音声'
}
const hiddenServiceKeys = new Set()
const serviceRoles = {
  home_assistant_bridge: 'action boundary',
  environment_state_server: 'environment',
  mediapipe: 'reflex',
  vision_snapshot_processor: 'environment input',
  aituber_kit: 'expression',
  touchdesigner_control_gui: 'display',
  thought_core_api: 'conscious API',
  thought_core_watcher: 'conscious bridge',
  voicevox: 'speech'
}
const serviceRolesJa = {
  home_assistant_bridge: '操作境界',
  environment_state_server: '環境',
  mediapipe: '反射',
  vision_snapshot_processor: '視覚入力',
  aituber_kit: '表情',
  touchdesigner_control_gui: '表示',
  thought_core_api: '思考中枢',
  thought_core_watcher: '思考監視',
  voicevox: '音声'
}

const launchScopeItems = [
  { field: 'EnableThoughtCore', label: 'Thought Core API', enabledWhen: 'truthy' },
  { field: 'EnableThoughtCoreWatch', label: 'Thought Core watcher', enabledWhen: 'truthy' },
  { field: 'SkipAituber', label: 'Expression UI', enabledWhen: 'falsy' },
  { field: 'SkipHomeAssistantBridge', label: 'Action bridge', enabledWhen: 'falsy' },
  { field: 'SkipEnvironmentState', label: 'Environment state', enabledWhen: 'falsy' },
  { field: 'SkipMediapipe', label: 'Reflex sensor', enabledWhen: 'falsy' },
  { field: 'SkipVisionSnapshotProcessor', label: 'Vision snapshot', enabledWhen: 'falsy' },
  { field: 'SkipTouchDesignerGui', label: 'Display runtime GUI', enabledWhen: 'falsy' },
  { field: 'SkipVoicevoxCheck', label: 'VOICEVOX readiness check', enabledWhen: 'falsy' }
]

const launchScopeLabelsJa = {
  EnableThoughtCore: '思考中枢API',
  EnableThoughtCoreWatch: '思考中枢の監視',
  SkipAituber: '表情表示',
  SkipHomeAssistantBridge: '操作ブリッジ',
  SkipEnvironmentState: '環境状態',
  SkipMediapipe: 'カメラ反射入力',
  SkipVisionSnapshotProcessor: '視覚状態の取得',
  SkipTouchDesignerGui: '投影表示GUI',
  SkipVoicevoxCheck: 'VOICEVOX準備確認'
}

const fieldLabels = {
  StopExisting: 'Restart managed services first',
  EnableThoughtCore: 'Start Thought Core API',
  EnableThoughtCoreWatch: 'Run Thought Core watcher',
  ThoughtCoreNoProvider: 'Use configured conversation LLM',
  SkipAituber: 'Start expression UI',
  SkipHomeAssistantBridge: 'Start action bridge',
  SkipEnvironmentState: 'Start environment state',
  SkipMediapipe: 'Start reflex sensor',
  SkipVisionSnapshotProcessor: 'Start vision snapshot',
  SkipTouchDesignerGui: 'Start display runtime GUI',
  SkipVoicevoxCheck: 'Require VOICEVOX readiness check',
  MediapipeOpenBrowser: 'Open MediaPipe monitor',
  MediapipeNoBrowser: 'Hide MediaPipe monitor',
  MediapipePythonGui: 'Use Python camera GUI',
  EnableHomeControlFaultInjection: 'Enable action bridge fault injection'
}
const fieldLabelsJa = {
  StopExisting: '管理対象機能を先に再起動',
  EnableThoughtCore: '思考中枢APIを起動',
  EnableThoughtCoreWatch: '思考中枢の監視を実行',
  ThoughtCoreNoProvider: '設定済み会話LLMを使用',
  SkipAituber: '表情表示を起動',
  SkipHomeAssistantBridge: '操作ブリッジを起動',
  SkipEnvironmentState: '環境状態を起動',
  SkipMediapipe: 'カメラ反射入力を起動',
  SkipVisionSnapshotProcessor: '視覚状態の取得を起動',
  SkipTouchDesignerGui: '投影表示GUIを起動',
  SkipVoicevoxCheck: 'VOICEVOX準備確認を必須にする',
  MediapipeOpenBrowser: 'カメラ確認画面（MediaPipe）を開く',
  MediapipeNoBrowser: 'カメラ確認画面（MediaPipe）を隠す',
  MediapipePythonGui: 'Pythonカメラ画面を使う',
  EnableHomeControlFaultInjection: '操作ブリッジ障害注入を有効化'
}

const switchDescriptions = {
  ThoughtCoreNoProvider:
    'Checked lets ordinary conversation use the configured Thought Core LLM provider. Unchecked starts local fallback-only mode.'
}
const switchDescriptionsJa = {
  ThoughtCoreNoProvider:
    'チェック時は通常会話で設定済み会話LLMを使います。未チェック時は外部LLMを使わない簡易応答のみで起動します。'
}

const positiveDisplayFields = new Set(['ThoughtCoreNoProvider'])

const enableFieldsByService = {
  thought_core_api: ['EnableThoughtCore'],
  thought_core_watcher: ['EnableThoughtCoreWatch']
}

const skipFieldsByService = {
  home_assistant_bridge: ['SkipHomeAssistantBridge'],
  environment_state_server: ['SkipEnvironmentState'],
  mediapipe: ['SkipMediapipe'],
  vision_snapshot_processor: ['SkipVisionSnapshotProcessor', 'SkipMediapipe'],
  aituber_kit: ['SkipAituber'],
  touchdesigner_control_gui: ['SkipTouchDesignerGui'],
  voicevox: ['SkipVoicevoxCheck', 'SkipAituber']
}

const startupTargetFieldsByService = {
  thought_core_api: ['EnableThoughtCore'],
  thought_core_watcher: ['EnableThoughtCoreWatch'],
  home_assistant_bridge: ['SkipHomeAssistantBridge'],
  environment_state_server: ['SkipEnvironmentState'],
  mediapipe: ['SkipMediapipe'],
  vision_snapshot_processor: ['SkipVisionSnapshotProcessor'],
  aituber_kit: ['SkipAituber'],
  touchdesigner_control_gui: ['SkipTouchDesignerGui'],
  voicevox: ['SkipVoicevoxCheck']
}

const startupTargetDependenciesByService = {
  vision_snapshot_processor: [{ field: 'SkipMediapipe', label: 'Reflex sensor', labelJa: 'カメラ反射入力' }],
  voicevox: [{ field: 'SkipAituber', label: 'Expression UI', labelJa: '表情表示' }]
}

const operationLabels = {
  idle: 'Launcher standby',
  starting: 'Starting stack',
  started: 'Stack online',
  stopping: 'Stopping stack',
  reclaim: 'Recovering display port',
  stopped: 'Stack stopped',
  saving: 'Saving config',
  blocked: 'Action blocked',
  error: 'Action failed'
}
const operationLabelKeys = {
  idle: 'operation.idle',
  starting: 'operation.starting',
  started: 'operation.started',
  stopping: 'operation.stopping',
  reclaim: 'operation.reclaim',
  stopped: 'operation.stopped',
  saving: 'operation.saving',
  blocked: 'operation.blocked',
  error: 'operation.error'
}

const $ = (id) => document.getElementById(id)

const operationLabel = (operation) => t(operationLabelKeys[operation] || 'operation.idle')

const localizedFieldLabel = (value) =>
  state.language === 'ja' && fieldLabelsJa[value] ? fieldLabelsJa[value] : fieldLabels[value]

const localizedSwitchDescription = (field) =>
  state.language === 'ja' && switchDescriptionsJa[field] ? switchDescriptionsJa[field] : switchDescriptions[field]

const localizedLaunchScopeLabel = (item) =>
  state.language === 'ja' && launchScopeLabelsJa[item.field] ? launchScopeLabelsJa[item.field] : item.label

const localizedServiceLabel = (name) =>
  state.language === 'ja' && serviceLabelsJa[name] ? serviceLabelsJa[name] : serviceLabels[name]

const localizedServiceRole = (name) =>
  state.language === 'ja' && serviceRolesJa[name] ? serviceRolesJa[name] : serviceRoles[name]

const setSaveState = (key) => {
  $('save-state').textContent = t(key)
}

const updateLanguageButtons = () => {
  document.documentElement.lang = state.language
  document.querySelectorAll('[data-language]').forEach((button) => {
    const active = button.dataset.language === state.language
    button.classList.toggle('active', active)
    button.setAttribute('aria-pressed', active ? 'true' : 'false')
  })
}

const applyStaticTranslations = () => {
  updateLanguageButtons()
  document.querySelectorAll('[data-i18n]').forEach((element) => {
    element.textContent = t(element.dataset.i18n)
  })
  document.querySelectorAll('[data-i18n-title]').forEach((element) => {
    element.title = t(element.dataset.i18nTitle)
  })
  document.querySelectorAll('[data-i18n-aria-label]').forEach((element) => {
    element.setAttribute('aria-label', t(element.dataset.i18nAriaLabel))
  })
  $('copy-command').textContent = t('button.copy')
  $('copy-log').textContent = t('button.copy')
  $('log-path').textContent = t('log.recentOutput')
  if (!state.busy) {
    $('save-state').textContent = state.remoteBusy ? t('saveState.locked') : t('saveState.ready')
  }
  if (!state.latestStatusTimestamp) {
    $('status-time').textContent = t('status.awaiting')
  }
}

const setLanguage = (language) => {
  if (!supportedLanguages.has(language) || state.language === language) {
    return
  }
  state.language = language
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  } catch {
    // Local storage is optional; the selected language still applies in-memory.
  }
  applyLanguage()
}

const applyLanguage = () => {
  applyStaticTranslations()
  renderOperation()
  renderActionButtons()
  renderControls()
  renderSystemSummary(state.latestServices || null, state.latestStatusTimestamp)
  renderServices(state.latestServices || {})
  renderEndpoints(state.latestEndpoints || [])
  renderStartupTiming(state.startupTiming)
  renderDiagnosticSurfaces(state.diagnosticSurfaces)
  renderLauncherLog(state.latestLogTailRaw)
}

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.message || payload.error || `HTTP ${response.status}`)
    error.status = response.status
    error.payload = payload
    throw error
  }
  return payload
}

const setBusy = (busy, label = '') => {
  state.busy = busy
  document.body.dataset.busy = busy ? 'true' : 'false'
  $('save-state').textContent = busy
    ? label || t('saveState.working')
    : state.remoteBusy
      ? t('saveState.locked')
      : t('saveState.ready')
  renderActionButtons()
}

const renderActionButtons = () => {
  const disabled = state.busy || state.remoteBusy
  for (const id of ['start-button', 'stop-button', 'refresh-button', 'reclaim-ports-button', 'save-config', 'stop-launcher-button']) {
    $(id).disabled = disabled
  }
  $('start-button').textContent =
    state.busy && state.operation === 'starting' ? t('button.starting') : t('button.start')
  $('stop-button').textContent =
    state.busy && state.operation === 'stopping' ? t('button.stopping') : t('button.stopStack')
  $('reclaim-ports-button').textContent =
    state.busy && state.operation === 'reclaim' ? t('button.reclaiming') : t('button.reclaimPorts')
  $('stop-launcher-button').textContent =
    state.busy && state.operation === 'stopping' ? t('button.stopping') : t('button.stopLauncher')
  $('refresh-button').textContent = t('button.refresh')
  $('save-config').textContent =
    state.busy && state.operation === 'saving' ? t('button.saving') : t('button.save')
}

const setOperation = (operation, detail = '') => {
  state.operation = operation
  state.operationDetail = detail || operationLabel(operation) || ''
  if (!['starting', 'stopping'].includes(operation)) {
    state.operationProgress = {
      percent: operation === 'started' || operation === 'stopped' ? 100 : 0,
      label: operation === 'started' ? '100%' : '',
      visible: operation === 'started' || operation === 'stopped'
    }
  }
  document.body.dataset.operation = operation
  renderOperation()
  renderOperationReadiness()
  renderActionButtons()
}

const renderOperation = () => {
  const banner = $('operation-banner')
  const operation = state.operation || 'idle'
  banner.hidden = operation === 'idle'
  banner.className = `operation-banner ${operation}`
  $('operation-title').textContent = operationLabel(operation)
  $('operation-detail').textContent = state.operationDetail || t('operation.waiting')
  const progress = state.operationProgress || {}
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0))
  const progressNode = $('operation-progress')
  progressNode.hidden = !progress.visible
  progressNode.setAttribute('aria-valuenow', String(Math.round(percent)))
  $('operation-progress-bar').style.width = `${percent}%`
  $('operation-progress-label').hidden = !progress.visible
  $('operation-progress-label').textContent = progress.label || `${Math.round(percent)}%`
}

const renderOperationReadiness = () => {
  const readinessStates = {
    starting: ['readiness.starting', 'warn'],
    stopping: ['readiness.stopping', 'warn'],
    reclaim: ['readiness.checkStack', 'warn'],
    stopped: ['readiness.stopped', 'warn'],
    blocked: ['readiness.locked', 'warn'],
    error: ['readiness.error', 'down']
  }
  const readiness = readinessStates[state.operation]
  if (!readiness) {
    return
  }
  const readinessCard = $('readiness-card')
  readinessCard.classList.remove('ready', 'warn', 'down')
  readinessCard.classList.add(readiness[1])
  $('readiness-label').textContent = t(readiness[0])
  $('readiness-detail').textContent = state.operationDetail
}

const operationUiType = (operationType) => {
  if (operationType === 'start') {
    return 'starting'
  }
  if (operationType === 'stop') {
    return 'stopping'
  }
  if (operationType === 'reclaim') {
    return 'reclaim'
  }
  if (operationType === 'save') {
    return 'saving'
  }
  return 'blocked'
}

const applyServerOperation = (operation) => {
  const wasRemoteBusy = state.remoteBusy
  const remoteBusy = Boolean(operation && operation.busy)
  state.remoteBusy = remoteBusy && !state.busy
  state.remoteOperation = remoteBusy ? operation : null

  if (state.remoteBusy) {
    const uiOperation = operationUiType(operation.type)
    setOperation(
      uiOperation,
      t('operation.remoteBusy', { type: operation.type, time: formatTimestamp(operation.startedAt) })
    )
    setSaveState('saveState.locked')
    return
  }

  if (wasRemoteBusy && !state.busy && ['starting', 'stopping', 'saving', 'blocked'].includes(state.operation)) {
    setOperation('idle')
  }
  renderActionButtons()
}

const profileOptions = () => {
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  return profile ? profile.options || {} : {}
}

const currentOptions = () => ({
  ...state.options
})

const applyPreviewOptions = (previewOptions) => {
  const cameraSelection = state.options.MediapipeCameraName
  const cameraSelectionKey = state.options.MediapipeCameraSelectionKey
  state.options = {
    ...(previewOptions || {}),
    MediapipeCameraName: cameraSelection,
    MediapipeCameraSelectionKey: cameraSelectionKey
  }
}

const videoInputDevices = () =>
  (state.videoInputDevices || [])
    .map((device) => ({
      value: String(device?.value || '').trim(),
      label: String(device?.label || '').trim()
    }))
    .filter((device) => device.value && device.label)

const normalizeCameraSelection = (value) => {
  const name = String(value || '').trim()
  return name && name.length <= 256 && !/[\u0000-\u001f\u007f]/.test(name) ? name : ''
}

const renderCameraSelector = () => {
  const select = $('MediapipeCameraSelectionKey')
  const selectedKey = String(state.options.MediapipeCameraSelectionKey || '').trim()
  const selectedLabel = String(state.options.MediapipeCameraName || '').trim()
  const devices = videoInputDevices()
  const selectedMatch = Boolean(
    selectedKey && devices.some((device) => device.value === selectedKey)
  )
  const options = []

  if (!selectedKey) {
    options.push(`<option value="">${escapeHtml(t('launch.cameraChoose'))}</option>`)
  } else if (!selectedMatch) {
    options.push(
      `<option value="${escapeHtml(selectedKey)}">${escapeHtml(selectedLabel || t('launch.cameraName'))} (${escapeHtml(t('launch.cameraMissingSuffix'))})</option>`
    )
  }
  for (const device of devices) {
    options.push(
      `<option value="${escapeHtml(device.value)}">${escapeHtml(device.label)}</option>`
    )
  }
  select.innerHTML = options.join('')
  select.value = selectedKey

  let statusKey = 'launch.cameraSelectionPending'
  let statusValues = {}
  if (state.videoInputSelectionClass === 'selected_ambiguous') {
    statusKey = 'launch.cameraSelectionAmbiguous'
  } else if (state.videoInputSelectionClass === 'manual_selection') {
    statusKey = 'launch.cameraSelectionManual'
  } else if (state.videoInputEnumerationClass === 'video_inputs_enumerated') {
    statusKey = selectedKey && !selectedMatch
      ? 'launch.cameraSelectionMissing'
      : 'launch.cameraSelectionAvailable'
    statusValues = { count: devices.length }
  } else if (state.videoInputEnumerationClass === 'video_inputs_none') {
    statusKey = selectedKey ? 'launch.cameraSelectionMissing' : 'launch.cameraSelectionNone'
  } else if (state.videoInputEnumerationClass !== 'pending') {
    statusKey = 'launch.cameraSelectionUnavailable'
  }
  $('camera-selection-state').textContent = t(statusKey, statusValues)
}

const refreshVideoInputDevices = async () => {
  state.videoInputEnumerationClass = 'pending'
  renderCameraSelector()
  try {
    const payload = await api('/api/video-input-devices')
    state.videoInputDevices = Array.isArray(payload.devices) ? payload.devices : []
    state.videoInputEnumerationClass = payload.result_class || 'video_input_enumeration_unavailable'
    state.videoInputSelectionClass = payload.selection_class || 'no_selection'
  } catch {
    state.videoInputDevices = []
    state.videoInputEnumerationClass = 'video_input_enumeration_unavailable'
    state.videoInputSelectionClass = 'no_selection'
  }
  renderCameraSelector()
}

const formatReviewCommandPreview = (commandLine) => String(commandLine || '').trim()

const setCommandPreview = (commandLine) => {
  $('command-preview').textContent = formatReviewCommandPreview(commandLine)
}

const plainLogText = (logText) => String(logText || '').trimEnd()

const launcherLogLevel = (line) => {
  const text = line.toLowerCase()
  if (/\b(error|failed|failure|exception|fatal|denied|timeout)\b/.test(text)) return 'error'
  if (/\b(warn|warning|degraded|retry|waiting|skipped|not running)\b/.test(text)) return 'warn'
  if (/\b(ok|ready|online|started|finished|completed|listening|accepted)\b/.test(text)) return 'ok'
  return 'info'
}

const launcherLogSourceFromText = (line) => {
  const text = line.toLowerCase()
  if (text.includes('voicevox')) return 'voicevox'
  if (text.includes('thought core')) return 'thought-core'
  if (text.includes('environment')) return 'environment'
  if (text.includes('home assistant') || text.includes('action bridge')) return 'action-bridge'
  if (text.includes('mediapipe') || text.includes('camera')) return 'camera'
  if (text.includes('vision')) return 'vision'
  if (text.includes('touchdesigner') || text.includes('display runtime')) return 'display'
  if (text.includes('aituber') || text.includes('expression')) return 'expression'
  return 'stack'
}

const parseLauncherLogLine = (line) => {
  const raw = String(line || '')
  const bracketMatch = raw.match(/^\[([^\]]+)]\s*(.*)$/)
  const timestampMatch = raw.match(/^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(.*)$/)
  const separatorMatch = raw.match(/^=+\s*(.*?)\s*=+$/)
  let source = ''
  let message = raw
  let time = ''

  if (bracketMatch) {
    source = bracketMatch[1]
    message = bracketMatch[2] || raw
  } else if (timestampMatch) {
    time = timestampMatch[1]
    message = timestampMatch[2] || raw
  } else if (separatorMatch) {
    source = 'launcher'
    message = separatorMatch[1] || raw
  }

  const normalizedSource = String(source || '').trim()
  const displaySource = normalizedSource
    ? normalizedSource.split(/[:/\s]+/).filter(Boolean).slice(0, 2).join(':')
    : launcherLogSourceFromText(raw)

  return {
    raw,
    time: time ? formatTimestamp(time) : '',
    source: displaySource || 'stack',
    level: launcherLogLevel(raw),
    message: message.trim() || raw
  }
}

const renderLauncherLog = (logText) => {
  const rawLog = plainLogText(logText)
  const fallback = t('status.noLog')
  state.latestLogTailRaw = rawLog
  const lines = (rawLog || fallback).split(/\r?\n/)
  $('log-output').innerHTML = lines
    .map((line) => {
      const entry = parseLauncherLogLine(line)
      const time = entry.time ? `<span class="log-entry-time">${escapeHtml(entry.time)}</span>` : ''
      return `
        <div class="log-entry" data-log-level="${escapeHtml(entry.level)}">
          <span class="log-entry-meta">
            ${time}
            <span class="log-entry-source">${escapeHtml(entry.source)}</span>
            <span class="log-entry-level">${escapeHtml(entry.level)}</span>
          </span>
          <span class="log-entry-message">${escapeHtml(entry.message)}</span>
        </div>
      `
    })
    .join('')
}

const prependLauncherLog = (message) => {
  const raw = plainLogText(state.latestLogTailRaw)
  renderLauncherLog(`${message}${raw ? `\n\n${raw}` : ''}`)
}

const applyProfileDefaults = async () => {
  const preview = await api('/api/preview', {
    method: 'POST',
    body: JSON.stringify({
      profileId: state.selectedProfileId,
      options: profileOptions()
    })
  })
  applyPreviewOptions(preview.options)
  setCommandPreview(preview.commandLine)
  renderControls()
  renderSystemSummary()
}

const setOption = (key, value) => {
  state.options[key] = value
  if (key === 'MediapipeOpenBrowser' && value) {
    state.options.MediapipeNoBrowser = false
  }
  if (key === 'MediapipeNoBrowser' && value) {
    state.options.MediapipeOpenBrowser = false
  }
  renderControls()
  refreshPreview()
}

const setReadyTimeoutOption = (field, value) => {
  if (!readyTimeoutOptionFields.includes(field)) {
    return
  }
  const normalized = Math.max(1, Math.round(Number(value) || 0))
  state.options[field] = normalized
  refreshPreview()
  renderStartupTiming(state.startupTiming)
}

const isLaunchServiceEnabled = (field) => {
  if (field === 'StopExisting') {
    return null
  }
  if (field.startsWith('Skip')) {
    return !state.options[field]
  }
  return Boolean(state.options[field])
}

const launchScopeItemIsEnabled = (item) =>
  item.enabledWhen === 'falsy' ? !state.options[item.field] : Boolean(state.options[item.field])

const summarizeLaunchScope = () => {
  const summary = {
    enabled: [],
    skipped: []
  }
  for (const item of launchScopeItems) {
    if (launchScopeItemIsEnabled(item)) {
      summary.enabled.push(localizedLaunchScopeLabel(item))
    } else {
      summary.skipped.push(localizedLaunchScopeLabel(item))
    }
  }
  return summary
}

const summarizeLaunchServices = () => {
  const enabled = launchScopeItems.filter((item) => launchScopeItemIsEnabled(item)).length
  const total = launchScopeItems.length
  return {
    card: `${enabled}/${total} ${t('summary.enabled')}`,
    drawer: `${enabled}/${total} ${t('summary.servicesNoun')}`
  }
}

const summarizeDiagnostics = () => {
  const enabled = diagnosticSwitchFields.filter((field) => state.options[field]).length
  return {
    card: enabled ? `${enabled} ${t('summary.enabled')}` : t('summary.off'),
    drawer: enabled ? `${enabled} ${t('summary.togglesOn')}` : t('summary.noTestToggles')
  }
}

const summarizeRuntime = () => {
  const fallbackOnly = Boolean(state.options.ThoughtCoreNoProvider)
  const providerLabels = {
    configured: t('provider.configured'),
    'openai-compatible': t('provider.openai'),
    'codex-cli': t('provider.codex'),
    'codex-cli-luna': t('provider.codexLuna')
  }
  return {
    card: fallbackOnly
      ? t('summary.fallbackOnly')
      : providerLabels[state.options.ThoughtCoreLlmProvider] || t('summary.providerAllowed')
  }
}

const summarizePorts = () => {
  const values = portFields.map((field) => String(state.options[field] || '').trim()).filter(Boolean)
  const duplicates = values.filter((value, index) => values.indexOf(value) !== index)
  return {
    card: duplicates.length ? t('summary.checkConflict') : `${values.length}/${portFields.length} ${t('summary.set')}`,
    drawer: duplicates.length ? t('summary.duplicatePorts') : t('summary.coreBindings')
  }
}

const demoSafeRows = () => state.demoSafeSettings?.rows || []

const demoReadinessById = () =>
  new Map((state.demoReadinessStatus?.rows || []).map((row) => [row.id, row]))

const summarizeDemoSafe = () => {
  const summary = state.demoSafeSettings?.summary || {}
  const total = Number(summary.total) || demoSafeRows().length
  const enabled = Number(summary.enabled) || demoSafeRows().filter((row) => row.enabled).length
  return {
    card: enabled > 0 ? `${enabled}/${total} ${t('summary.enabled')}` : t('demoSafe.noneEnabled'),
    drawer: total > 0
      ? t('demoSafe.drawerSummary', { enabled, total })
      : t('demoSafe.defaultOff')
  }
}

const renderLaunchSummary = () => {
  const scope = summarizeLaunchScope()
  const services = summarizeLaunchServices()
  const diagnostics = summarizeDiagnostics()
  const runtime = summarizeRuntime()
  const ports = summarizePorts()
  const demoSafe = summarizeDemoSafe()
  $('services-summary').textContent = services.card
  $('launch-scope-enabled').textContent = t('launchScope.enabled', {
    value: scope.enabled.join(', ') || t('value.none')
  })
  $('launch-scope-skipped').textContent = t('launchScope.skipped', {
    value: scope.skipped.join(', ') || t('value.none')
  })
  $('services-drawer-summary').textContent = services.drawer
  $('diagnostics-summary').textContent = diagnostics.card
  $('diagnostics-drawer-summary').textContent = diagnostics.drawer
  $('runtime-summary').textContent = runtime.card
  $('ports-summary').textContent = ports.card
  $('ports-drawer-summary').textContent = ports.drawer
  $('demo-safe-summary').textContent = demoSafe.card
  $('demo-safe-drawer-summary').textContent = demoSafe.drawer
}

const renderControls = () => {
  const profileSelect = $('profile-select')
  const profilesByGroup = groupProfiles(visibleProfilesForSelect(state.profiles))
  profileSelect.innerHTML = profilesByGroup
    .map(([group, profiles]) => {
      const options = profiles
        .map(
          (profile) =>
            `<option value="${profile.id}">${escapeHtml(profile.name)}</option>`
        )
        .join('')
      return `<optgroup label="${escapeHtml(group)}">${options}</optgroup>`
    })
    .join('')
  profileSelect.value = state.selectedProfileId
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  $('profile-description').textContent = profile ? profile.description : ''
  $('active-profile-name').textContent = profile ? profile.name : state.selectedProfileId
  $('active-profile-detail').textContent = state.options.MediapipeMode
    ? `MediaPipe: ${state.options.MediapipeMode}`
    : t('metric.profilePending')
  renderLaunchSummary()

  for (const field of numericOptionFields) {
    const input = $(field)
    input.value = state.options[field] || ''
  }
  for (const field of textFields) {
    $(field).value = state.options[field] || ''
  }
  renderCameraSelector()
  $('ThoughtCoreLlmProvider').value = state.options.ThoughtCoreLlmProvider || 'configured'

  document.querySelectorAll('#mediapipe-mode button').forEach((button) => {
    button.classList.toggle('active', button.dataset.value === state.options.MediapipeMode)
  })

  renderSwitchGroup('core-switch-grid', coreSwitchFields)
  renderSwitchGroup('diagnostic-switch-grid', diagnosticSwitchFields)
  renderDemoSafeSettings()
}

const groupProfiles = (profiles) => {
  const groups = new Map()
  for (const profile of profiles || []) {
    const group = profile.group || 'Other'
    if (!groups.has(group)) {
      groups.set(group, [])
    }
    groups.get(group).push(profile)
  }
  return Array.from(groups.entries())
}

const visibleProfilesForSelect = (profiles) =>
  (profiles || []).filter((profile) => !profile.hidden || profile.id === state.selectedProfileId)

const displaySwitchValue = (field) => {
  if (positiveDisplayFields.has(field)) {
    return !state.options[field]
  }
  return field.startsWith('Skip') ? !state.options[field] : Boolean(state.options[field])
}

const setSwitchValue = (field, checked) => {
  if (positiveDisplayFields.has(field)) {
    setOption(field, !checked)
    return
  }
  let value = field.startsWith('Skip') ? !checked : checked
  if (field === 'SkipMediapipe' && value) {
    state.options.SkipVisionSnapshotProcessor = true
  }
  if (field === 'SkipVisionSnapshotProcessor' && state.options.SkipMediapipe) {
    value = true
  }
  setOption(field, value)
}

const renderSwitchGroup = (elementId, fields) => {
  const switchGrid = $(elementId)
  switchGrid.innerHTML = fields
    .map(
      (field) => {
        const description = localizedSwitchDescription(field)
          ? `<small class="switch-description">${escapeHtml(localizedSwitchDescription(field))}</small>`
          : ''
        return `
        <label class="switch-row">
          <span class="switch-copy">
            <strong>${labelFor(field)}</strong>
            ${description}
          </span>
          <input type="checkbox" data-switch="${field}" ${displaySwitchValue(field) ? 'checked' : ''} />
        </label>
      `
      }
    )
    .join('')

  switchGrid.querySelectorAll('[data-switch]').forEach((input) => {
    input.addEventListener('change', (event) => {
      setSwitchValue(event.target.dataset.switch, event.target.checked)
    })
  })
}

const demoSafeAreaLabel = (area) => t(`demoSafe.${area}`) === `demoSafe.${area}`
  ? labelFor(area)
  : t(`demoSafe.${area}`)

const readinessDisplayClass = (statusClass) =>
  String(statusClass || 'not_checked_class').replace(/_/g, '-')

const renderDemoSafeSettings = () => {
  const container = $('demo-safe-settings-list')
  const readiness = demoReadinessById()
  const rows = demoSafeRows()
  if (rows.length === 0) {
    container.innerHTML = `<p class="demo-safe-empty">${escapeHtml(t('demoSafe.defaultOff'))}</p>`
    return
  }
  container.innerHTML = rows
    .map((row) => {
      const status = readiness.get(row.id) || {}
      const doesNotProve = (row.does_not_prove || []).join(', ')
      const actionIds = (row.action_ids || []).join(' -> ')
      const timingEstimate = Number(row.timing_estimate_sec) || 0
      const restoreDisabled = row.restore_supported ? '' : 'disabled'
      return `
        <section class="demo-safe-row" data-demo-safe-row="${escapeHtml(row.id)}">
          <div class="demo-safe-row-header">
            <span>${escapeHtml(demoSafeAreaLabel(row.area || 'general'))}</span>
            <strong>${escapeHtml(row.label || row.id)}</strong>
          </div>
          <p>${escapeHtml(row.description || '')}</p>
          <div class="demo-safe-controls">
            <label class="demo-safe-toggle">
              <input
                type="checkbox"
                data-demo-safe-id="${escapeHtml(row.id)}"
                data-demo-safe-field="enabled"
                ${row.enabled ? 'checked' : ''}
              />
              <span>${escapeHtml(t('demoSafe.enabled'))}</span>
            </label>
            <label class="demo-safe-toggle ${row.restore_supported ? '' : 'disabled'}">
              <input
                type="checkbox"
                data-demo-safe-id="${escapeHtml(row.id)}"
                data-demo-safe-field="restore_required"
                ${row.restore_required ? 'checked' : ''}
                ${restoreDisabled}
              />
              <span>${escapeHtml(row.restore_supported ? t('demoSafe.restoreRequired') : t('demoSafe.notApplicable'))}</span>
            </label>
            <label class="demo-safe-limit">
              <span>${escapeHtml(t('demoSafe.maxActions'))}</span>
              <input
                type="number"
                min="0"
                max="25"
                data-demo-safe-id="${escapeHtml(row.id)}"
                data-demo-safe-field="max_action_count"
                value="${Number(row.max_action_count) || 0}"
              />
            </label>
            <label class="demo-safe-limit">
              <span>${escapeHtml(t('demoSafe.maxDuration'))}</span>
              <input
                type="number"
                min="0"
                max="3600"
                data-demo-safe-id="${escapeHtml(row.id)}"
                data-demo-safe-field="max_duration_sec"
                value="${Number(row.max_duration_sec) || 0}"
              />
            </label>
          </div>
          <div class="demo-safe-readiness ${escapeHtml(readinessDisplayClass(status.status_class))}">
            <span>${escapeHtml(t('demoSafe.readiness'))}</span>
            <strong>${escapeHtml(status.status_class || 'not_checked_class')}</strong>
          </div>
          ${actionIds || timingEstimate ? `
            <dl class="demo-safe-route">
              ${actionIds ? `
                <div>
                  <dt>${escapeHtml(t('demoSafe.actions'))}</dt>
                  <dd>${escapeHtml(actionIds)}</dd>
                </div>
              ` : ''}
              ${timingEstimate ? `
                <div>
                  <dt>${escapeHtml(t('demoSafe.timing'))}</dt>
                  <dd>${escapeHtml(String(timingEstimate))}s</dd>
                </div>
              ` : ''}
              <div>
                <dt>${escapeHtml(t('demoSafe.stimulus'))}</dt>
                <dd>${escapeHtml(row.feedback_stimulus_class || 'not_applicable')}</dd>
              </div>
            </dl>
          ` : ''}
          <small class="demo-safe-proof">
            ${escapeHtml(row.proof_ceiling || 'source_static_readiness')}
            ${doesNotProve ? ` / ${escapeHtml(doesNotProve)}` : ''}
          </small>
        </section>
      `
    })
    .join('')

  container.querySelectorAll('[data-demo-safe-id]').forEach((input) => {
    input.addEventListener('change', (event) => {
      const element = event.target
      const field = element.dataset.demoSafeField
      const value = element.type === 'checkbox' ? element.checked : Number(element.value)
      setDemoSafeField(element.dataset.demoSafeId, field, value)
    })
  })
}

const setDemoSafeField = (id, field, value) => {
  const row = demoSafeRows().find((item) => item.id === id)
  if (!row) {
    return
  }
  if (field === 'enabled') {
    row.enabled = Boolean(value)
  } else if (field === 'restore_required') {
    row.restore_required = row.restore_supported ? Boolean(value) : false
  } else if (field === 'max_action_count') {
    row.max_action_count = Math.max(0, Math.min(25, Number(value) || 0))
  } else if (field === 'max_duration_sec') {
    row.max_duration_sec = Math.max(0, Math.min(3600, Number(value) || 0))
  }
  const enabled = demoSafeRows().filter((item) => item.enabled)
  state.demoSafeSettings.summary = {
    ...(state.demoSafeSettings.summary || {}),
    total: demoSafeRows().length,
    enabled: enabled.length,
    enabled_appliance: enabled.filter((item) => item.area === 'appliance').length,
    enabled_readiness: enabled.filter((item) => item.area !== 'appliance').length
  }
  renderLaunchSummary()
  renderDemoSafeSettings()
}

const currentDemoSafeSettings = () => ({
  rows: demoSafeRows().map((row) => ({
    id: row.id,
    enabled: Boolean(row.enabled),
    restore_required: Boolean(row.restore_required),
    max_action_count: Number(row.max_action_count) || 0,
    max_duration_sec: Number(row.max_duration_sec) || 0
  }))
})

const labelFor = (value) =>
  localizedFieldLabel(value) ||
  value
    .replace(/^Skip/, 'Skip ')
    .replace(/^Stop/, 'Stop ')
    .replace(/^Enable/, 'Enable ')
    .replace(/^Mediapipe/, 'MediaPipe ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')

const escapeHtml = (value) =>
  String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

const stateClass = (serviceState) =>
  `state-${String(serviceState || 'down').toLowerCase().replace(/_/g, '-')}`

const serviceDisplayName = (name) => localizedServiceLabel(name) || labelFor(name)

const serviceRole = (name) => localizedServiceRole(name) || t('service.systemCell')

const serviceStateShort = (serviceState) => {
  const value = String(serviceState || 'DOWN').toUpperCase()
  if (value === 'OK_EXTERNAL') return 'EXT'
  if (value === 'DEGRADED') return 'DEG'
  if (value === 'STARTING') return 'WAIT'
  return value
}

const formatElapsed = (ms) => {
  const value = Number(ms)
  if (!Number.isFinite(value) || value < 0) {
    return '-'
  }
  if (value < 1000) {
    return `${Math.round(value)} ms`
  }
  return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`
}

const formatReadyTimeout = (item) => {
  const value = Number(item?.readyTimeoutMs)
  if (Number.isFinite(value) && value > 0) {
    return formatElapsed(value)
  }
  return t('startup.maxWaitUnset')
}

const startupTimingTimeoutEditIsActive = () => {
  const active = document.activeElement
  return Boolean(active && active.dataset && active.dataset.readyTimeoutField)
}

const readyTimeoutInputValue = (serviceId, item) => {
  const field = readyTimeoutFieldForService(serviceId)
  const optionValue = Number(state.options[field])
  if (field && Number.isFinite(optionValue) && optionValue > 0) {
    return String(optionValue)
  }
  const msValue = Number(item?.readyTimeoutMs)
  if (Number.isFinite(msValue) && msValue > 0) {
    return String(Math.round(msValue / 1000))
  }
  return ''
}

const renderReadyTimeoutCell = (serviceId, item) => {
  const field = readyTimeoutFieldForService(serviceId)
  if (!field) {
    return `<span class="startup-timeout-static">${escapeHtml(formatReadyTimeout(item))}</span>`
  }
  const serviceName = serviceDisplayName(serviceId)
  return `
    <label class="startup-timeout-input-shell">
      <span class="sr-only">${escapeHtml(t('startup.editMaxWait', { name: serviceName }))}</span>
      <input
        class="startup-timeout-input"
        data-ready-timeout-field="${escapeHtml(field)}"
        type="number"
        min="1"
        step="1"
        inputmode="numeric"
        value="${escapeHtml(readyTimeoutInputValue(serviceId, item))}"
        aria-label="${escapeHtml(t('startup.editMaxWait', { name: serviceName }))}"
      />
      <span>${escapeHtml(t('startup.secondsUnit'))}</span>
    </label>
  `
}

// launcher_startup_timing.v1 を、表示に必要な最小状態へ変換する。
// lifecycle の正本は Node supervisor のままで、この関数は判定を追加しない。
const projectStartupTimingView = (timing) => {
  if (
    !timing ||
    timing.schema_version !== 'launcher_startup_timing.v1' ||
    !Array.isArray(timing.expectedServiceIds) ||
    !Array.isArray(timing.readyServiceIds)
  ) {
    return null
  }
  const expectedServiceIds = timing.expectedServiceIds.map((value) => String(value || '').trim())
  const readyServiceIds = timing.readyServiceIds.map((value) => String(value || '').trim())
  if (
    expectedServiceIds.some((value) => !value) ||
    readyServiceIds.some((value) => !value) ||
    new Set(expectedServiceIds).size !== expectedServiceIds.length ||
    new Set(readyServiceIds).size !== readyServiceIds.length
  ) {
    return null
  }
  const expected = new Set(expectedServiceIds)
  if (readyServiceIds.some((serviceId) => !expected.has(serviceId))) {
    return null
  }
  const ready = new Set(readyServiceIds)
  const rows = expectedServiceIds.map((serviceId) => Object.freeze({
    serviceId,
    ready: ready.has(serviceId)
  }))
  const allExpectedReady = rows.every((row) => row.ready)
  return Object.freeze({
    expectedCount: expectedServiceIds.length,
    readyCount: rows.filter((row) => row.ready).length,
    operational: timing.operational === true && allExpectedReady,
    statusClass: String(timing.status_class || 'unknown'),
    rows: Object.freeze(rows)
  })
}

const renderStartupTiming = (timing) => {
  const container = $('startup-timing-list')
  if (!container) {
    return
  }
  if (startupTimingTimeoutEditIsActive()) {
    return
  }
  const view = projectStartupTimingView(timing)
  if (!view) {
    container.innerHTML = `<div class="diagnostic-row"><strong>${escapeHtml(t('startup.noTiming'))}</strong></div>`
    return
  }
  const rows = view.rows.map((row) => {
    const stateLabel = row.ready ? t('startup.ready') : t('startup.waiting')
    return `
      <div class="startup-timing-row" data-state-group="${row.ready ? 'ok' : 'warn'}">
        <span class="startup-service">${escapeHtml(serviceDisplayName(row.serviceId))}</span>
        <span class="startup-state">${escapeHtml(stateLabel)}</span>
        <span class="startup-elapsed">-</span>
        <span class="startup-timeout">${renderReadyTimeoutCell(row.serviceId, {})}</span>
      </div>
    `
  }).join('')
  container.innerHTML = `
    <div class="diagnostic-summary-grid">
      <div><span>${escapeHtml(t('startup.operational'))}</span><strong>${escapeHtml(view.operational ? t('startup.ready') : t('startup.waiting'))}</strong></div>
      <div><span>${escapeHtml(t('startup.ready'))}</span><strong>${escapeHtml(String(view.readyCount))}/${escapeHtml(String(view.expectedCount))}</strong></div>
      <div><span>${escapeHtml(t('startup.state'))}</span><strong>${escapeHtml(view.statusClass)}</strong></div>
    </div>
    <div class="startup-timing-table" role="table" aria-label="${escapeHtml(t('startup.title'))}">
      <div class="startup-timing-header" role="row">
        <span>${escapeHtml(t('startup.service'))}</span>
        <span>${escapeHtml(t('startup.state'))}</span>
        <span>${escapeHtml(t('startup.actual'))}</span>
        <span>${escapeHtml(t('startup.maxWait'))}</span>
      </div>
      ${rows}
    </div>
  `
}

const renderDiagnosticSurfaces = (summary) => {
  const container = $('diagnostic-surface-list')
  if (!container) {
    return
  }
  const surfaces = Array.isArray(summary?.surfaces) ? summary.surfaces : []
  if (!surfaces.length) {
    container.innerHTML = `<div class="diagnostic-row"><strong>${escapeHtml(t('diagnosticSurfaces.noLive'))}</strong></div>`
    return
  }
  const counts = summary.counts || {}
  const rows = surfaces.map((surface) => {
    const ready = surface.status_class === 'source_static_ready_class'
    const stateGroup = ready ? 'ok' : 'warn'
    const status = ready ? t('diagnosticSurfaces.ready') : t('diagnosticSurfaces.hold')
    return `
      <div class="diagnostic-row" data-state-group="${stateGroup}">
        <span class="diagnostic-name">${escapeHtml(surface.label || surface.id)}</span>
        <span class="diagnostic-detail">${escapeHtml(status)} · ${escapeHtml(surface.temporal_analysis_class || '-')}</span>
        <span class="diagnostic-subdetail">${escapeHtml(t('diagnosticSurfaces.nextRoute'))}: ${escapeHtml(surface.next_route_class || '-')}</span>
      </div>
    `
  }).join('')
  container.innerHTML = `
    <div class="diagnostic-summary-grid">
      <div><span>${escapeHtml(t('diagnosticSurfaces.ready'))}</span><strong>${escapeHtml(String(counts.ready || 0))}/${escapeHtml(String(counts.total || surfaces.length))}</strong></div>
      <div><span>${escapeHtml(t('diagnosticSurfaces.hold'))}</span><strong>${escapeHtml(String(counts.hold || 0))}</strong></div>
      <div><span>${escapeHtml(t('diagnosticSurfaces.noLive'))}</span><strong>0</strong></div>
    </div>
    ${rows}
  `
}

// Quick Linksの表示専用翻訳。canonical URLと有効条件は
// server側 launcher-surface-catalog.js が所有する。
const endpointDisplayName = (name) => {
  const labels = {
    'AITuber Kit': 'Expression runtime',
    'Expression runtime': 'Operator',
    'Body map inspector': 'Diagnostics body map',
    'Display control GUI/API': 'Display runtime GUI/API',
    'Display runtime GUI/API': 'Display',
    'Action bridge operator': 'Action operator',
    'Home Assistant bridge health': 'Action bridge health',
    'Action bridge health': 'Action',
    'MediaPipe Browser Monitor': 'Reflex browser monitor',
    'Reflex browser monitor': 'Camera Hub',
    'MediaMTX video': 'Reflex camera video',
    'Reflex camera video': 'Video',
    'MediaPipe Camera Hub WebSocket': 'Reflex Camera Hub WebSocket',
    'Reflex Camera Hub WebSocket': 'Camera WS',
    'Vision Snapshot Processor WebSocket': 'Vision snapshot WebSocket',
    'Vision snapshot WebSocket': 'Vision WS',
    'TouchDesigner UDP receiver': 'Display UDP receiver',
    'Display UDP receiver': 'TD UDP',
    'Projection Visual': 'Operator stage',
    'Projection Stage Output': 'Stage output',
    'Projection Effect Diagnostic': 'Effect diagnostic',
    'Passive Projection': 'Stage',
    'Thought Core API index': 'Core API',
    'Thought Core health': 'Core health',
    'Environment display state': 'Env state',
    'Environment indicators': 'Env indicators',
    'VOICEVOX': 'Speech',
    'Thought Core watcher': 'Core watch'
  }
  const labelsJa = {
    'AITuber Kit': '表情表示',
    'Expression runtime': '操作画面',
    'Body map inspector': '自己状態マップ',
    'Display control GUI/API': '投影表示GUI/API',
    'Display runtime GUI/API': '表示',
    'Action bridge operator': '家電操作面',
    'Home Assistant bridge health': '操作ブリッジ状態',
    'Action bridge health': '操作',
    'MediaPipe Browser Monitor': 'カメラ反射入力の確認画面',
    'Reflex browser monitor': 'Camera Hub',
    'MediaMTX video': '反射カメラ映像',
    'Reflex camera video': '映像',
    'MediaPipe Camera Hub WebSocket': '反射Camera Hub WebSocket',
    'Reflex Camera Hub WebSocket': 'Camera WS',
    'Vision Snapshot Processor WebSocket': '視覚状態取得WebSocket',
    'Vision snapshot WebSocket': '視覚状態WS',
    'TouchDesigner UDP receiver': '表示UDP受信',
    'Display UDP receiver': 'TD UDP',
    'Projection Visual': '操作ステージ',
    'Projection Stage Output': '投影出力',
    'Projection Effect Diagnostic': '映像効果診断',
    'Passive Projection': 'ステージ',
    'Thought Core API index': '思考中枢API',
    'Thought Core health': '思考中枢の状態',
    'Environment display state': '環境状態',
    'Environment indicators': '環境指標',
    'VOICEVOX': '音声',
    'Thought Core watcher': '思考中枢の監視'
  }
  if (state.language === 'ja') {
    return labelsJa[name] || labels[name] || name
  }
  return labels[name] || name
}

const endpointTargetLabel = (endpoint, kind, canOpen) => {
  if (!endpoint.enabled) return t('endpoint.skipped')
  if (!canOpen) {
    if (kind === 'websocket') return t('endpoint.websocketReference')
    if (kind === 'background') return t('endpoint.backgroundReference')
    return t('endpoint.reference')
  }
  if (kind === 'stage') return t('endpoint.stageView')
  if (kind === 'diagnostic') return t('endpoint.diagnostic')
  if (endpoint.name === 'Projection Visual') return t('endpoint.operatorPreview')
  if (kind === 'api' || kind === 'thought') return t('endpoint.localApi')
  if (kind === 'camera') return t('endpoint.cameraFeed')
  if (kind === 'display') return t('endpoint.displayRuntime')
  if (kind === 'speech') return t('endpoint.speechRuntime')
  return t('endpoint.openBrowser')
}

const serviceIsIncluded = (name) => {
  const enableFields = enableFieldsByService[name] || []
  if (enableFields.length > 0) {
    return enableFields.some((field) => state.options[field])
  }
  const skipFields = skipFieldsByService[name] || []
  return !skipFields.some((field) => state.options[field])
}

const serviceStartupTargetFields = (name) => startupTargetFieldsByService[name] || []

const serviceStartupTargetBlockers = (name) =>
  (startupTargetDependenciesByService[name] || [])
    .filter((dependency) => !displaySwitchValue(dependency.field))
    .map((dependency) => (state.language === 'ja' && dependency.labelJa ? dependency.labelJa : dependency.label))

const serviceStateGroup = (serviceState) => {
  const value = String(serviceState || 'DOWN').toUpperCase()
  if (value === 'OK' || value === 'OK_EXTERNAL') {
    return 'ok'
  }
  if (value === 'DEGRADED' || value === 'STARTING') {
    return 'warn'
  }
  return 'down'
}

const serviceIsBooting = (service, included) =>
  state.operation === 'starting' &&
  included &&
  serviceStateGroup(service?.state) !== 'ok'

const setServiceStartupTarget = (name, checked) => {
  const fields = serviceStartupTargetFields(name)
  for (const field of fields) {
    setSwitchValue(field, checked)
  }
  renderControls()
  renderLaunchSummary()
  renderServices(state.latestServices || {})
}

const formatTimestamp = (value) => {
  if (!value) {
    return t('status.awaiting')
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(state.language === 'ja' ? 'ja-JP' : undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(date)
}

const summarizeServices = (services = {}) => {
  const entries = Object.entries(services).filter(
    ([name]) => !hiddenServiceKeys.has(name) && serviceIsIncluded(name)
  )
  const summary = {
    total: entries.length,
    online: 0,
    warn: 0,
    down: 0
  }
  for (const [, service] of entries) {
    const group = serviceStateGroup(service.state)
    if (group === 'ok') {
      summary.online += 1
    } else if (group === 'warn') {
      summary.warn += 1
    } else {
      summary.down += 1
    }
  }
  return summary
}

const setOperationProgressFromSummary = (summary, mode) => {
  if (!summary || summary.total <= 0) {
    state.operationProgress = {
      percent: 8,
      label: mode === 'stopping' ? t('progress.stopping') : t('progress.checking'),
      visible: true
    }
    renderOperation()
    return
  }
  const count = mode === 'stopping'
    ? Math.max(0, summary.total - summary.online)
    : summary.online
  const remaining = mode === 'stopping'
    ? summary.online
    : Math.max(0, summary.total - summary.online)
  const percent = Math.round((count / summary.total) * 100)
  const noun = remaining === 1 ? t('progress.service') : t('progress.services')
  state.operationProgress = {
    percent,
    label:
      remaining === 0
        ? `${percent}%`
        : t('progress.remaining', { percent, remaining, noun }),
    visible: true
  }
  renderOperation()
}

const formatStopVerificationDetail = (verification) => {
  if (!verification) {
    return t('stop.noVerification')
  }
  if (verification.ok) {
    return t('stop.verified', { count: verification.checkedPortCount || 0 })
  }
  const issues = []
  if (verification.pidFileExists) {
    issues.push(t('stop.pidRegistry'))
  }
  if (verification.aliveRecorded?.length) {
    issues.push(
      t('stop.alivePids', {
        value: verification.aliveRecorded
          .map((entry) => `${entry.name}#${entry.pid}`)
          .join(', ')
      })
    )
  }
  if (verification.openPorts?.length) {
    issues.push(
      t('stop.openPorts', {
        value: verification.openPorts
          .map((entry) => `${entry.label}:${entry.port}`)
          .join(', ')
      })
    )
  }
  if (verification.timedOut) {
    issues.push(t('stop.timedOut'))
  }
  return issues.length > 0
    ? t('stop.incomplete', { issues: issues.join('; ') })
    : t('stop.incompleteGeneric')
}

const renderSystemSummary = (services = null, timestamp = '') => {
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  $('active-profile-name').textContent = profile ? profile.name : state.selectedProfileId
  $('active-profile-detail').textContent = state.options.MediapipeMode
    ? `MediaPipe: ${state.options.MediapipeMode}`
    : t('metric.profilePending')

  if (!services) {
    return
  }

  const summary = summarizeServices(services)
  if (state.operation === 'starting') {
    setOperationProgressFromSummary(summary, 'starting')
    const detail =
      summary.total > 0
        ? t('status.startWatching', { online: summary.online, total: summary.total })
        : t('status.startAccepted')
    if (summary.total > 0 && summary.online === summary.total) {
      setOperation('started', t('status.allOnline', { time: formatTimestamp(timestamp) }))
    } else {
      setOperation('starting', detail)
    }
  } else if (state.operation === 'stopping') {
    setOperationProgressFromSummary(summary, 'stopping')
  }

  const attention = summary.warn + summary.down
  const readinessCard = $('readiness-card')
  readinessCard.classList.remove('ready', 'warn', 'down')

  let readinessLabel = t('readiness.ready')
  let readinessClass = 'ready'
  if (state.operation === 'starting') {
    readinessLabel = t('readiness.starting')
    readinessClass = 'warn'
  } else if (state.operation === 'stopping') {
    readinessLabel = t('readiness.stopping')
    readinessClass = 'warn'
  } else if (state.operation === 'stopped') {
    readinessLabel = t('readiness.stopped')
    readinessClass = 'warn'
  } else if (summary.total === 0) {
    readinessLabel = t('readiness.manual')
    readinessClass = 'warn'
  } else if (summary.down > 0) {
    readinessLabel = t('readiness.checkStack')
    readinessClass = 'down'
  } else if (summary.warn > 0) {
    readinessLabel = t('readiness.warmingUp')
    readinessClass = 'warn'
  }
  readinessCard.classList.add(readinessClass)
  $('readiness-label').textContent = readinessLabel
  $('readiness-detail').textContent =
    state.operation === 'starting' ||
    state.operation === 'stopping' ||
    state.operation === 'stopped'
      ? state.operationDetail
      : t('status.expectedOnline', { online: summary.online, total: summary.total })
  $('online-count').textContent = `${summary.online}/${summary.total}`
  $('online-detail').textContent = t('status.updated', { time: formatTimestamp(timestamp) })
  $('attention-count').textContent = String(attention)
  $('attention-detail').textContent =
    attention === 0 ? t('status.nominal') : t('status.attention', { warn: summary.warn, down: summary.down })
}

const renderServices = (services) => {
  state.latestServices = services || {}
  const names = Object.keys(services || {}).filter((name) => !hiddenServiceKeys.has(name))
  $('service-list').innerHTML = names
    .reduce(
      (markup, name) => {
        const service = services[name]
        const included = serviceIsIncluded(name)
        const targetFields = serviceStartupTargetFields(name)
        const targetBlockers = serviceStartupTargetBlockers(name)
        const group = serviceStateGroup(service.state)
        const isBooting = serviceIsBooting(service, included)
        const rowClass = included ? '' : ' service-skipped'
        const targetControl =
          targetBlockers.length > 0
            ? `
              <span
                class="service-target-badge"
                title="${escapeHtml(t('service.requires', { targets: targetBlockers.join(', ') }))}"
              >
                ${escapeHtml(t('service.blocked'))}
              </span>
            `
            : targetFields.length > 0
            ? `
              <label class="service-target-toggle" title="${escapeHtml(t('service.targetTitle'))}">
                <input
                  type="checkbox"
                  data-service-startup-target="${escapeHtml(name)}"
                  ${included ? 'checked' : ''}
                  aria-label="${escapeHtml(t('service.includeAria', { name: serviceDisplayName(name) }))}"
                />
                <span>${included ? escapeHtml(t('service.target')) : escapeHtml(t('service.skip'))}</span>
              </label>
            `
            : `<span class="service-target-badge">${escapeHtml(t('service.fixed'))}</span>`
        return `${markup}
          <div
            class="service-row${rowClass}"
            data-state-group="${group}"
            data-booting="${isBooting ? 'true' : 'false'}"
            data-startup-target="${included ? 'included' : 'skipped'}"
            role="row"
          >
            <span class="service-status" role="cell">
              <span class="service-led" aria-hidden="true"></span>
              <span
                class="state-pill ${stateClass(service.state)}"
                title="${escapeHtml(service.state)}"
              >
                ${escapeHtml(serviceStateShort(service.state))}
              </span>
            </span>
            <span class="service-title" role="cell">
              <span class="service-name">${escapeHtml(serviceDisplayName(name))}</span>
              <span class="service-key">
                ${escapeHtml(serviceRole(name))}${included ? '' : ` / ${escapeHtml(t('service.profileOff'))}`}
              </span>
            </span>
            <span class="service-startup-target" role="cell">${targetControl}</span>
            <span class="service-metric" role="cell">${service.pid || '-'}</span>
            <span class="service-metric" role="cell" title="${escapeHtml(service.tcp?.detail || '-')}">
              ${escapeHtml(service.tcp?.detail || '-')}
            </span>
            <span class="service-metric" role="cell" title="${escapeHtml(service.http?.detail || '-')}">
              ${escapeHtml(service.http?.detail || '-')}
            </span>
          </div>
        `
      },
      `
        <div class="service-rack" role="table" aria-label="${escapeHtml(t('service.tableAria'))}">
          <div class="service-rack-header" role="row">
            <span role="columnheader">${escapeHtml(t('service.header.state'))}</span>
            <span role="columnheader">${escapeHtml(t('service.header.organ'))}</span>
            <span role="columnheader">${escapeHtml(t('service.header.target'))}</span>
            <span role="columnheader">PID</span>
            <span role="columnheader">TCP</span>
            <span role="columnheader">HTTP</span>
          </div>
      `
    ) + '</div>'

  $('service-list')
    .querySelectorAll('[data-service-startup-target]')
    .forEach((input) => {
      input.addEventListener('change', (event) => {
        setServiceStartupTarget(event.target.dataset.serviceStartupTarget, event.target.checked)
      })
    })
}

const renderEndpoints = (endpoints) => {
  const groups = new Map()
  for (const endpoint of endpoints || []) {
    if (!groups.has(endpoint.group)) {
      groups.set(endpoint.group, [])
    }
    groups.get(endpoint.group).push(endpoint)
  }
  $('endpoint-list').innerHTML = Array.from(groups.entries())
    .map(([group, items]) => {
      const links = items
        .map((endpoint) => {
          const isUrl = /^https?:|^file:/.test(endpoint.url)
          const canOpen = isUrl && endpoint.enabled
          const attrs = canOpen
            ? `href="${escapeHtml(endpoint.url)}" target="_blank" rel="noreferrer"`
            : 'href="#" aria-disabled="true" tabindex="-1"'
          const status = endpoint.enabled
            ? (canOpen ? t('endpoint.open') : t('endpoint.reference'))
            : t('endpoint.skipped')
          const className = endpoint.enabled ? (canOpen ? '' : 'reference-only') : 'disabled'
          const kind = endpointKind(endpoint)
          const title = endpoint.url
            ? `${endpointDisplayName(endpoint.name)}: ${endpoint.url}`
            : endpointDisplayName(endpoint.name)
          return `
            <a class="endpoint-link ${className}" data-kind="${kind}" title="${escapeHtml(title)}" ${attrs}>
              <span class="endpoint-icon" aria-hidden="true">${endpointIcon(kind)}</span>
              <span class="endpoint-copy">
                <strong>${escapeHtml(endpointDisplayName(endpoint.name))}</strong>
                <span>${escapeHtml(endpointTargetLabel(endpoint, kind, canOpen))}</span>
              </span>
              <em class="endpoint-status">${status}</em>
            </a>
          `
        })
        .join('')
      return `
        <section class="endpoint-group">
          <h3>${escapeHtml(endpointGroupLabel(group))}</h3>
          <div class="endpoint-links">${links}</div>
        </section>
      `
    })
    .join('')
}

const endpointGroupLabel = (group) => {
  const labels = {
    'Open in browser': 'endpoint.group.openBrowser',
    'Local APIs and feeds': 'endpoint.group.localApis',
    'Background links': 'endpoint.group.backgroundLinks'
  }
  return labels[group] ? t(labels[group]) : group
}

// Catalogが選んだ表示分類を、このUIが認識するicon paletteへ安全に写す。
const endpointIcons = Object.freeze({
    ui: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="12" rx="2"></rect><path d="M8 21h8"></path><path d="M12 17v4"></path></svg>',
    api: '<svg viewBox="0 0 24 24"><path d="M7 8l-4 4 4 4"></path><path d="M17 8l4 4-4 4"></path><path d="M14 4l-4 16"></path></svg>',
    websocket: '<svg viewBox="0 0 24 24"><path d="M5 12a7 7 0 0 1 14 0"></path><path d="M8 12a4 4 0 0 1 8 0"></path><path d="M12 12h.01"></path><path d="M12 16v4"></path></svg>',
    thought: '<svg viewBox="0 0 24 24"><path d="M9 18h6"></path><path d="M10 22h4"></path><path d="M8 14a6 6 0 1 1 8 0c-.8.6-1 1.3-1 2H9c0-.7-.2-1.4-1-2z"></path></svg>',
    stage: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"></rect><path d="M8 9h8"></path><path d="M8 13h5"></path></svg>',
    diagnostic: '<svg viewBox="0 0 24 24"><path d="M9 3h6l1 3h3v15H5V6h3z"></path><path d="M9 11h6"></path><path d="M9 15h4"></path></svg>',
    display: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"></rect><path d="M8 20h8"></path><path d="M12 16v4"></path><path d="M7 8h10"></path></svg>',
    speech: '<svg viewBox="0 0 24 24"><path d="M11 5L6 9H3v6h3l5 4z"></path><path d="M15 9a4 4 0 0 1 0 6"></path><path d="M18 6a8 8 0 0 1 0 12"></path></svg>',
    camera: '<svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"></path><circle cx="12" cy="13" r="3"></circle></svg>',
    background: '<svg viewBox="0 0 24 24"><path d="M4 6h16v12H4z"></path><path d="M8 10h8"></path><path d="M8 14h5"></path></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"></path><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"></path></svg>'
})

const endpointKind = (endpoint) => {
  const kind = String(endpoint.presentationKind || '')
  return Object.hasOwn(endpointIcons, kind) ? kind : 'link'
}

const endpointIcon = (kind) => endpointIcons[kind] || endpointIcons.link

const refreshPreview = async () => {
  try {
    const preview = await api('/api/preview', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions()
      })
    })
    applyPreviewOptions(preview.options)
    setCommandPreview(preview.commandLine)
  } catch (error) {
    $('command-preview').textContent = error.message
  }
}

const refreshState = async () => {
  const payload = await api('/api/state')
  state.profiles = payload.profiles || []
  state.selectedProfileId = payload.config?.selectedProfileId || 'thought-core-v0'
  state.configIdentity = payload.config?.configIdentity || null
  state.options = payload.config?.options || {}
  $('workspace-root').textContent = payload.portMode
    ? `${payload.workspaceRoot} · ${payload.portMode}`
    : payload.workspaceRoot
  $('status-time').textContent = payload.status?.timestamp || t('status.unknown')
  state.latestStatusTimestamp = payload.status?.timestamp || ''
  state.latestServices = payload.status?.services || {}
  state.startupTiming = payload.startupTiming || payload.status?.startupTiming || null
  state.diagnosticSurfaces = payload.diagnosticSurfaces || payload.status?.diagnosticSurfaces || null
  state.latestEndpoints = payload.endpoints || []
  state.demoSafeSettings = payload.demoSafeSettings || {
    rows: [],
    summary: { total: 0, enabled: 0, enabled_appliance: 0, enabled_readiness: 0 }
  }
  state.demoReadinessStatus = payload.demoReadinessStatus || { rows: [] }
  setCommandPreview(payload.preview?.commandLine || '')
  renderLauncherLog(payload.logTail)
  renderControls()
  applyServerOperation(payload.operation || payload.status?.operation)
  renderSystemSummary(state.latestServices, state.latestStatusTimestamp)
  renderServices(state.latestServices)
  renderStartupTiming(state.startupTiming)
  renderDiagnosticSurfaces(state.diagnosticSurfaces)
  renderEndpoints(state.latestEndpoints)
}

const refreshStatusOnly = async () => {
  const payload = await api('/api/status')
  $('status-time').textContent = payload.timestamp || t('status.unknown')
  state.latestStatusTimestamp = payload.timestamp || ''
  state.latestServices = payload.services || {}
  state.startupTiming = payload.startupTiming || null
  state.diagnosticSurfaces = payload.diagnosticSurfaces || null
  applyServerOperation(payload.operation)
  renderSystemSummary(state.latestServices, state.latestStatusTimestamp)
  renderServices(state.latestServices)
  renderStartupTiming(state.startupTiming)
  renderDiagnosticSurfaces(state.diagnosticSurfaces)
  const logs = await api('/api/logs')
  renderLauncherLog(logs.logTail)
}

const refreshLogsOnly = async () => {
  const logs = await api('/api/logs')
  renderLauncherLog(logs.logTail)
}

const startStack = async () => {
  setOperation('starting', t('action.startSending'))
  setBusy(true, t('saveState.starting'))
  try {
    const saved = await api('/api/save-config', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions(),
        demoSettings: currentDemoSafeSettings()
      })
    })
    state.configIdentity = saved.configIdentity
    await api('/api/start', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        expectedConfigSha256: saved.configIdentity?.effective_config_sha256
      })
    })
    setOperation('starting', t('action.startAcceptedWatching'))
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const stopStack = async () => {
  setOperation('stopping', t('action.stopRunning'))
  setBusy(true, t('saveState.stopping'))
  try {
    const payload = await api('/api/stop', {
      method: 'POST',
      body: JSON.stringify({})
    })
    setOperation('stopped', formatStopVerificationDetail(payload.stopVerification))
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const reclaimManagedPorts = async () => {
  setOperation('reclaim', t('action.reclaimPorts'))
  setBusy(true, t('saveState.working'))
  try {
    const payload = await api('/api/reclaim-managed-ports', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId
      })
    })
    const recovered = Number(payload.managedPortReclaim?.reclaimed?.length || 0)
    setOperation(
      recovered > 0 ? 'stopped' : 'idle',
      recovered > 0
        ? t('action.reclaimPortsRecovered')
        : t('action.reclaimPortsNone')
    )
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const stopLauncher = async () => {
  const confirmed = window.confirm(
    t('action.stopLauncherConfirm')
  )
  if (!confirmed) {
    return
  }
  setOperation('stopping', t('action.stopLauncherShuttingDown'))
  setBusy(true, t('saveState.stopping'))
  try {
    await api('/api/shutdown', { method: 'POST' })
  } catch (error) {
    if (!String(error.message || '').includes('Failed to fetch')) {
      throw error
    }
  }
  setOperation('stopped', t('action.launcherStopped'))
  document.querySelectorAll('button, input, select').forEach((element) => {
    element.disabled = true
  })
}

const saveConfig = async () => {
  setOperation('saving', t('action.savingConfig'))
  setBusy(true, t('saveState.saving'))
  try {
    await api('/api/save-config', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions(),
        demoSettings: currentDemoSafeSettings()
      })
    })
    await refreshState()
    setSaveState('saveState.saved')
    window.setTimeout(() => {
      setSaveState('saveState.ready')
    }, 1200)
  } finally {
    setBusy(false)
    setOperation('idle')
  }
}

const bindControls = () => {
  document.querySelectorAll('[data-language]').forEach((button) => {
    button.addEventListener('click', () => setLanguage(button.dataset.language))
  })
  $('profile-select').addEventListener('change', (event) => {
    state.selectedProfileId = event.target.value
    applyProfileDefaults().catch(showError)
  })
  for (const field of numericOptionFields) {
    $(field).addEventListener('change', (event) => {
      setOption(field, Number(event.target.value))
    })
  }
  $('startup-timing-list').addEventListener('change', (event) => {
    const field = event.target?.dataset?.readyTimeoutField
    if (!field) {
      return
    }
    setReadyTimeoutOption(field, event.target.value)
  })
  $('startup-timing-list').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target?.dataset?.readyTimeoutField) {
      event.target.blur()
    }
  })
  for (const field of textFields) {
    $(field).addEventListener('change', (event) => {
      setOption(field, event.target.value)
    })
  }
  $('MediapipeCameraSelectionKey').addEventListener('change', (event) => {
    const selectionKey = String(event.target.value || '').trim()
    const matchingDevices = videoInputDevices().filter(
      (device) => device.value === selectionKey
    )
    const selectedDevice = matchingDevices[0]
    state.options.MediapipeCameraSelectionKey = selectionKey
    state.options.MediapipeCameraName = selectedDevice?.label || ''
    state.videoInputSelectionClass = !selectionKey
      ? 'no_selection'
      : matchingDevices.length === 1
        ? 'selected_available'
        : matchingDevices.length > 1
          ? 'selected_ambiguous'
          : 'selected_unresolvable'
    renderControls()
    refreshPreview()
  })
  $('refresh-camera-devices').addEventListener('click', () => {
    refreshVideoInputDevices().catch(showError)
  })
  $('apply-manual-camera').addEventListener('click', () => {
    const manualInput = $('MediapipeCameraNameManual')
    const value = normalizeCameraSelection(manualInput.value)
    if (!value) {
      return
    }
    state.options.MediapipeCameraSelectionKey = ''
    state.videoInputSelectionClass = 'manual_selection'
    setOption('MediapipeCameraName', value)
    manualInput.value = ''
  })
  $('ThoughtCoreLlmProvider').addEventListener('change', (event) => {
    setOption('ThoughtCoreLlmProvider', event.target.value)
  })
  document.querySelectorAll('#mediapipe-mode button').forEach((button) => {
    button.addEventListener('click', () => {
      setOption('MediapipeMode', button.dataset.value)
    })
  })
  $('refresh-button').addEventListener('click', () => refreshState().catch(showError))
  $('save-config').addEventListener('click', () => saveConfig().catch(showError))
  $('start-button').addEventListener('click', () => startStack().catch(showError))
  $('stop-button').addEventListener('click', () => stopStack().catch(showError))
  $('reclaim-ports-button').addEventListener('click', () => reclaimManagedPorts().catch(showError))
  $('stop-launcher-button').addEventListener('click', () => stopLauncher().catch(showError))
  $('copy-command').addEventListener('click', async () => {
    await navigator.clipboard.writeText($('command-preview').textContent)
    setSaveState('saveState.copied')
    window.setTimeout(() => {
      setSaveState('saveState.ready')
    }, 1200)
  })
  $('copy-log').addEventListener('click', async () => {
    await navigator.clipboard.writeText(state.latestLogTailRaw || $('log-output').textContent)
    setSaveState('saveState.logCopied')
    window.setTimeout(() => {
      setSaveState('saveState.ready')
    }, 1200)
  })
}

const showError = (error) => {
  setBusy(false)
  if (error.payload?.error === 'operation_in_progress') {
    const operation = error.payload.operation
    state.remoteBusy = Boolean(operation && operation.busy)
    state.remoteOperation = state.remoteBusy ? operation : null
    setOperation('blocked', error.payload.message || t('error.operationInProgress'))
    $('save-state').textContent = state.remoteBusy ? t('saveState.locked') : t('saveState.ready')
    prependLauncherLog(error.message)
    return
  }
  if (error.payload?.stopVerification) {
    setOperation('error', formatStopVerificationDetail(error.payload.stopVerification))
    setSaveState('saveState.error')
    prependLauncherLog(error.payload.message || error.message)
    return
  }
  setOperation('error', error.message || t('error.checkLog'))
  setSaveState('saveState.error')
  prependLauncherLog(error.message)
}

applyStaticTranslations()
bindControls()
refreshState()
  .then(async () => {
    await refreshVideoInputDevices()
    renderOperation()
    renderActionButtons()
    window.setInterval(() => refreshStatusOnly().catch(() => {}), 5000)
  })
  .catch(showError)
