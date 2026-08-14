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
const start = appSource.indexOf('const formatOperationError =')
const end = appSource.indexOf('const showError =', start)
assert.notEqual(start, -1)
assert.notEqual(end, -1)

const context = {
  result: null,
  t: (key) => key
}
vm.runInNewContext(
  `${appSource.slice(start, end)}\nresult = formatOperationError`,
  context,
  { filename: 'launcher-operation-error-view.js' }
)
const formatOperationError = context.result

const readinessFailure = (failedServiceId) => ({
  message: 'HTTP 409',
  payload: {
    operation: {
      phase: 'failed',
      reason: 'readiness_timeout',
      services: [
        { service_id: failedServiceId, state: 'failed' }
      ]
    }
  }
})

test('VOICEVOX readiness timeoutを具体的なowner actionへ変換する', () => {
  assert.equal(
    formatOperationError(readinessFailure('voicevox')),
    'error.voicevoxUnavailable'
  )
})

test('他serviceのreadiness timeoutは汎用Ready案内へ変換する', () => {
  assert.equal(
    formatOperationError(readinessFailure('thought_core_api')),
    'error.readinessTimeout'
  )
})

test('管理状態のconfig lockは完了待ちまたは停止後の再操作を案内する', () => {
  assert.equal(
    formatOperationError({ payload: { error: 'operation_config_locked' } }),
    'error.operationConfigLocked'
  )
})

test('未知の失敗は既存のbounded messageへ戻す', () => {
  assert.equal(formatOperationError({ message: 'HTTP 500' }), 'HTTP 500')
})
