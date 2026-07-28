'use strict'

const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

const { LauncherContractError, assertAuthority, readBoundedUtf8Text } = require('./launcher-supervisor-contract')
const { reduce, startOperation, validateIdentityInputs, validateSnapshot } = require('./launcher-supervisor-reducer')

const STORE_DIRECTORY = 'launcher-operation.v1'
const RECORD_FILE = 'launcher-operation.v1.json'
const LOCK_FILE = 'launcher-operation.v1.lock'
const TEMP_FILE = 'launcher-operation.v1.json.tmp'
const LOCK_RECOVERY_FILE = 'launcher-operation.v1.lock.recovering'
const LOCK_DISCARD_FILE = 'launcher-operation.v1.lock.discarding'
const MAX_OPERATION_RECORD_BYTES = 64 * 1024
const MAX_LOCK_RECORD_BYTES = 1024
const LOCK_STALE_MS = 30000
const LOCK_NONCE = /^ll_[a-z0-9]{32}$/u
const OWNER_LIVENESS = new Set(['alive', 'absent', 'pid_reused', 'access_denied', 'unknown'])
const ALLOWED_CONTENT = new Set([RECORD_FILE, LOCK_FILE, TEMP_FILE, LOCK_RECOVERY_FILE, LOCK_DISCARD_FILE])
const fail = (code) => { throw new LauncherContractError(code) }

const lstat = (target, code) => {
  try { return fs.lstatSync(target) } catch { fail(code) }
}

const rejectReparse = (target) => {
  const stat = lstat(target, 'operation_store_root_invalid')
  if (stat.isSymbolicLink()) fail('operation_store_reparse_rejected')
  return stat
}

const rejectReparseTraversal = (fullPath) => {
  const root = path.parse(fullPath).root
  let current = root
  for (const segment of path.relative(root, fullPath).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment)
    rejectReparse(current)
  }
}

const hardenPrivate = (target, directory) => {
  try {
    fs.chmodSync(target, directory ? 0o700 : 0o600)
    if (process.platform !== 'win32') {
      const expected = directory ? 0o700 : 0o600
      if ((fs.statSync(target).mode & 0o777) !== expected) fail('operation_store_access_invalid')
    }
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_access_invalid')
  }
}

const validateContents = (paths) => {
  let entries
  try { entries = fs.readdirSync(paths.root, { withFileTypes: true }) } catch { fail('operation_store_root_invalid') }
  for (const entry of entries) {
    if (!ALLOWED_CONTENT.has(entry.name)) fail('operation_store_foreign_content')
    const stat = rejectReparse(path.join(paths.root, entry.name))
    if (!stat.isFile()) fail('operation_store_foreign_content')
  }
}

const resolvePaths = (authorizedPrivateRuntimeRoot) => {
  if (typeof authorizedPrivateRuntimeRoot !== 'string' || !path.isAbsolute(authorizedPrivateRuntimeRoot)) fail('operation_store_root_invalid')
  const parent = path.resolve(authorizedPrivateRuntimeRoot)
  const parentStat = rejectReparse(parent)
  if (!parentStat.isDirectory()) fail('operation_store_root_invalid')
  rejectReparseTraversal(parent)
  const root = path.resolve(parent, STORE_DIRECTORY)
  if (path.dirname(root).toLowerCase() !== parent.toLowerCase()) fail('operation_store_root_invalid')
  try {
    if (fs.existsSync(root)) {
      const existing = fs.lstatSync(root)
      if (existing.isSymbolicLink()) fail('operation_store_reparse_rejected')
      if (!existing.isDirectory()) fail('operation_store_collision')
    }
    if (!fs.existsSync(root)) fs.mkdirSync(root, { mode: 0o700 })
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_root_invalid')
  }
  const rootStat = rejectReparse(root)
  if (!rootStat.isDirectory()) fail('operation_store_collision')
  hardenPrivate(root, true)
  const recordPath = path.resolve(root, RECORD_FILE)
  const lockPath = path.resolve(root, LOCK_FILE)
  const temporaryPath = path.resolve(root, TEMP_FILE)
  const recoveryLockPath = path.resolve(root, LOCK_RECOVERY_FILE)
  const discardLockPath = path.resolve(root, LOCK_DISCARD_FILE)
  if ([recordPath, lockPath, temporaryPath, recoveryLockPath, discardLockPath].some((candidate) => path.dirname(candidate).toLowerCase() !== root.toLowerCase())) fail('operation_store_root_invalid')
  const paths = { root, recordPath, lockPath, temporaryPath, recoveryLockPath, discardLockPath }
  validateContents(paths)
  return paths
}

