'use strict'

const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

const { LauncherContractError, assertAuthority, readBoundedUtf8Text } = require('./launcher-supervisor-contract')
const { reduce, startOperation, validateIdentityInputs, validateSnapshot } = require('./launcher-supervisor-reducer')

const STORE_DIRECTORY = 'launcher-operation.v2'
const RECORD_FILE = 'launcher-operation.v2.json'
const LOCK_FILE = 'launcher-operation.v2.lock'
const TEMP_FILE = 'launcher-operation.v2.json.tmp'
const LOCK_RECOVERY_FILE = 'launcher-operation.v2.lock.recovering'
const LOCK_DISCARD_FILE = 'launcher-operation.v2.lock.discarding'
const SUPERVISOR_LEASE_FILE = 'launcher-supervisor.v2.lease'
const SUPERVISOR_LEASE_DISCARD_FILE = 'launcher-supervisor.v2.lease.discarding'
const MAX_OPERATION_RECORD_BYTES = 64 * 1024
const MAX_LOCK_RECORD_BYTES = 1024
const MAX_SUPERVISOR_LEASE_BYTES = 2048
const LOCK_STALE_MS = 30000
const LOCK_NONCE = /^ll_[a-z0-9]{32}$/u
const SUPERVISOR_LEASE_NONCE = /^sl_[a-z0-9]{64}$/u
const SHA256 = /^[a-f0-9]{64}$/u
const OWNER_LIVENESS = new Set(['alive', 'absent', 'pid_reused', 'access_denied', 'unknown'])
const ALLOWED_CONTENT = new Set([
  RECORD_FILE, LOCK_FILE, TEMP_FILE, LOCK_RECOVERY_FILE, LOCK_DISCARD_FILE,
  SUPERVISOR_LEASE_FILE, SUPERVISOR_LEASE_DISCARD_FILE
])
const supervisorLeaseState = new WeakMap()
const startCleanupState = new WeakMap()
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
  const supervisorLeasePath = path.resolve(root, SUPERVISOR_LEASE_FILE)
  const supervisorLeaseDiscardPath = path.resolve(root, SUPERVISOR_LEASE_DISCARD_FILE)
  if ([recordPath, lockPath, temporaryPath, recoveryLockPath, discardLockPath, supervisorLeasePath, supervisorLeaseDiscardPath]
    .some((candidate) => path.dirname(candidate).toLowerCase() !== root.toLowerCase())) fail('operation_store_root_invalid')
  const paths = {
    root, recordPath, lockPath, temporaryPath, recoveryLockPath, discardLockPath,
    supervisorLeasePath, supervisorLeaseDiscardPath
  }
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
      return { descriptor, ownerNonce, released: false }
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
  if (lock.released) return
  if (lock.descriptor !== undefined) {
    try {
      fs.closeSync(lock.descriptor)
      lock.descriptor = undefined
    } catch { fail('operation_store_lock_release_failed') }
  }
  try {
    if (readLockRecord(lockPath).owner_nonce !== lock.ownerNonce) fail('operation_store_lock_release_failed')
    fs.unlinkSync(lockPath)
    lock.released = true
  } catch { fail('operation_store_lock_release_failed') }
}

const withLock = (paths, authority, ownerLivenessObserver, action, onCommittedReleaseFailure = undefined) => {
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
  if (releaseError) {
    if (typeof onCommittedReleaseFailure === 'function') return onCommittedReleaseFailure(result, lock)
    throw releaseError
  }
  return result
}

const exactSupervisorLeaseRecord = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== 'binding_sha256,created_at_ms,graph_sha256,operation_id,owner_nonce,owner_pid,schema_version,supervisor_generation' ||
      value.schema_version !== 'launcher_supervisor_lease.v2' || typeof value.owner_nonce !== 'string' ||
      !SUPERVISOR_LEASE_NONCE.test(value.owner_nonce) || !Number.isSafeInteger(value.owner_pid) || value.owner_pid <= 0 ||
      !Number.isSafeInteger(value.created_at_ms) || value.created_at_ms < 0 ||
      typeof value.operation_id !== 'string' || !/^lop_[a-z0-9]{8,64}$/u.test(value.operation_id) ||
      !Number.isSafeInteger(value.supervisor_generation) || value.supervisor_generation < 1 ||
      !SHA256.test(value.graph_sha256) || !SHA256.test(value.binding_sha256)) {
    fail('supervisor_lease_unavailable')
  }
  return value
}

