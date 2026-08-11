'use strict'

/**
 * 根幹5/6: sealed private planを実行する所有workerとの唯一の通信窓口。
 * Node側はJSON lineを相関し、PowerShell workerの作業結果を固定schemaで受け取る。
 * 任意commandや未検証processを起動する一般shellではない。
 */

const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { spawn } = require('node:child_process')
const {
  assertAuthority,
  deepFreeze,
  validateWorkerMessage,
  validateWorkerRequestAgainstAuthority
} = require('./launcher-supervisor-contract')
const { getSupervisorLeaseBinding } = require('./launcher-operation-store')
const {
  TRUSTED_WINDOWS_WORKERS,
  verifyTrustedWindowsWorkerExecutable
} = require('./launcher-private-service-plan')

const MAX_WORKER_LINE_BYTES = 4096
const MAX_PRIVATE_PLAN_BYTES = 256 * 1024
const DEFAULT_CLOSE_TIMEOUT_MS = 5000
const DEFAULT_RESPONSE_GRACE_MS = 1000
const RESULT_FIELDS = [
  'operation_id', 'supervisor_generation', 'authority_lease_proof', 'dispatch_id', 'service_id', 'action', 'expected_revision', 'worker_nonce'
]
const SAFE_ERROR_CODES = new Set([
  'worker_request_oversized',
  'worker_response_oversized',
  'worker_response_invalid',
  'worker_response_mismatch',
  'worker_transport_busy',
  'worker_transport_closed',
  'worker_transport_failed',
  'worker_transport_timeout',
  'worker_configuration_invalid'
])
const SHA256 = /^[a-f0-9]{64}$/u
const WORKER_EXECUTABLE_CLASSES = new Set(Object.keys(TRUSTED_WINDOWS_WORKERS))

class LauncherJobWorkerError extends Error {
  constructor (code) {
    super(SAFE_ERROR_CODES.has(code) ? code : 'worker_transport_failed')
    this.name = 'LauncherJobWorkerError'
    this.code = this.message
  }
}

const fail = (code) => { throw new LauncherJobWorkerError(code) }

const exactAbsoluteFile = (value) => {
  if (typeof value !== 'string' || value.length === 0 || value.length > 1024 || value.includes('\u0000') || !path.isAbsolute(value)) {
    fail('worker_configuration_invalid')
  }
  const resolved = path.resolve(value)
  if (!fs.existsSync(resolved)) fail('worker_configuration_invalid')
  const stat = fs.lstatSync(resolved)
  if (!stat.isFile() || stat.isSymbolicLink()) fail('worker_configuration_invalid')
  return resolved
}

const exactAbsoluteDirectory = (value) => {
  if (typeof value !== 'string' || value.length === 0 || value.length > 1024 || value.includes('\u0000') || !path.isAbsolute(value)) {
    fail('worker_configuration_invalid')
  }
  const resolved = path.resolve(value)
  if (!fs.existsSync(resolved) || !fs.lstatSync(resolved).isDirectory()) fail('worker_configuration_invalid')
  return resolved
}

const parseWorkerResultLine = (line, authority) => {
  assertAuthority(authority)
  if (typeof line !== 'string' || Buffer.byteLength(line, 'utf8') === 0 || Buffer.byteLength(line, 'utf8') > MAX_WORKER_LINE_BYTES || /[\r\n]/u.test(line)) {
    fail('worker_response_oversized')
  }
  let value
  try { value = JSON.parse(line) } catch { fail('worker_response_invalid') }
  try { validateWorkerMessage(value, authority) } catch { fail('worker_response_invalid') }
  if (value.message_type !== 'result') fail('worker_response_invalid')
  return deepFreeze(JSON.parse(JSON.stringify(value)))
}

