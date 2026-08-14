'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

const appSource = fs.readFileSync(
  path.join(__dirname, '..', 'tools', 'home-control-launcher', 'public', 'app.js'),
  'utf8'
)
const start = appSource.indexOf('const projectStartupTimingView =')
const end = appSource.indexOf('const renderStartupTiming =', start)
assert.notEqual(start, -1)
assert.notEqual(end, -1)

const context = { result: null }
vm.runInNewContext(
  `${appSource.slice(start, end)}\nresult = projectStartupTimingView`,
  context,
  { filename: 'launcher-startup-timing-view.js' }
)
const projectStartupTimingView = context.result

const timing = (readyServiceIds, operational = false) => ({
  schema_version: 'launcher_startup_timing.v1',
  status_class: operational ? 'ready' : 'waiting_ready',
  expectedServiceIds: ['thought_core_api', 'aituber_kit', 'voicevox'],
  readyServiceIds,
  operational,
  readiness_authority: 'node_supervisor',
  raw_private_publication_flags: false
})

test('0/3と2/3は未Readyの行だけを待機表示にする', () => {
  const zero = projectStartupTimingView(timing([]))
  assert.equal(zero.readyCount, 0)
  assert.equal(zero.operational, false)
  assert.deepEqual(Array.from(zero.rows, (row) => row.ready), [false, false, false])

  const partial = projectStartupTimingView(timing(['thought_core_api', 'voicevox']))
  assert.equal(partial.readyCount, 2)
  assert.equal(partial.operational, false)
  assert.deepEqual(Array.from(partial.rows, (row) => row.ready), [true, false, true])
})

test('公開3/3でもsupervisorが未完了なら起動完了にしない', () => {
  const view = projectStartupTimingView(
    timing(['thought_core_api', 'aituber_kit', 'voicevox'], false)
  )
  assert.equal(view.readyCount, 3)
  assert.equal(view.operational, false)
})

test('公開3/3かつsupervisor operationalだけを起動完了にする', () => {
  const view = projectStartupTimingView(
    timing(['thought_core_api', 'aituber_kit', 'voicevox'], true)
  )
  assert.equal(view.expectedCount, 3)
  assert.equal(view.readyCount, 3)
  assert.equal(view.operational, true)
  assert.equal(view.statusClass, 'ready')
})

test('v0、重複、未知のReady IDは表示不能として閉じる', () => {
  assert.equal(projectStartupTimingView({ schema_version: 'launcher_startup_timing.v0' }), null)
  assert.equal(
    projectStartupTimingView({
      ...timing(['thought_core_api']),
      expectedServiceIds: ['thought_core_api', 'thought_core_api']
    }),
    null
  )
  assert.equal(
    projectStartupTimingView(timing(['thought_core_api', 'unknown_service'])),
    null
  )
})