const readSupervisorLeaseText = (target) => {
  let stat
  try { stat = fs.lstatSync(target) } catch { fail('supervisor_lease_unavailable') }
  if (stat.isSymbolicLink() || !stat.isFile()) fail('supervisor_lease_unavailable')
  return readBoundedUtf8Text(
    target, MAX_SUPERVISOR_LEASE_BYTES, 'supervisor_lease_unavailable', 'supervisor_lease_unavailable'
  )
}

const parseSupervisorLeaseText = (text) => {
  try { return exactSupervisorLeaseRecord(JSON.parse(text)) } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('supervisor_lease_unavailable')
  }
}

const requireConfirmedLeaseOwnerAbsent = (observer, record) => {
  if (observeOwnerLiveness(observer, record) !== 'absent') fail('supervisor_lease_unavailable')
}

const supervisorLeaseProof = (record) => `lp_${crypto.createHash('sha256').update(Buffer.from([
  record.schema_version, record.operation_id, String(record.supervisor_generation), record.graph_sha256,
  record.binding_sha256, record.owner_nonce, String(record.owner_pid), String(record.created_at_ms)
].join('\n'), 'utf8')).digest('hex')}`

const preserveLeaseReplacement = (paths) => {
  try {
    if (!fs.existsSync(paths.supervisorLeasePath) && fs.existsSync(paths.supervisorLeaseDiscardPath)) {
      fs.renameSync(paths.supervisorLeaseDiscardPath, paths.supervisorLeasePath)
    }
  } catch {}
  fail('supervisor_lease_unavailable')
}

const removeAbandonedSupervisorLease = (paths, ownerLivenessObserver) => {
  if (fs.existsSync(paths.supervisorLeaseDiscardPath)) fail('supervisor_lease_unavailable')
  if (!fs.existsSync(paths.supervisorLeasePath)) return
  const observedText = readSupervisorLeaseText(paths.supervisorLeasePath)
  const observed = parseSupervisorLeaseText(observedText)
  requireConfirmedLeaseOwnerAbsent(ownerLivenessObserver, observed)
  if (readSupervisorLeaseText(paths.supervisorLeasePath) !== observedText) fail('supervisor_lease_unavailable')
  requireConfirmedLeaseOwnerAbsent(ownerLivenessObserver, observed)
  try { fs.renameSync(paths.supervisorLeasePath, paths.supervisorLeaseDiscardPath) } catch { fail('supervisor_lease_unavailable') }
  let claimedText
  try { claimedText = readSupervisorLeaseText(paths.supervisorLeaseDiscardPath) } catch { preserveLeaseReplacement(paths) }
  if (claimedText !== observedText || fs.existsSync(paths.supervisorLeasePath)) preserveLeaseReplacement(paths)
  if (observeOwnerLiveness(ownerLivenessObserver, observed) !== 'absent') preserveLeaseReplacement(paths)
  try {
    if (readSupervisorLeaseText(paths.supervisorLeaseDiscardPath) !== observedText || fs.existsSync(paths.supervisorLeasePath)) {
      preserveLeaseReplacement(paths)
    }
    fs.unlinkSync(paths.supervisorLeaseDiscardPath)
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    preserveLeaseReplacement(paths)
  }
}

const requireSupervisorLeaseState = (lease, authority) => {
  assertAuthority(authority)
  const state = supervisorLeaseState.get(lease)
  if (!state || state.released || state.authority !== authority || state.descriptor === undefined) fail('supervisor_lease_invalid')
  let currentText
  try { currentText = readSupervisorLeaseText(state.paths.supervisorLeasePath) } catch { fail('supervisor_lease_invalid') }
  if (currentText !== state.recordText) fail('supervisor_lease_invalid')
  return state
}