const exactLockRecord = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== 'created_at_ms,owner_nonce,owner_pid,schema_version' ||
      value.schema_version !== 'launcher_operation_lock.v1' || typeof value.owner_nonce !== 'string' ||
      !LOCK_NONCE.test(value.owner_nonce) || !Number.isSafeInteger(value.owner_pid) || value.owner_pid <= 0 ||
      !Number.isSafeInteger(value.created_at_ms) || value.created_at_ms < 0) {
    fail('operation_store_lock_unavailable')
  }
  return value
}

const readLockRecord = (lockPath) => {
  try {
    return exactLockRecord(JSON.parse(readBoundedUtf8Text(
      lockPath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable'
    )))
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_lock_unavailable')
  }
}

const parseLockRecordText = (text) => {
  try { return exactLockRecord(JSON.parse(text)) } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_lock_unavailable')
  }
}

const isStaleLock = (record) => {
  const age = Date.now() - record.created_at_ms
  return Number.isSafeInteger(age) && age > LOCK_STALE_MS
}

const defaultObserveOwnerLiveness = (owner) => owner.owner_pid === process.pid ? 'alive' : 'unknown'

const observeOwnerLiveness = (observer, record) => {
  if (typeof observer !== 'function') return 'unknown'
  let result
  try {
    result = observer(Object.freeze({
      owner_pid: record.owner_pid,
      owner_nonce: record.owner_nonce,
      created_at_ms: record.created_at_ms
    }))
  } catch {
    return 'unknown'
  }
  return OWNER_LIVENESS.has(result) ? result : 'unknown'
}

const requireConfirmedAbsent = (observer, record) => {
  if (observeOwnerLiveness(observer, record) !== 'absent') fail('operation_store_lock_unavailable')
}

const restoreClaimOrPreserve = (paths, sourcePath) => {
  try {
    if (!fs.existsSync(sourcePath) && fs.existsSync(paths.discardLockPath)) {
      fs.renameSync(paths.discardLockPath, sourcePath)
    }
  } catch {}
  fail('operation_store_lock_unavailable')
}