const correlateWorkerResult = (request, result, authority) => {
  assertAuthority(authority)
  try {
    validateWorkerRequestAgainstAuthority(request, authority)
    validateWorkerMessage(result, authority)
  } catch {
    fail('worker_response_invalid')
  }
  if (result.message_type !== 'result') fail('worker_response_invalid')
  for (const field of RESULT_FIELDS) {
    if (result[field] !== request[field]) fail('worker_response_mismatch')
  }
  return result
}

class LauncherJobWorkerClient {
  constructor ({ authority, transport, supervisorLease }) {
    assertAuthority(authority)
    if (!transport || typeof transport.exchange !== 'function' || typeof transport.close !== 'function') {
      fail('worker_configuration_invalid')
    }
    let leaseBinding
    try { leaseBinding = getSupervisorLeaseBinding(supervisorLease, authority) } catch { fail('worker_configuration_invalid') }
    if (transport.authorityLeaseProof !== leaseBinding.authority_lease_proof) fail('worker_configuration_invalid')
    this.authority = authority
    this.transport = transport
    this.leaseBinding = leaseBinding
    this.closed = false
    this.inflight = false
  }

  async execute (request) {
    if (this.closed) fail('worker_transport_closed')
    if (this.inflight) fail('worker_transport_busy')
    try { validateWorkerRequestAgainstAuthority(request, this.authority) } catch { fail('worker_response_invalid') }
    if (request.operation_id !== this.leaseBinding.operation_id ||
        request.supervisor_generation !== this.leaseBinding.supervisor_generation ||
        request.authority_lease_proof !== this.leaseBinding.authority_lease_proof) fail('worker_response_invalid')
    const line = JSON.stringify(request)
    if (Buffer.byteLength(line, 'utf8') > MAX_WORKER_LINE_BYTES) fail('worker_request_oversized')
    this.inflight = true
    try {
      const responseLine = await this.transport.exchange(line, request.deadline_ms)
      const result = parseWorkerResultLine(responseLine, this.authority)
      return correlateWorkerResult(request, result, this.authority)
    } catch (error) {
      if (error instanceof LauncherJobWorkerError && error.code.startsWith('worker_response_') && typeof this.transport.abort === 'function') {
        this.transport.abort()
      }
      if (error instanceof LauncherJobWorkerError) throw error
      fail('worker_transport_failed')
    } finally {
      this.inflight = false
    }
  }

  async close () {
    if (this.closed) return
    this.closed = true
    try { await this.transport.close() } catch { fail('worker_transport_failed') }
  }
}

class PowerShellJsonLineTransport {
  constructor ({
    repositoryRoot,
    privatePlanPath,
    powershellPath,
    privatePlanSha256,
    workerExecutableClass,
    workerExecutableSha256,
    authority,
    supervisorLease,
    spawnImpl = spawn,
    workerExecutableVerifier = verifyTrustedWindowsWorkerExecutable,
    closeTimeoutMs = DEFAULT_CLOSE_TIMEOUT_MS,
    responseGraceMs = DEFAULT_RESPONSE_GRACE_MS
  }) {
    let leaseBinding
    try { leaseBinding = getSupervisorLeaseBinding(supervisorLease, authority) } catch { fail('worker_configuration_invalid') }
    this.repositoryRoot = exactAbsoluteDirectory(repositoryRoot)
    this.privatePlanPath = exactAbsoluteFile(privatePlanPath)
    if (typeof privatePlanSha256 !== 'string' || !SHA256.test(privatePlanSha256) ||
        !WORKER_EXECUTABLE_CLASSES.has(workerExecutableClass) ||
        typeof workerExecutableSha256 !== 'string' || !SHA256.test(workerExecutableSha256) ||
        typeof spawnImpl !== 'function' || typeof workerExecutableVerifier !== 'function' ||
        !Number.isSafeInteger(closeTimeoutMs) || closeTimeoutMs < 100 || closeTimeoutMs > 30000 ||
        !Number.isSafeInteger(responseGraceMs) || responseGraceMs < 100 || responseGraceMs > 5000) {
      fail('worker_configuration_invalid')
    }
    this.powershellPath = exactAbsoluteFile(powershellPath)
    this.privatePlanSha256 = privatePlanSha256
    this.workerExecutableClass = workerExecutableClass
    this.workerExecutableSha256 = workerExecutableSha256
    this.spawnImpl = spawnImpl
    this.workerExecutableVerifier = workerExecutableVerifier
    this.closeTimeoutMs = closeTimeoutMs
    this.responseGraceMs = responseGraceMs
    this.authorityLeaseProof = leaseBinding.authority_lease_proof
    this.child = null
    this.pending = null
    this.buffer = ''
    this.closed = false
    this.terminal = false
  }