const createSupervisorLeaseFile = ({ paths, operationId, supervisorGeneration, authority }) => {
  let descriptor
  let created = false
  const record = {
    schema_version: 'launcher_supervisor_lease.v2', operation_id: operationId,
    supervisor_generation: supervisorGeneration, graph_sha256: authority.identities.graphSha256,
    binding_sha256: authority.identities.bindingSha256,
    owner_nonce: `sl_${crypto.randomBytes(32).toString('hex')}`,
    owner_pid: process.pid, created_at_ms: Date.now()
  }
  const recordText = `${JSON.stringify(record)}\n`
  try {
    descriptor = fs.openSync(paths.supervisorLeasePath, 'wx', 0o600)
    created = true
    fs.writeFileSync(descriptor, Buffer.from(recordText, 'utf8'))
    fs.fsyncSync(descriptor)
    hardenPrivate(paths.supervisorLeasePath, false)
    const lease = Object.freeze(Object.create(null))
    supervisorLeaseState.set(lease, {
      authority, descriptor, paths, record, recordText, released: false,
      leaseProof: supervisorLeaseProof(record)
    })
    return lease
  } catch (error) {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor) } catch {}
    }
    if (created) {
      try {
        if (fs.existsSync(paths.supervisorLeasePath) && readSupervisorLeaseText(paths.supervisorLeasePath) === recordText) {
          fs.unlinkSync(paths.supervisorLeasePath)
        }
      } catch {}
    }
    if (error instanceof LauncherContractError) throw error
    if (error?.code === 'EEXIST') fail('supervisor_lease_unavailable')
    fail('supervisor_lease_failed')
  }
}

const discardUnpublishedSupervisorLease = (lease, authority) => {
  const state = requireSupervisorLeaseState(lease, authority)
  let failed = false
  try { fs.closeSync(state.descriptor) } catch { failed = true }
  state.descriptor = undefined
  try {
    if (readSupervisorLeaseText(state.paths.supervisorLeasePath) !== state.recordText) failed = true
    else fs.unlinkSync(state.paths.supervisorLeasePath)
  } catch { failed = true }
  state.released = true
  if (failed) fail('supervisor_lease_release_failed')
}

const acquireSupervisorLease = ({
  operationId, supervisorGeneration, authority, authorizedPrivateRuntimeRoot,
  ownerLivenessObserver = defaultObserveOwnerLiveness
}) => {
  assertAuthority(authority)
  validateIdentityInputs(operationId, authority.identities.graphSha256, authority.identities.bindingSha256)
  if (!Number.isSafeInteger(supervisorGeneration) || supervisorGeneration < 1) fail('supervisor_lease_generation_invalid')
  const paths = resolvePaths(authorizedPrivateRuntimeRoot)
  return withLock(paths, authority, ownerLivenessObserver, () => {
    if (!fs.existsSync(paths.recordPath)) fail('operation_store_record_missing')
    const operation = readResolved(paths.recordPath, authority)
    if (operation.operation_id !== operationId || operation.supervisor_generation !== supervisorGeneration) fail('supervisor_lease_operation_mismatch')
    removeAbandonedSupervisorLease(paths, ownerLivenessObserver)
    return createSupervisorLeaseFile({ paths, operationId, supervisorGeneration, authority })
  })
}

const getSupervisorLeaseBinding = (lease, authority) => {
  const state = requireSupervisorLeaseState(lease, authority)
  return Object.freeze({
    operation_id: state.record.operation_id,
    supervisor_generation: state.record.supervisor_generation,
    authority_lease_proof: state.leaseProof
  })
}