const claimAndRemoveOwnedStaleLock = (paths, sourcePath, observedText, observed, ownerLivenessObserver) => {
  if (fs.existsSync(paths.discardLockPath)) fail('operation_store_lock_unavailable')
  requireConfirmedAbsent(ownerLivenessObserver, observed)
  const firstCheck = readBoundedUtf8Text(sourcePath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable')
  if (firstCheck !== observedText) fail('operation_store_lock_unavailable')
  requireConfirmedAbsent(ownerLivenessObserver, observed)
  const preClaim = readBoundedUtf8Text(sourcePath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable')
  if (preClaim !== observedText) fail('operation_store_lock_unavailable')
  try { fs.renameSync(sourcePath, paths.discardLockPath) } catch { fail('operation_store_lock_unavailable') }
  let claimedText
  try {
    claimedText = readBoundedUtf8Text(paths.discardLockPath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable')
  } catch { restoreClaimOrPreserve(paths, sourcePath) }
  if (claimedText !== observedText) restoreClaimOrPreserve(paths, sourcePath)
  if (observeOwnerLiveness(ownerLivenessObserver, observed) !== 'absent') restoreClaimOrPreserve(paths, sourcePath)
  let preUnlink
  try {
    preUnlink = readBoundedUtf8Text(paths.discardLockPath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable')
  } catch { restoreClaimOrPreserve(paths, sourcePath) }
  if (preUnlink !== observedText) restoreClaimOrPreserve(paths, sourcePath)
  try { fs.unlinkSync(paths.discardLockPath) } catch { restoreClaimOrPreserve(paths, sourcePath) }
}

const removeOwnedStaleRecovery = (paths, ownerLivenessObserver) => {
  if (fs.existsSync(paths.discardLockPath)) fail('operation_store_lock_unavailable')
  if (!fs.existsSync(paths.recoveryLockPath)) return
  const observedText = readBoundedUtf8Text(paths.recoveryLockPath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable')
  const recovery = parseLockRecordText(observedText)
  if (!isStaleLock(recovery) || fs.existsSync(paths.lockPath)) fail('operation_store_lock_unavailable')
  claimAndRemoveOwnedStaleLock(paths, paths.recoveryLockPath, observedText, recovery, ownerLivenessObserver)
}

const recoverStaleLock = (paths, ownerLivenessObserver) => {
  removeOwnedStaleRecovery(paths, ownerLivenessObserver)
  if (!fs.existsSync(paths.lockPath)) return
  const observedText = readBoundedUtf8Text(
    paths.lockPath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable'
  )
  const observed = parseLockRecordText(observedText)
  if (!isStaleLock(observed)) fail('operation_store_lock_unavailable')
  requireConfirmedAbsent(ownerLivenessObserver, observed)
  try {
    fs.renameSync(paths.lockPath, paths.recoveryLockPath)
    const claimedText = readBoundedUtf8Text(
      paths.recoveryLockPath, MAX_LOCK_RECORD_BYTES, 'operation_store_lock_unavailable', 'operation_store_lock_unavailable'
    )
    if (claimedText !== observedText) {
      if (!fs.existsSync(paths.lockPath)) fs.renameSync(paths.recoveryLockPath, paths.lockPath)
      fail('operation_store_lock_unavailable')
    }
    claimAndRemoveOwnedStaleLock(paths, paths.recoveryLockPath, observedText, observed, ownerLivenessObserver)
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_lock_unavailable')
  }
}

const acquireLock = (paths, ownerLivenessObserver) => {
  recoverStaleLock(paths, ownerLivenessObserver)
  let descriptor
  let created = false
  const ownerNonce = `ll_${crypto.randomBytes(16).toString('hex')}`
  try {
    descriptor = fs.openSync(paths.lockPath, 'wx', 0o600)
    created = true
    const bytes = Buffer.from(`${JSON.stringify({
      schema_version: 'launcher_operation_lock.v1', owner_nonce: ownerNonce,
      owner_pid: process.pid, created_at_ms: Date.now()
    })}\n`, 'utf8')
    fs.writeFileSync(descriptor, bytes)
    fs.fsyncSync(descriptor)
    hardenPrivate(paths.lockPath, false)
    return { descriptor, ownerNonce }
  } catch (error) {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor) } catch {}
    }
    if (created) {
      try {
        if (fs.existsSync(paths.lockPath) && readLockRecord(paths.lockPath).owner_nonce === ownerNonce) fs.unlinkSync(paths.lockPath)
      } catch {}
    }
    if (error instanceof LauncherContractError) throw error
    if (error?.code === 'EEXIST') fail('operation_store_lock_unavailable')
    fail('operation_store_lock_failed')
  }
}

const releaseLock = (lock, lockPath) => {
  let failed = false
  try { fs.closeSync(lock.descriptor) } catch { failed = true }
  try {
    if (readLockRecord(lockPath).owner_nonce !== lock.ownerNonce) fail('operation_store_lock_release_failed')
    fs.unlinkSync(lockPath)
  } catch { failed = true }
  if (failed) fail('operation_store_lock_release_failed')
}

const withLock = (paths, authority, ownerLivenessObserver, action) => {
  const lock = acquireLock(paths, ownerLivenessObserver)
  let result
  let actionError
  try {
    validateContents(paths)
    recoverOwnedTemporary(paths, authority)
    result = action()
  } catch (error) {
    actionError = error
  }
  let releaseError
  try {
    releaseLock(lock, paths.lockPath)
  } catch (error) { releaseError = error }
  if (actionError && releaseError) {
    const primaryCode = actionError instanceof LauncherContractError ? actionError.code : 'operation_store_action_failed'
    throw new LauncherContractError(primaryCode, 'operation_store_lock_release_failed')
  }
  if (actionError) throw actionError
  if (releaseError) throw releaseError
  return result
}

const readResolved = (recordPath, authority) => {
  let operation
  try {
    rejectReparse(recordPath)
    operation = JSON.parse(readBoundedUtf8Text(
      recordPath, MAX_OPERATION_RECORD_BYTES, 'operation_store_read_failed', 'operation_store_record_oversized'
    ))
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_read_failed')
  }
  return validateSnapshot(operation, authority)
}

const recoverOwnedTemporary = (paths, authority) => {
  if (!fs.existsSync(paths.temporaryPath)) return
  let pending
  try { pending = readResolved(paths.temporaryPath, authority) } catch { fail('operation_store_recovery_invalid') }
  if (!fs.existsSync(paths.recordPath)) {
    try {
      fs.renameSync(paths.temporaryPath, paths.recordPath)
      hardenPrivate(paths.recordPath, false)
      return
    } catch { fail('operation_store_recovery_failed') }
  }
  const current = readResolved(paths.recordPath, authority)
  if (pending.operation_id !== current.operation_id || pending.graph_sha256 !== current.graph_sha256 || pending.binding_sha256 !== current.binding_sha256) {
    fail('operation_store_recovery_invalid')
  }
  if (pending.revision === current.revision + 1) {
    try {
      fs.renameSync(paths.temporaryPath, paths.recordPath)
      hardenPrivate(paths.recordPath, false)
      return
    } catch { fail('operation_store_recovery_failed') }
  }
  if (pending.revision <= current.revision) {
    try { fs.unlinkSync(paths.temporaryPath) } catch { fail('operation_store_recovery_failed') }
    return
  }
  fail('operation_store_recovery_invalid')
}

const writeAtomic = (paths, operation, authority) => {
  validateSnapshot(operation, authority)
  let descriptor
  let created = false
  try {
    const bytes = Buffer.from(`${JSON.stringify(operation)}\n`, 'utf8')
    if (bytes.length > MAX_OPERATION_RECORD_BYTES) fail('operation_store_record_oversized')
    descriptor = fs.openSync(paths.temporaryPath, 'wx', 0o600)
    created = true
    fs.writeFileSync(descriptor, bytes)
    fs.fsyncSync(descriptor)
    fs.closeSync(descriptor)
    descriptor = undefined
    hardenPrivate(paths.temporaryPath, false)
    fs.renameSync(paths.temporaryPath, paths.recordPath)
    rejectReparse(paths.recordPath)
    hardenPrivate(paths.recordPath, false)
  } catch (error) {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor) } catch {}
    }
    try { if (created && fs.existsSync(paths.temporaryPath)) fs.unlinkSync(paths.temporaryPath) } catch {}
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_write_failed')
  }
}