  start () {
    if (this.child || this.closed) return
    const workerPath = exactAbsoluteFile(path.join(this.repositoryRoot, 'ops', 'scripts', 'home-control-stack', 'launcher-job-worker.ps1'))
    let privatePlanBytes
    let verifiedWorker
    try {
      privatePlanBytes = fs.readFileSync(this.privatePlanPath)
      if (!Buffer.isBuffer(privatePlanBytes) || privatePlanBytes.length === 0 || privatePlanBytes.length > MAX_PRIVATE_PLAN_BYTES ||
          crypto.createHash('sha256').update(privatePlanBytes).digest('hex') !== this.privatePlanSha256) {
        fail('worker_configuration_invalid')
      }
      verifiedWorker = this.workerExecutableVerifier({
        filePath: this.powershellPath,
        expectedClass: this.workerExecutableClass,
        expectedSha256: this.workerExecutableSha256
      })
      if (!verifiedWorker || path.win32.normalize(verifiedWorker.worker_file_path).toLowerCase() !== path.win32.normalize(this.powershellPath).toLowerCase()) {
        fail('worker_configuration_invalid')
      }
    } catch (error) {
      if (error instanceof LauncherJobWorkerError) throw error
      fail('worker_configuration_invalid')
    }
    const environment = {
      ...process.env,
      SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE: this.privatePlanPath,
      SWORD_LAUNCHER_N1_PRIVATE_PLAN_SHA256: this.privatePlanSha256,
      SWORD_LAUNCHER_N1_PRIVATE_LEASE_PROOF: this.authorityLeaseProof
    }
    const child = this.spawnImpl(verifiedWorker.worker_file_path, [
      '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', workerPath
    ], {
      cwd: this.repositoryRoot,
      env: environment,
      windowsHide: true,
      shell: false,
      stdio: ['pipe', 'pipe', 'ignore']
    })
    if (!child || !child.stdin || !child.stdout || typeof child.once !== 'function') fail('worker_transport_failed')
    this.child = child
    child.stdout.on('data', (chunk) => this.onData(chunk))
    child.stdin.on('error', () => {
      this.terminal = true
      this.failPending('worker_transport_failed', true)
    })
    child.once('error', () => {
      this.terminal = true
      this.failPending('worker_transport_failed')
    })
    child.once('exit', () => {
      this.terminal = true
      this.failPending('worker_transport_failed')
    })
  }