const releaseSupervisorLease = (lease, authority, ownerLivenessObserver = defaultObserveOwnerLiveness) => {
  const state = requireSupervisorLeaseState(lease, authority)
  return withLock(state.paths, authority, ownerLivenessObserver, () => {
    if (readSupervisorLeaseText(state.paths.supervisorLeasePath) !== state.recordText) fail('supervisor_lease_release_failed')
    try { fs.closeSync(state.descriptor) } catch { fail('supervisor_lease_release_failed') }
    state.descriptor = undefined
    try { fs.unlinkSync(state.paths.supervisorLeasePath) } catch { fail('supervisor_lease_release_failed') }
    state.released = true
  })
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
  if (pending.operation_id !== current.operation_id || pending.supervisor_generation !== current.supervisor_generation ||
      pending.graph_sha256 !== current.graph_sha256 || pending.binding_sha256 !== current.binding_sha256 ||
      pending.private_plan_sha256 !== current.private_plan_sha256 ||
      pending.worker_executable_class !== current.worker_executable_class ||
      pending.worker_executable_sha256 !== current.worker_executable_sha256) {
    fail('operation_store_recovery_invalid')
  }
  if (pending.revision === current.revision + 1) {
    try {
      hardenPrivate(paths.temporaryPath, false)
      fs.renameSync(paths.temporaryPath, paths.recordPath)
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
    if (fs.existsSync(paths.recordPath)) {
      const recordStat = rejectReparse(paths.recordPath)
      if (!recordStat.isFile()) fail('operation_store_foreign_content')
    }
    // The fully validated, private temporary file carries its protection across the atomic rename.
    // Nothing after this commit point may fail and make the caller misclassify a published record.
    fs.renameSync(paths.temporaryPath, paths.recordPath)
  } catch (error) {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor) } catch {}
    }
    try { if (created && fs.existsSync(paths.temporaryPath)) fs.unlinkSync(paths.temporaryPath) } catch {}
    if (error instanceof LauncherContractError) throw error
    fail('operation_store_write_failed')
  }
}

const decorateStartCleanup = (result, cleanupClass, state) => {
  const decorated = { ...result, operation_lock_cleanup_class: cleanupClass }
  startCleanupState.set(decorated, state)
  return decorated
}

const retryStartCleanup = (started, authority) => {
  assertAuthority(authority)
  const state = startCleanupState.get(started)
  if (!state || state.authority !== authority) fail('operation_store_cleanup_invalid')
  if (state.cleanupClass === 'clear') return 'clear'
  if (state.cleanupClass !== 'operation_lock_release_failed') fail('operation_store_cleanup_invalid')
  try { fs.lstatSync(state.lockPath) } catch (error) {
    if (error?.code !== 'ENOENT') return 'operation_lock_release_failed'
    state.cleanupClass = 'clear'
    return 'clear'
  }
  try { releaseLock(state.lock, state.lockPath) } catch { return 'operation_lock_release_failed' }
  state.cleanupClass = 'clear'
  return 'clear'
}

const startAndPersist = (
  operationId, configIdentity, planIdentity, authority, authorizedPrivateRuntimeRoot, ownerLivenessObserver = defaultObserveOwnerLiveness
) => {
  assertAuthority(authority)
  validateIdentityInputs(operationId, authority.identities.graphSha256, authority.identities.bindingSha256)
  const paths = resolvePaths(authorizedPrivateRuntimeRoot)
  const result = withLock(paths, authority, ownerLivenessObserver, () => {
    removeAbandonedSupervisorLease(paths, ownerLivenessObserver)
    const active = fs.existsSync(paths.recordPath) ? readResolved(paths.recordPath, authority) : null
    const generation = active === null ? 1 : active.supervisor_generation + 1
    if (!Number.isSafeInteger(generation) || generation > Number.MAX_SAFE_INTEGER) fail('operation_store_generation_exhausted')
    const decision = startOperation(active, operationId, authority, configIdentity, planIdentity, generation)
    const supervisorLease = createSupervisorLeaseFile({
      paths, operationId: decision.operation.operation_id,
      supervisorGeneration: decision.operation.supervisor_generation, authority
    })
    try { writeAtomic(paths, decision.operation, authority) } catch (error) {
      try { discardUnpublishedSupervisorLease(supervisorLease, authority) } catch {
        const primaryCode = error instanceof LauncherContractError ? error.code : 'operation_store_write_failed'
        throw new LauncherContractError(primaryCode, 'supervisor_lease_release_failed')
      }
      throw error
    }
    return { ...decision, supervisorLease }
  }, (committed, lock) => decorateStartCleanup(committed, 'operation_lock_release_failed', {
    authority, cleanupClass: 'operation_lock_release_failed', lock, lockPath: paths.lockPath
  }))
  if (startCleanupState.has(result)) return result
  return decorateStartCleanup(result, 'clear', { authority, cleanupClass: 'clear' })
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
  SUPERVISOR_LEASE_DISCARD_FILE,
  SUPERVISOR_LEASE_FILE,
  TEMP_FILE,
  acquireSupervisorLease,
  getSupervisorLeaseBinding,
  readOperation,
  reduceAndPersist,
  releaseSupervisorLease,
  retryStartCleanup,
  startAndPersist
}
