'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')

const {
  buildLauncherSurfaceCatalog,
  buildProjectionVisualUrls,
  normalizeLoopbackHttpUrl
} = require('../tools/home-control-launcher/launcher-surface-catalog')

const options = Object.freeze({
  AituberPort: 3000,
  ThoughtCoreHost: '0.0.0.0',
  ThoughtCorePort: 8767,
  TouchDesignerGuiPort: 9982,
  HomeAssistantBridgePort: 8787,
  EnvironmentStatePort: 8791,
  MediapipeBrowserMonitorPort: 8794,
  MediapipePort: 8792,
  MediapipeMode: 'mediamtx',
  VisionSnapshotProcessorPort: 8793,
  VoicevoxUrl: 'http://localhost:50021/',
  SkipAituber: false,
  EnableThoughtCore: true,
  SkipTouchDesignerGui: false,
  SkipHomeAssistantBridge: false,
  SkipEnvironmentState: false,
  SkipMediapipe: false,
  SkipVisionSnapshotProcessor: false,
  SkipVoicevoxCheck: false,
  EnableThoughtCoreWatch: true
})

test('Projection Visualの三つの役割を別URLとして固定する', () => {
  assert.deepEqual(buildProjectionVisualUrls('127.0.0.1', 3000), {
    operator: 'http://127.0.0.1:3000/projection-visual/',
    stageOutput: 'http://127.0.0.1:3000/projection-visual/?mode=stage-output&hud=0',
    passive: 'http://127.0.0.1:3000/projection-visual/?mode=passive&hud=0'
  })
})

test('利用者向けsurface一覧に正式なstage-outputを一つだけ含める', () => {
  const catalog = buildLauncherSurfaceCatalog(options)
  const stageOutputs = catalog.filter((entry) => entry.name === 'Projection Stage Output')

  assert.equal(stageOutputs.length, 1)
  assert.deepEqual(stageOutputs[0], {
    group: 'Open in browser',
    name: 'Projection Stage Output',
    url: 'http://127.0.0.1:3000/projection-visual/?mode=stage-output&hud=0',
    enabled: true
  })
})

test('公開用URLではlocalhostだけをloopback literalへ正規化する', () => {
  assert.equal(normalizeLoopbackHttpUrl('http://localhost:50021'), 'http://127.0.0.1:50021')
  assert.equal(normalizeLoopbackHttpUrl('https://localhost:50021'), 'https://localhost:50021')
})