const startAndPersist = (
  operationId, authority, authorizedPrivateRuntimeRoot, ownerLivenessObserver = defaultObserveOwnerLiveness
) => {
  assertAuthority(authority)
  validateIdentityInputs(operationId, authority.identities.graphSha256, authority.identities.bindingSha256)
  const paths = resolvePaths(authorizedPrivateRuntimeRoot)
  return withLock(paths, authority, ownerLivenessObserver, () => {
    const active = fs.existsSync(paths.recordPath) ? readResolved(paths.recordPath, authority) : null
    const decision = startOperation(active, operationId, authority)
    writeAtomic(paths, decision.operation, authority)
    return decision
  })
}

const reduceAndPersist = (
  current, event, authority, authorizedPrivateRuntimeRoot, ownerLivenessObserver = defaultObserveOwnerLiveness
) => {
  assertAuthority(authority)
  if (!event || event.operation_id !== current.operation_id) return current
  const paths = resolvePaths(authorizedPrivateRuntimeRoot)
  return withLock(paths, authority, ownerLivenessObserver, () => {
    if (!fs.existsSync(paths.recordPath)) fail('operation_store_record_missing')
    const persisted = readResolved(paths.recordPath, authority)
    if (persisted.operation_id !== current.operation_id || persisted.revision !== current.revision) fail('operation_store_revision_conflict')
    const result = reduce(persisted, event, authority)
    writeAtomic(paths, result, authority)
    return result
  })
}

const readOperation = (authority, authorizedPrivateRuntimeRoot, ownerLivenessObserver = defaultObserveOwnerLiveness) => {
  assertAuthority(authority)
  const paths = resolvePaths(authorizedPrivateRuntimeRoot)
  return withLock(paths, authority, ownerLivenessObserver, () => {
    if (!fs.existsSync(paths.recordPath)) fail('operation_store_record_missing')
    return readResolved(paths.recordPath, authority)
  })
}

module.exports = {
  LOCK_DISCARD_FILE,
  LOCK_FILE,
  LOCK_RECOVERY_FILE,
  LOCK_STALE_MS,
  MAX_OPERATION_RECORD_BYTES,
  RECORD_FILE,
  STORE_DIRECTORY,
  TEMP_FILE,
  readOperation,
  reduceAndPersist,
  startAndPersist
}