  onData (chunk) {
    if (!this.pending) return
    this.buffer += Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk)
    if (Buffer.byteLength(this.buffer, 'utf8') > MAX_WORKER_LINE_BYTES + 2) {
      this.failPending('worker_response_oversized', true)
      return
    }
    const newline = this.buffer.indexOf('\n')
    if (newline < 0) return
    const line = this.buffer.slice(0, newline).replace(/\r$/u, '')
    const trailing = this.buffer.slice(newline + 1)
    this.buffer = ''
    if (trailing.length !== 0) {
      this.failPending('worker_response_invalid', true)
      return
    }
    const pending = this.pending
    this.pending = null
    clearTimeout(pending.timer)
    pending.resolve(line)
  }

  failPending (code, terminate = false) {
    if (!this.pending) return
    const pending = this.pending
    this.pending = null
    this.buffer = ''
    clearTimeout(pending.timer)
    pending.reject(new LauncherJobWorkerError(code))
    if (terminate) this.abort()
  }

  abort () {
    this.terminal = true
    const child = this.child
    if (!child) return
    try { child.stdin.end() } catch {}
    try { child.kill() } catch {}
  }

  exchange (line, deadlineMs) {
    if (this.closed) return Promise.reject(new LauncherJobWorkerError('worker_transport_closed'))
    if (this.pending) return Promise.reject(new LauncherJobWorkerError('worker_transport_busy'))
    if (typeof line !== 'string' || Buffer.byteLength(line, 'utf8') > MAX_WORKER_LINE_BYTES || !Number.isSafeInteger(deadlineMs) || deadlineMs < 0 || deadlineMs > 300000) {
      return Promise.reject(new LauncherJobWorkerError('worker_configuration_invalid'))
    }
    this.start()
    if (this.terminal) return Promise.reject(new LauncherJobWorkerError('worker_transport_failed'))
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!this.pending) return
        this.failPending('worker_transport_timeout', true)
      }, Math.max(1, deadlineMs + this.responseGraceMs))
      this.pending = { resolve, reject, timer }
      try {
        this.child.stdin.write(`${line}\n`, 'utf8', (error) => {
          if (error) this.failPending('worker_transport_failed', true)
        })
      } catch {
        this.failPending('worker_transport_failed', true)
      }
    })
  }

  async close () {
    if (this.closed) return
    this.closed = true
    this.failPending('worker_transport_closed')
    const child = this.child
    this.child = null
    if (!child) return
    const exited = await new Promise((resolve) => {
      let completed = false
      let gracefulTimer = null
      let forcedTimer = null
      const finish = (value) => {
        if (completed) return
        completed = true
        if (gracefulTimer) clearTimeout(gracefulTimer)
        if (forcedTimer) clearTimeout(forcedTimer)
        resolve(value)
      }
      const force = () => {
        if (completed) return
        try {
          if (child.exitCode !== null) return finish(true)
          if (child.kill() === false && child.exitCode !== null) return finish(true)
        } catch {
          return finish(false)
        }
        forcedTimer = setTimeout(() => finish(false), this.closeTimeoutMs)
      }
      if (child.exitCode !== null) return finish(true)
      child.once('exit', () => finish(true))
      gracefulTimer = setTimeout(force, this.closeTimeoutMs)
      try { child.stdin.end() } catch { force() }
    })
    if (!exited) fail('worker_transport_failed')
  }
}

const createOwnerLivenessObserver = ({ inspectProcess }) => {
  if (typeof inspectProcess !== 'function') fail('worker_configuration_invalid')
  return (owner) => {
    if (!owner || !Number.isSafeInteger(owner.owner_pid) || owner.owner_pid <= 0 ||
        !Number.isSafeInteger(owner.created_at_ms) || owner.created_at_ms < 0) return 'unknown'
    let observation
    try { observation = inspectProcess(owner.owner_pid) } catch { return 'unknown' }
    if (!observation || typeof observation !== 'object' || Array.isArray(observation)) return 'unknown'
    if (observation.status === 'absent') return 'absent'
    if (observation.status === 'access_denied') return 'access_denied'
    if (observation.status !== 'alive' || !Number.isSafeInteger(observation.creation_time_ms) || observation.creation_time_ms < 0) return 'unknown'
    return observation.creation_time_ms > owner.created_at_ms ? 'pid_reused' : 'alive'
  }
}

module.exports = {
  LauncherJobWorkerClient,
  LauncherJobWorkerError,
  MAX_WORKER_LINE_BYTES,
  PowerShellJsonLineTransport,
  correlateWorkerResult,
  createOwnerLivenessObserver,
  parseWorkerResultLine
}
